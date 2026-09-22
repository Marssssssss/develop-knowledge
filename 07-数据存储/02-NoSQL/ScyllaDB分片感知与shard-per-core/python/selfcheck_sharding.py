"""ScyllaDB shard-per-core 映射自检（期望值全部手算，见注释）。"""

import sys

from sharding import (
    FixedShardPartitioner,
    StaticSharder,
    Token,
    init_zero_based_shard_start,
    sharding_algorithm_name,
    shard_of,
    zero_based_shard_of,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAILED: " + label
    PASS += 1


def eq(a, b, label):
    ok(a == b, "%s (got %r want %r)" % (label, a, b))


# ---- 1. unbias：把 int64 token 环挪到 [0, 2^64) ----
eq(Token.key(0).unbias(), 1 << 63, "unbias(0) = 2^63")
eq(Token.key(-(1 << 63)).unbias(), 0, "unbias(INT64_MIN) = 0")
eq(Token.key((1 << 63) - 1).unbias(), (1 << 64) - 1, "unbias(INT64_MAX) = 2^64-1")
# bias 是 unbias 的逆，带回绕
eq(Token.bias(1 << 63).data, 0, "bias(2^63) = 0")
eq(Token.bias(0).data, -(1 << 63), "bias(0) = INT64_MIN（有符号回绕）")

# ---- 2. zero_based_shard_of：floor(0.token * shards) ----
eq(zero_based_shard_of(0, 4, 0), 0, "token 0 -> shard 0（区间左端）")
eq(zero_based_shard_of((1 << 64) - 1, 4, 0), 3, "token 2^64-1 -> shard 3（右端贴合上界）")
# 2^62 = 0.25 * 2^64，正好是 shard 1 的起点
eq(zero_based_shard_of(1 << 62, 4, 0), 1, "token 2^62 -> shard 1")
eq(zero_based_shard_of((1 << 62) - 1, 4, 0), 0, "token 2^62-1 -> shard 0（差一格就翻）")
eq(zero_based_shard_of(1 << 63, 4, 0), 2, "token 2^63 -> shard 2")
eq(zero_based_shard_of((1 << 63) + (1 << 62), 4, 0), 3, "token 1.5*2^63 -> shard 3")

# sharding_ignore_msb_bits：左移丢弃高位，等价于只看低位小数
eq(zero_based_shard_of(1 << 63, 4, 1), 0, "msb=1 时 2^63 左移后溢出为 0 -> shard 0")
eq(zero_based_shard_of(1 << 62, 4, 1), 2, "msb=1 时 2^62 左移成 2^63 -> shard 2")
eq(zero_based_shard_of(1 << 63, 1, 3), 0, "任意 msb，shards=1 恒为 0")

# ---- 3. shard_of 的三态分派 ----
eq(shard_of(8, 0, Token.minimum()), 0, "minimum token 硬编码落到 shard 0")
eq(shard_of(8, 0, Token.maximum()), 7, "maximum token 落到最后一个 shard")
eq(shard_of(1, 0, Token.maximum()), 0, "单 shard 下 maximum 仍是 0")

# ---- 4. init_zero_based_shard_start（shards=3，除法不整除要 +1 修正）----
starts3 = init_zero_based_shard_start(3, 0)
eq(starts3[0], 0, "shards=3 时 ret[0] = 0")
# 手算：floor(2^64/3) = 6148914691236517205，乘 3 得 2^64-1 仍归 shard 0，故 +1
eq(starts3[1], 6148914691236517206, "shards=3 时 ret[1] 需 +1 修正")
eq(starts3[2], 12297829382473034411, "shards=3 时 ret[2] 需 +1 修正")
for s in range(3):
    eq(zero_based_shard_of(starts3[s], 3, 0), s, "ret[%d] 确实属于 shard %d" % (s, s))
    if s > 0:
        ok(zero_based_shard_of(starts3[s] - 1, 3, 0) != s, "ret[%d]-1 不再属于 shard %d" % (s, s))
eq(init_zero_based_shard_start(1, 0), [0], "shards=1 特判返回 [0]")
eq(init_zero_based_shard_start(4, 0), [0, 1 << 62, 1 << 63, 3 << 62], "shards=4 整除无需修正")

# msb 下 start 也要右移
eq(init_zero_based_shard_start(4, 1)[1], (1 << 62) >> 1, "msb=1 时 start 同步右移")

# ---- 5. static_sharder 的行为 ----
sd = StaticSharder(4)
eq(sd.shard_count, 4, "shard_count 取构造参数")
# token 0 的 unbias 是 2^63，(2^63 * 4) >> 64 = 2
eq(sd.shard_for_reads(Token.key(0)), 2, "token 0（unbias 后是环的正中间）-> shard 2")
# 与之成对的负 token：unbias 落在环的左半 -> shard 更小
eq(sd.shard_for_reads(Token.key(-1)), 1, "token -1 落在环中点偏左 -> shard 1")

# 迁移期写入要同时落在两个 shard
sets = sd.shard_for_writes(Token.key(0), migrating=True)
eq(len(sets), 2, "tablet 迁移期 shard_for_writes 返回两个 shard")
ok(2 in sets, "其中一个仍是原 shard 2")
ok(len(set(sets)) == 2, "两个 shard 不重复")
eq(sd.shard_for_writes(Token.key(0)), [2], "非迁移期只写一个 shard")

# next_shard：从 shard 2 出发，下一个是 3，且返回它的边界 token
ns = sd.next_shard(Token.key(0))
ok(ns is not None, "next_shard 有结果")
eq(ns[0], 3, "shard 2 之后是 shard 3")
eq(sd.shard_for_reads(ns[1]), 3, "返回的 token 确实属于 shard 3")
# 回绕：落在最后一个 shard 的 token，next 要么回绕到 0 要么没有
tail = Token.bias(sd.shard_start[3])
eq(sd.shard_for_reads(tail), 3, "末 shard 起点属于末 shard")
ns2 = sd.next_shard(tail)
ok(ns2 is None or ns2[0] == 0, "从末 shard 出发要么回绕到 shard 0 要么没有下一个")

# token_for_next_shard 溢出
ok(sd.token_for_next_shard(Token.maximum(), 1).is_maximum(), "maximum token 上再找下一个是 maximum")

# ---- 6. fixed_shard_partitioner：token 里直接编码 shard ----
eq(FixedShardPartitioner.shard_bits, 16, "shard 占 16 位")
eq(FixedShardPartitioner.shard_shift, 48, "shard_shift = 64-16")
eq(FixedShardPartitioner.max_shard, 32767, "max_shard 是 int16 上界（顶层位留给符号）")
t = FixedShardPartitioner.token_for_shard(3, 0xABCDEF)
eq(FixedShardPartitioner.shard_of(t), 3, "token 高 16 位就是 shard")
eq(t.raw() & FixedShardPartitioner.hash_mask, 0xABCDEF, "低 48 位是 hash")
# clamp：编码值超过实际 shard 数会被压到最后一个
eq(FixedShardPartitioner.shard_of_clamped(t, 2), 1, "shard 3 在只有 2 个 shard 时被 clamp 到 1")
eq(FixedShardPartitioner.shard_of_clamped(FixedShardPartitioner.token_for_shard(1, 7), 4), 1, "未越界不 clamp")

# ---- 7. 算法名 ----
eq(sharding_algorithm_name(), "biased-token-round-robin", "官方自报的算法名")

print("PASS=%d" % PASS)
sys.exit(0)
