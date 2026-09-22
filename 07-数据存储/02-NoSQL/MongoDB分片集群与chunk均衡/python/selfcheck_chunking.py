"""MongoDB chunk 均衡模型自检（期望值全部手算，见注释）。"""

import sys

from chunking import (
    DEFAULT_CHUNK_MB,
    Chunk,
    Migration,
    balance_round,
    max_parallel_migrations,
    migration_threshold,
    needs_balancing,
    split_plan,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAILED: " + label
    PASS += 1


def eq(a, b, label):
    ok(a == b, "%s (got %r want %r)" % (label, a, b))


# ---- 1. 阈值口径：3 × range size ----
eq(DEFAULT_CHUNK_MB, 128, "默认 range size 是 128MB")
eq(migration_threshold(), 384, "默认阈值 = 3 × 128 = 384MB")
eq(migration_threshold(64), 192, "range size 64MB 时阈值 192MB")
# 官方原文：差值「至少 384MB」才迁移
eq(needs_balancing([1000, 616]), True, "差 384MB 恰好触发（>=）")
eq(needs_balancing([1000, 617]), False, "差 383MB 不触发（严格小于阈值才叫均衡）")
eq(needs_balancing([1000, 1000]), False, "完全相等当然均衡")
eq(needs_balancing([500]), False, "只有一个 shard 时无从均衡")
# 三个 shard 时看的是最大与最小之差，不是相邻差
eq(needs_balancing([1000, 900, 616]), True, "多 shard 看 max-min")
eq(needs_balancing([1000, 900, 900]), False, "max-min = 100，不触发")

# ---- 2. 并发迁移数 = floor(n/2) ----
eq(max_parallel_migrations(2), 1, "2 shard -> 1 个并发迁移")
eq(max_parallel_migrations(3), 1, "3 shard -> floor(3/2) = 1")
eq(max_parallel_migrations(4), 2, "4 shard -> 2")
eq(max_parallel_migrations(5), 2, "5 shard -> 2（向下取整）")
eq(max_parallel_migrations(1), 0, "1 shard -> 0（没有目标）")

# ---- 3. 自动分裂是二分 ----
eq(split_plan(64), (0, 1, 64.0), "64MB 不超过 128MB，不分裂")
eq(split_plan(128), (0, 1, 128.0), "正好等于阈值也不分裂（严格大于才分裂）")
eq(split_plan(129), (1, 2, 64.5), "129MB 二分一次，每块 64.5MB")
# 900MB：900/1=900>128 -> 450>128 -> 225>128 -> 112.5<=128，共 3 次，8 块
eq(split_plan(900), (3, 8, 112.5), "900MB 二分三次 -> 8 块 × 112.5MB")
eq(split_plan(256), (1, 2, 128.0), "256MB 二分一次正好两块 128MB")
# 块数永远是 2 的幂
for size in (200, 500, 1000, 5000):
    _, parts, each = split_plan(size)
    ok(parts & (parts - 1) == 0, "块数 %d 是 2 的幂（size=%d）" % (parts, size))
    ok(each <= 128.0, "分裂后每块 %.1fMB 不超过 128MB（size=%d）" % (each, size))

# ---- 4. jumbo chunk：超限且不可分裂 ----
single = Chunk("a", "b", 200, distinct_keys=1)
eq(single.divisible, False, "只含一个唯一 shard key 值的 chunk 不可分裂")
eq(single.evaluate(), True, "200MB 的单值 chunk 被标为 jumbo")
multi = Chunk("a", "b", 200, distinct_keys=5)
eq(multi.evaluate(), False, "200MB 但含 5 个值 -> 可分裂，不算 jumbo")
small = Chunk("a", "b", 100, distinct_keys=1)
eq(small.evaluate(), False, "单值但不超限，也不算 jumbo")
# 单调 shard key 的典型场景：一天一个值，全落一个 chunk
eq(Chunk("2026-09-22", "2026-09-23", 500, 1).evaluate(), True, "按天做 shard key 会造出 jumbo")

# ---- 5. 迁移状态机（7 步）----
eq(len(Migration.STEPS), 7, "官方描述的 range 迁移是 7 步")
m = Migration("c1", async_delete=True)
for _ in range(6):
    m.advance()
eq(m.done, True, "异步模式下第 6 步后即视为完成")
eq(m.deleted, False, "异步模式下删除还没做")
m.advance()
eq(m.deleted, True, "再推一步才真正删掉源端副本")
# 成对构造：同步模式（_waitForDelete）下完成即已删除
m2 = Migration("c2", async_delete=False)
for _ in range(7):
    m2.advance()
eq(m2.done, True, "同步模式下也要走完 7 步")
eq(m2.deleted, True, "同步模式下完成时删除已完成")

# ---- 6. 均衡过程 ----
rounds, moves = balance_round([1000, 600, 600])
ok(moves > 0, "初始不均衡，需要迁移")
final = rounds[-1] if rounds else None
ok(final is not None, "至少产生一轮")
ok(not needs_balancing(final), "均衡轮结束后已落在阈值内")
# 阈值是 384，最后一步停下时差值必然 < 384
ok(max(final) - min(final) < 384, "终止时 max-min < 384MB")
# 已经均衡的集合不需要迁移
_, moves0 = balance_round([600, 600, 600])
eq(moves0, 0, "本来就均衡则不产生迁移")

print("PASS=%d" % PASS)
sys.exit(0)
