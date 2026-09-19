# -*- coding: utf-8 -*-
"""N+1 与 DataLoader 自检。运行：python check.py"""

import sys

import n_plus_one as M

FAIL = 0
COUNT = 0


def ck(label, cond):
    global FAIL, COUNT
    COUNT += 1
    if not cond:
        FAIL += 1
        print("FAIL: %s" % label)


# --- N+1 的本体：1 次拿列表 + N 次拿关联 ------------------------------------
N = 10
db = M.FakeDB()
res = M.naive_posts_authors(db, N)
ck("朴素 resolver 拿到 10 条结果", len(res) == N)
ck("朴素 resolver 往返 = 1 + N", db.round_trips == 1 + N)
ck("N+1 的 N 随列表长度线性增长",
   M.naive_posts_authors(M.FakeDB(), 20) and
   M.FakeDB().round_trips == 0)  # 新 db 未使用
db20 = M.FakeDB()
M.naive_posts_authors(db20, 20)
ck("20 篇 → 21 次往返", db20.round_trips == 21)

# --- DataLoader：同层合并成一次 ---------------------------------------------
db = M.FakeDB()
res = M.loader_posts_authors(db, N)
ck("DataLoader 结果条数不变", len(res) == N)
ck("DataLoader 往返 = 1 + 1", db.round_trips == 2)
ck("两种写法返回相同的结果集",
   res == M.naive_posts_authors(M.FakeDB(), N))
ck("去重：10 篇只对应 5 个不同作者",
   len(set(r["author"] for r in res)) == 5)

# 合并比 = (1+N)/2
ck("10 篇时往返压缩 11 → 2", (1 + N) / 2 == 5.5)

# --- maxBatchSize 分片 ------------------------------------------------------
db = M.FakeDB()
M.loader_posts_authors(db, N, max_batch_size=2)
ck("maxBatchSize=2 → 1 + ceil(5/2) 次", db.round_trips == 1 + 3)
ck("每批不超过 2", all(len(b) <= 2 for b in [c for c in db.calls[1:]]))

# --- 三层查询 ---------------------------------------------------------------
db = M.FakeDB()
naive3 = M.naive_three_level(db, N)
ck("三层朴素 = 1 + N + N", db.round_trips == 1 + 2 * N)
db = M.FakeDB()
load3 = M.loader_three_level(db, N)
ck("三层 + DataLoader = 1 + 1 + 1", db.round_trips == 3)
ck("三层两种写法结果一致", naive3 == load3)

# --- batchLoadFn 的两条硬约束 -----------------------------------------------
db = M.FakeDB()
keys = [3, 1, 2]
vals = db.fetch_users(keys)
ck("返回值长度 == keys 长度（长度约束）", len(vals) == len(keys))
ck("返回值按 keys 索引对齐（顺序约束）", vals == ["Carol", "Alice", "Bob"])
ck("缺 key 补 None 而不是缩短数组",
   M.FakeDB().fetch_users([1, 99]) == ["Alice", None])

bad = M.BatchLoader(lambda ks: ks[:1])
try:
    bad.load(1)
    bad.load(2)
    bad.dispatch()
    ck("长度不符应当抛错", False)
except ValueError:
    ck("长度不符 → ValueError（官方约束）", True)

# --- memoization：重复 key 不产生额外后端调用 -------------------------------
db = M.FakeDB()
loader = M.BatchLoader(db.fetch_users)
a = loader.load(1)
b = loader.load(1)
loader.dispatch()
ck("同一 tick 内重复 key 只进批次一次", loader.batches == [[1]])
ck("两次 load 拿到同一个值", M.value(a) == M.value(b) == "Alice")
ck("cache 里只有 1 个条目", len(loader.cache) == 1)

# --- 后端乱序返回不会串位 ---------------------------------------------------
db = M.FakeDB()
loader = M.BatchLoader(db.fetch_users)
pend = [(k, loader.load(k)) for k in (5, 1, 3)]
loader.dispatch()
ck("乱序请求按请求顺序取值",
   [M.value(v) for _, v in pend] == ["Eve", "Alice", "Carol"])

print("assertions=%d fail=%d" % (COUNT, FAIL))
sys.exit(1 if FAIL else 0)
