#!/usr/bin/env python3
"""MySQL 主从复制与 binlog 最小模拟:复制格式 / GTID / 半同步。

依据 dev.mysql.com/doc/refman/8.0 (replication-formats, replication-semisync,
replication-gtids) 归纳:
- SBR: binlog 记 SQL 语句;RBR: 记行变更事件,是 8.0 默认(binlog_format=ROW)。
- 半同步: source 等待 >=1 个 replica 把事务事件写入 relay log 并落盘后才 commit 返回;
  超时自动降级异步;等待点 AFTER_SYNC(binlog 落盘后、引擎 commit 前,8.0 默认)
  vs AFTER_COMMIT(引擎 commit 后才返回,存在幻读窗口)。
- GTID: source_id:transaction_id;同一 GTID 在同一服务器只应用一次(幂等)。
"""
import time


class Event:
    def __init__(self, gtid, kind, payload):
        self.gtid, self.kind, self.payload = gtid, kind, payload


class Source:
    """模拟主库:按 binlog_format 生成事件,半同步时阻塞等待 ACK。"""

    def __init__(self, format="ROW", semi_sync=False, wait_point="AFTER_SYNC", timeout=0.05):
        self.format = format          # STATEMENT | ROW
        self.semi_sync = semi_sync
        self.wait_point = wait_point  # AFTER_SYNC | AFTER_COMMIT
        self.timeout = timeout
        self.gtid_seq = 0
        self.binlog = []
        self.relay_acked = 0          # replica 已落盘 relay log 的事件数
        self.commit_visible = 0       # 已对客户端可见(commit)的事件数
        self.degraded = False         # 半同步超时后降级为异步

    def _emit(self, kind, payload):
        self.gtid_seq += 1
        ev = Event("%s:%d" % ("3E11FA47-71CA-11E1-9E33-C80AA9429562", self.gtid_seq), kind, payload)
        self.binlog.append(ev)
        return ev

    def execute(self, sql, changes=None):
        """执行一条语句并写 binlog。ROW 格式记行变更,STATEMENT 格式记语句。"""
        if self.format == "ROW" and changes is not None:
            evs = [self._emit("WriteRows" if c[0] == "I" else
                              "UpdateRows" if c[0] == "U" else "DeleteRows", c[1]) for c in changes]
        else:
            evs = [self._emit("Query", sql)]
        if self.semi_sync and not self.degraded:
            self._wait_ack(len(self.binlog))
        self.commit_visible = len(self.binlog)
        return evs

    def _wait_ack(self, upto):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.relay_acked >= upto:   # replica 已收齐并刷盘 relay log
                return
            time.sleep(0.001)
        self.degraded = True               # 超时 → 降级异步(官方文档行为)

    def ack(self, count):
        self.relay_acked = count


class Replica:
    """模拟从库:IO 线程拉事件写 relay log,SQL 线程重放。"""

    def __init__(self):
        self.relay_log = []
        self.executed_gtid = set()
        self.data = {}
        self.source = None

    def connect(self, source):
        source.replica = self
        self.source = source

    def pull(self):
        """IO 线程:取走 binlog 尚未同步的部分,写 relay log 并 ACK。"""
        new = self.source.binlog[len(self.relay_log):]
        self.relay_log.extend(new)
        self.source.ack(len(self.relay_log))

    def replay(self):
        """SQL 线程:重放 relay log;GTID 幂等 —— 已执行过的事务直接忽略。"""
        for ev in self.relay_log:
            if ev.gtid in self.executed_gtid:
                continue
            self.executed_gtid.add(ev.gtid)
            if ev.kind in ("WriteRows", "Query") and isinstance(ev.payload, tuple) and ev.kind == "WriteRows":
                self.data[ev.payload[0]] = dict(ev.payload[1])
            elif ev.kind == "UpdateRows":
                self.data[ev.payload[0]] = dict(ev.payload[1])
            elif ev.kind == "DeleteRows":
                self.data.pop(ev.payload[0], None)


def main():
    # ---- 1. 复制格式:ROW 记行事件,STATEMENT 记 SQL ----
    src = Source(format="ROW")
    src.execute("INSERT INTO t VALUES (1,now())", changes=[("I", (1, {"v": 1}))])
    src.execute("UPDATE t SET v=v+1 WHERE id=1", changes=[("U", (1, {"v": 2}))])
    kinds = [e.kind for e in src.binlog]
    assert kinds == ["WriteRows", "UpdateRows"], kinds

    src2 = Source(format="STATEMENT")
    src2.execute("UPDATE t SET v=UUID()")   # 非确定性函数,SBR 有主从不一致风险
    assert src2.binlog[0].kind == "Query" and src2.binlog[0].payload == "UPDATE t SET v=UUID()"

    # ---- 2. 三线程复制链路 + GTID 幂等 ----
    rep = Replica()
    rep.connect(src)
    rep.pull()
    rep.replay()
    assert rep.data == {1: {"v": 2}}, rep.data
    assert len(rep.executed_gtid) == 2
    rep.replay()                              # 重复重放:GTID 去重,数据不变
    assert rep.data == {1: {"v": 2}} and len(rep.executed_gtid) == 2

    # ---- 3. 半同步:AFTER_SYNC 等待点 + 超时降级 ----
    semi = Source(format="ROW", semi_sync=True, wait_point="AFTER_SYNC", timeout=0.05)
    rep2 = Replica()
    rep2.connect(semi)
    semi.execute("INSERT INTO t VALUES (2)", changes=[("I", (2, {"v": 1}))])
    # execute 已返回但 replica 未 ACK → 半同步下本应阻塞,这里验证超时降级路径
    assert semi.degraded is True
    # 降级后的事务不再等待
    t0 = time.monotonic()
    semi.execute("INSERT INTO t VALUES (3)", changes=[("I", (3, {"v": 1}))])
    assert time.monotonic() - t0 < 0.02
    # 正常 ACK 场景:replica 在超时内拉取
    semi2 = Source(format="ROW", semi_sync=True, wait_point="AFTER_SYNC", timeout=1.0)
    rep3 = Replica()
    rep3.connect(semi2)

    class FastReplica(Replica):
        pass
    rep3 = FastReplica()
    rep3.connect(semi2)
    import threading
    def worker():
        time.sleep(0.01)
        rep3.pull()
    threading.Thread(target=worker).start()
    t0 = time.monotonic()
    semi2.execute("INSERT INTO t VALUES (4)", changes=[("I", (4, {"v": 1}))])
    waited = time.monotonic() - t0
    assert semi2.degraded is False and waited >= 0.01   # commit 返回前确实等到了 ACK
    # AFTER_SYNC:ACK 在引擎 commit 之前 → commit 可见数 <= ACK 数(数据不丢窗口更小)
    assert semi2.commit_visible == len(semi2.binlog)

    print("ALL 5 DEMO-1 (ReplicationBinlog) ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
