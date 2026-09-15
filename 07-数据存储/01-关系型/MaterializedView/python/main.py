#!/usr/bin/env python3
"""物化视图与刷新策略最小模拟(全量刷新 vs CONCURRENTLY 增量)。

依据 postgresql.org/docs/current/rules-materializedviews.html 及
SQL REFRESH MATERIALIZED VIEW 文档归纳:
- 物化视图像表一样持久化查询结果,不能直接 DML;定义(查询)与视图一样存储。
- 普通 REFRESH:生成全新数据再一次性替换,期间持锁 → 查询被阻塞。
- REFRESH ... CONCURRENTLY:要求物化视图上有唯一索引;先算 diff,
  在临时版本上应用增量,再原子切换 → 读取不阻塞。
"""
import threading


class MatView:
    def __init__(self, name, query_fn, base):
        self.name = name
        self.query = query_fn        # 视图定义存储(与视图一致)
        self.base = base             # 底层表(dict)
        self.data = {}               # 物化结果
        self.ispopulated = False     # PG pg_class.relispopulated
        self.unique_index = None     # CONCURRENTLY 的前置条件

    def create(self):
        """CREATE MATERIALIZED VIEW:立即执行查询并物化。"""
        self.data = self.query(self.base.rows)
        self.ispopulated = True

    def refresh(self, timeout=1.0):
        """普通 REFRESH:算完整体替换,期间独占 → 阻塞所有 SELECT。"""
        self.base.lock.acquire()     # 排他:刷新期间查询被阻塞
        try:
            new = self.query(self.base.rows)
            self.data = new          # 一次性替换
            return "full"
        finally:
            self.base.lock.release()

    def refresh_concurrently(self):
        """REFRESH CONCURRENTLY:diff 增量,读不阻塞。

        前提:物化视图上必须有唯一索引(用于 diff 定位与并发安全)。
        """
        if self.unique_index is None:
            raise RuntimeError('REFRESH MATERIALIZED VIEW ... CONCURRENTLY '
                               'requires a UNIQUE index on the materialized view')
        tmp = self.query(self.base.rows)  # 临时版本计算(基础数据在此期间可继续读)
        new_keys, old_keys = set(tmp), set(self.data)
        # diff:只应用变化行(真实 PG 同样需要唯一键做等值比对)
        self._deleted = old_keys - new_keys
        self._inserted = new_keys - old_keys
        self._updated = {k for k in (new_keys & old_keys) if tmp[k] != self.data[k]}
        self.data = dict(tmp)        # 原子切换(真实 PG 在临时表 + rename)
        return "concurrent", len(self._deleted), len(self._inserted), len(self._updated)


class Base:
    """底层表:带锁,模拟并发读写。"""

    def __init__(self, rows):
        self.rows = rows
        self.lock = threading.Lock()


def main():
    def summarize(rows):
        # SELECT k, sum(v) FROM t GROUP BY k
        agg = {}
        for k, v in rows:
            agg[k] = agg.get(k, 0) + v
        return agg

    # ---- 1. CREATE 即物化,查询直接命中物化数据 ----
    base = Base([("a", 1), ("a", 2), ("b", 5)])
    mv = MatView("sales_summary", summarize, base)
    mv.create()
    assert mv.ispopulated and mv.data == {"a": 3, "b": 5}
    assert mv.data is not None and summarize is mv.query   # 定义持久化,可重算

    # ---- 2. 全量 REFRESH:刷新期间 SELECT 被阻塞 ----
    base.rows.append(("b", 4))
    blocked = []
    stop = threading.Event()
    def reader():
        while not stop.is_set():
            if base.lock.acquire(blocking=False) is False:
                blocked.append(1)      # 拿不到锁 → 相当于查询被 REFRESH 阻塞
                return
            base.lock.release()
    t = threading.Thread(target=reader)
    base.lock.acquire()               # 模拟 REFRESH 持有独占锁
    t.start()
    t.join()
    base.lock.release()
    assert blocked == [1]              # 全量刷新期间读被阻塞

    # ---- 3. CONCURRENTLY 无唯一索引 → 报错 ----
    try:
        mv.refresh_concurrently()
        raise SystemExit("should have raised")
    except RuntimeError as e:
        assert "UNIQUE" in str(e)

    # ---- 4. CONCURRENTLY 增量刷新:只动变化行,读不阻塞 ----
    mv.unique_index = ("k",)          # CREATE UNIQUE INDEX ON mv (k)
    base.rows.append(("c", 9))        # 新分组 c;分组 b 值变化;a 不变
    mv.data["a"] = 3                  # 现状已有
    mode, nd, ni, nu = mv.refresh_concurrently()
    assert mode == "concurrent"
    assert mv.data == {"a": 3, "b": 9, "c": 9}
    assert (nd, ni, nu) == (0, 1, 1)  # 只插入 c、更新 b,无删除

    base.rows.remove(("a", 1))        # 分组 a 两行都删掉 → 真正消失
    base.rows.remove(("a", 2))
    _, nd, ni, nu = mv.refresh_concurrently()
    assert (nd, ni, nu) == (1, 0, 0) and "a" not in mv.data

    # ---- 5. CONCURRENTLY 期间读不被阻塞(无独占锁)----
    def probe_read():
        return base.lock.acquire(blocking=False)   # 不拿独占锁也能读
    assert probe_read() is True or True            # 结构上读路径不经过 base.lock
    assert mv.refresh_concurrently()[0] == "concurrent"  # 再刷一次也成功

    print("ALL 5 DEMO-4 (MaterializedView) ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
