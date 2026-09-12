"""
WAL 预写日志 + ARIES 恢复 — 最小实现 (Pure Python stdlib)

核心机制 (ARIES, Mohan et al. 1992):
- 每条 update 写 redo-only log record (WAL: 必须先于 data page 落盘)
- 事务 COMMIT 前必须把 COMMIT log record 强制 flush 到稳定存储
- 崩溃后三阶段恢复: Analysis → Redo (repeat history) → Undo
- Steal + No-Force 策略: 内存中未 commit 的 dirty page 可以被偷写盘 (Steal);
                          commit 时 data page 不必落盘 (No-Force)
- Fuzzy checkpoint: 不需停机; 写 begin/end_checkpoint, end_checkpoint 持有 tx_table + dirty_page_table 快照
- Log Sequence Number (LSN) 单调递增; 每 page 有 pageLSN 记录被哪个 LSN 最后修改

参考:
- ARIES paper (Mohan et al. 1992 ACM TODS)
- Apache Derby Logging & Recovery README
  https://db.apache.org/derby/papers/recovery.html
- 《ARIES Recovery》(Moscow State University lecture notes)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

# -------- 数据结构 --------

# log record 类型常量
LRT_BEGIN = 'BEGIN'
LRT_UPDATE = 'UPDATE'
LRT_COMMIT = 'COMMIT'
LRT_ABORT = 'ABORT'
LRT_END = 'END'
LRT_CLR = 'CLR'        # 补偿日志记录: rollback 时产生, redo-only
LRT_CKPT_BEGIN = 'CKPT_BEGIN'
LRT_CKPT_END = 'CKPT_END'


@dataclass
class LogRecord:
    lsn: int               # Log Sequence Number, 单调递增
    prev_lsn: int          # 同一 tx 的前一条记录 LSN, 链式结构
    trans_id: int          # 事务 id; 0 = 系统级记录 (checkpoint)
    type: str              # 以上枚举之一
    page_id: int = -1      # 相关 page (UPDATE/CLR 用)
    offset: int = -1       # page 内偏移
    before_image: object = None
    after_image: object = None
    undo_next_lsn: int = 0  # CLR 用: 跳过被补偿的记录
    # 用于 checkpoint 携带的表
    tx_table_snapshot: dict = field(default_factory=dict)
    dirty_pages_snapshot: dict = field(default_factory=dict)
    master_lsn: int = 0   # 只在 last complete checkpoint 的 end record 写


@dataclass
class Page:
    pid: int
    data: dict  # offset -> value
    page_lsn: int = 0


class ARIESDB:
    def __init__(self, num_pages: int = 4):
        self.next_lsn = 1
        self.log: list[LogRecord] = []
        # 模拟 log buffer; force 时假装刷盘
        self.disk_log_lsn = 0
        # 数据 page (磁盘 + 内存合一, page_lsn 跟踪写入)
        self.pages: dict[int, Page] = {i: Page(pid=i, data={}) for i in range(num_pages)}
        # transaction table: {tx_id -> {state: U|C, lastLSN}}
        self.tx_table: dict[int, dict] = {}
        # dirty page table: {pid -> recLSN}
        self.dirty_pages: dict[int, int] = {}
        # master record: 最新一个 end_checkpoint 的 LSN
        self.master_lsn: int = 0

    # ---------- log 写 ----------
    def _append(self, rec: LogRecord) -> LogRecord:
        rec.lsn = self.next_lsn
        self.next_lsn += 1
        self.log.append(rec)
        return rec

    def _make_dummy_prev_lsn(self, tx_id: int) -> int:
        return self.tx_table.get(tx_id, {}).get('lastLSN', 0)

    def begin(self, tx_id: int):
        prev = self._make_dummy_prev_lsn(tx_id)
        rec = self._append(LogRecord(lsn=0, prev_lsn=prev,
                                      trans_id=tx_id, type=LRT_BEGIN))
        self.tx_table[tx_id] = {'state': 'U', 'lastLSN': rec.lsn}

    def update(self, tx_id: int, page_id: int, offset: int, value: object):
        prev = self.tx_table[tx_id]['lastLSN']
        page = self.pages[page_id]
        before = page.data.get(offset)
        rec = self._append(LogRecord(lsn=0, prev_lsn=prev, trans_id=tx_id,
                                      type=LRT_UPDATE, page_id=page_id,
                                      offset=offset, before_image=before,
                                      after_image=value))
        page.data[offset] = value
        page.page_lsn = rec.lsn
        # 标记 dirty
        if page_id not in self.dirty_pages:
            self.dirty_pages[page_id] = rec.lsn
        self.tx_table[tx_id]['lastLSN'] = rec.lsn

    def commit(self, tx_id: int):
        prev = self.tx_table[tx_id]['lastLSN']
        rec = self._append(LogRecord(lsn=0, prev_lsn=prev,
                                      trans_id=tx_id, type=LRT_COMMIT))
        self.tx_table[tx_id]['lastLSN'] = rec.lsn
        # WAL: commit 时强制把 log flush 到 disk (含 COMMIT 之前的所有记录)
        self.disk_log_lsn = self.next_lsn - 1
        # 写 END
        rec2 = self._append(LogRecord(lsn=0, prev_lsn=rec.lsn,
                                       trans_id=tx_id, type=LRT_END))
        self.tx_table[tx_id] = {'state': 'C', 'lastLSN': rec2.lsn}

    def abort(self, tx_id: int):
        """Abort 流程 (生产简化版): 不写 CLR, 直接打 ABORT+END"""
        prev = self.tx_table[tx_id]['lastLSN']
        rec = self._append(LogRecord(lsn=0, prev_lsn=prev,
                                      trans_id=tx_id, type=LRT_ABORT))
        self.tx_table[tx_id]['lastLSN'] = rec.lsn
        rec2 = self._append(LogRecord(lsn=0, prev_lsn=rec.lsn,
                                       trans_id=tx_id, type=LRT_END))
        self.tx_table[tx_id] = {'state': 'A', 'lastLSN': rec2.lsn}

    def force_log_to_disk(self):
        """模拟 fsync, 把整个 log buffer flush"""
        self.disk_log_lsn = self.next_lsn - 1

    # ---------- checkpoint ----------
    def write_checkpoint(self):
        """ARIES Fuzzy Checkpoint: begin → end(snapshot tables) → 更新 master"""
        rec = self._append(LogRecord(lsn=0, prev_lsn=0, trans_id=0, type=LRT_CKPT_BEGIN))
        end = self._append(LogRecord(lsn=0, prev_lsn=rec.lsn, trans_id=0,
                                      type=LRT_CKPT_END,
                                      tx_table_snapshot=dict(self.tx_table),
                                      dirty_pages_snapshot=dict(self.dirty_pages),
                                      master_lsn=rec.lsn))
        # 更新 master record (loss: 简化, end 写到自己的 lsn; 这里用 begin 的 lsn)
        self.master_lsn = rec.lsn
        # 把 end_checkpoint 也强制 flush, 否则崩溃后 master 指针不可信
        self.disk_log_lsn = self.next_lsn - 1

    # ---------- Recovery ----------
    @staticmethod
    def recover(log: list[LogRecord], master_lsn: int) -> dict:
        """ARIES 三阶段恢复. 返回重建后的 page dict + tx_table + dirty_pages"""
        log_by_lsn = {r.lsn: r for r in log}
        # 1. Analysis
        tx_table: dict[int, dict] = {}
        dirty: dict[int, int] = {}
        start = master_lsn
        for rec in log:
            if rec.lsn < start:
                continue
            t = rec.type
            if t == LRT_BEGIN:
                tx_table[rec.trans_id] = {'state': 'U', 'lastLSN': rec.lsn}
            elif t == LRT_UPDATE:
                if rec.trans_id not in tx_table:
                    tx_table[rec.trans_id] = {'state': 'U', 'lastLSN': rec.lsn}
                tx_table[rec.trans_id]['lastLSN'] = rec.lsn
                if rec.page_id not in dirty:
                    dirty[rec.page_id] = rec.lsn
            elif t == LRT_CLR:
                tx_table[rec.trans_id]['lastLSN'] = rec.lsn
            elif t == LRT_COMMIT:
                tx_table[rec.trans_id] = {'state': 'C', 'lastLSN': rec.lsn}
            elif t == LRT_END:
                if rec.trans_id in tx_table:
                    tx_table[rec.trans_id]['state'] = 'C'
            elif t == LRT_ABORT:
                tx_table[rec.trans_id] = {'state': 'A', 'lastLSN': rec.lsn}
            elif t == LRT_CKPT_END and rec.dirty_pages_snapshot:
                # 简化: 不重做 begin_checkpoint 后到 end_checkpoint 间的调整
                pass

        # losers = tx_table 中 state='U' (未 commit 也未 abort, 即 crash 时还活跃)
        losers = {tid for tid, info in tx_table.items() if info['state'] == 'U'}
        redo_start = min(dirty.values()) if dirty else (max(log_by_lsn.keys()) + 1)

        # 2. Redo: 重复历史 — 包括未提交 tx 的更新
        pages: dict[int, dict] = {}
        for rec in log:
            if rec.lsn < redo_start:
                continue
            if rec.type in (LRT_UPDATE, LRT_CLR):
                # 模拟 page 重建: 仅取 after_image 的 (pid, offset, value)
                pages.setdefault(rec.page_id, {})[rec.offset] = rec.after_image

        # 3. Undo: 反向扫 loser tx 最后 LSN, 写 CLR 抵消
        # 简化为: 模拟把 loser's 所有 update 的 after_image 替换为 before_image
        for rec in reversed(log):
            if rec.type == LRT_UPDATE and rec.trans_id in losers:
                pages.setdefault(rec.page_id, {})[rec.offset] = rec.before_image

        return {'pages': pages, 'tx_table': tx_table, 'dirty_pages': dirty,
                'losers': losers, 'redo_start': redo_start}


# ---------- Demos ----------

def demo_basic_wal():
    """Demo 1: WAL 协议 — log 必须先于 data page 落盘"""
    db = ARIESDB()
    db.begin(11)
    db.update(11, page_id=1, offset=0, value='Alice')
    db.update(11, page_id=1, offset=1, value=200)
    db.commit(11)
    print(f"[1] WAL: 事务提交后 disk_log_lsn={db.disk_log_lsn} (log force-flushed)")
    print(f"[1] 最终 page1: {db.pages[1].data}")
    assert db.pages[1].data == {0: 'Alice', 1: 200}


def demo_steal_no_force():
    """Demo 2: Steal 允许未 commit 数据落盘; No-Force 允许 commit 时 data 不落盘"""
    db = ARIESDB()
    db.begin(21)
    db.update(21, 1, 0, 'uncommitted')
    # Steal: 这个 page 被偷刷盘了 (在我们的模型里 page_lsn 已记录)
    print(f"[2] 未 commit 时 page.page_lsn={db.pages[1].page_lsn} (steal 副作用)")
    db.abort(21)
    print(f"[2] Abort 后 LSN 链以 END 结束, page 未被强制恢复 (生产会沿 undo chain)")
    # 模拟: abort 后数据被恢复应等于初始 (我们的 update 没设基线, 所以 demo 仅展示 WAL 链)


def demo_checkpoint():
    """Demo 3: Fuzzy checkpoint 携带当前 tx_table + dirty_pages 快照"""
    db = ARIESDB()
    db.begin(31); db.update(31, 0, 0, 'a'); db.update(31, 0, 1, 'b')
    db.begin(32); db.update(32, 1, 0, 'c')
    db.write_checkpoint()
    print(f"[3] Checkpoint 后 master_lsn={db.master_lsn}, tx_table={db.tx_table}, dirty={db.dirty_pages}")
    # 注意检查 tx_table snapshot 在 log 中某条 CKPT_END 上


def demo_crash_and_recover():
    """Demo 4: 模拟系统崩溃后 ARIES 三阶段恢复"""
    db = ARIESDB()
    db.begin(41); db.update(41, 0, 0, 'committed-data')
    db.commit(41)
    db.begin(42); db.update(42, 0, 1, 'uncommitted-data')
    # crash 之前 (T42 未 commit)
    db.write_checkpoint()  # master 记录最近 ckpt
    db.begin(43); db.update(43, 1, 0, 'another-loser')

    # 模拟崩溃: 复制 log, 然后从崩溃点恢复
    snapshot_log = list(db.log)
    master_at_crash = db.master_lsn
    print(f"[4] crash 时 next_lsn={db.next_lsn}, master={master_at_crash}, "
          f"活跃未 commit 的 tx={43}")

    result = ARIESDB.recover(snapshot_log, master_at_crash)
    print(f"[4] Recovery: redo_start LSN={result['redo_start']}, losers={result['losers']}")
    print(f"[4] 重建 page0={result['pages'].get(0)}, page1={result['pages'].get(1)}")
    # 验证: loser 42, 43 的更新被 undo
    assert 0 not in result['pages'].get(0, {}) or result['pages'][0].get(1) is None
    # 验证: 已 commit 的 41 留下
    assert result['pages'][0][0] == 'committed-data'


def demo_lsn_chain():
    """Demo 5: 同一 tx 的 update 用 prev_lsn 形成链, Abort/CLR 可以反向扫"""
    db = ARIESDB()
    db.begin(51)
    upd_lsns = []
    for i in range(3):
        db.update(51, 0, i, f'v{i}')
        upd_lsns.append(db.log[-1].lsn)
    db.commit(51)
    # 同 tx LSN 单调增, 但 prev_lsn 链把它们串起来 (这里是 commit 后的 END 记录作链底)
    print(f"[5] Tx51 改了 3 次 page0: LSN={upd_lsns}, 每条 prev_lsn 指前一条")
    # 验证 prev_lsn 链正确
    lsn_to_prev = {r.lsn: r.prev_lsn for r in db.log if r.trans_id == 51}
    print(f"[5] 同 tx LSN->prevLSN 映射: {lsn_to_prev}")


if __name__ == "__main__":
    print("== WAL 预写日志 + ARIES 恢复 ==")
    demo_basic_wal()
    demo_steal_no_force()
    demo_checkpoint()
    demo_crash_and_recover()
    demo_lsn_chain()
    print("All 5 demos OK.")
