"""HBase 分裂策略自检（期望值全部手算，见注释）。"""

import sys

from regions import (
    DEFAULT_JITTER,
    DEFAULT_MAX_FILE_SIZE,
    DEFAULT_MEMSTORE_FLUSH,
    MB,
    REGION_SPLIT_LIMIT,
    ConstantSizeSplitPolicy,
    IncreasingToUpperBoundSplitPolicy,
    can_write_hfile,
    constant_size_threshold,
    hfile_default_version,
    jitter_rate,
    split_sequence,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAILED: " + label
    PASS += 1


def eq(a, b, label):
    ok(a == b, "%s (got %r want %r)" % (label, a, b))


# ---- 1. 默认值 ----
eq(DEFAULT_MAX_FILE_SIZE, 10737418240, "hbase.hregion.max.filesize 默认 10GB")
eq(DEFAULT_MEMSTORE_FLUSH, 128 * MB, "memstore flush 默认 128MB")
eq(DEFAULT_JITTER, 0.25, "max.filesize.jitter 默认 0.25")
eq(REGION_SPLIT_LIMIT, 1000, "regionSplitLimit 默认 1000")

# ---- 2. jitterRate = (random - 0.5) * 0.25 -> [-0.125, +0.125] ----
eq(jitter_rate(0.0), -0.125, "random=0 -> -0.125（最小抖动）")
eq(jitter_rate(1.0), 0.125, "random=1 -> +0.125（最大抖动）")
eq(jitter_rate(0.5), 0.0, "random=0.5 -> 0（不抖）")
# 抖动范围确实是对称的
ok(abs(jitter_rate(0.0)) == abs(jitter_rate(1.0)), "抖动上下界对称")

# ---- 3. 抖动后的实际阈值 ----
eq(constant_size_threshold(DEFAULT_MAX_FILE_SIZE, 0.5), DEFAULT_MAX_FILE_SIZE,
   "不抖时阈值就是 10GB")
# random=0 -> rate=-0.125 -> value = -10GB*0.125 = -1342177280
eq(constant_size_threshold(DEFAULT_MAX_FILE_SIZE, 0.0), DEFAULT_MAX_FILE_SIZE - 1342177280,
   "最小抖动时阈值 = 10GB - 1.25GB")
eq(constant_size_threshold(DEFAULT_MAX_FILE_SIZE, 1.0), DEFAULT_MAX_FILE_SIZE + 1342177280,
   "最大抖动时阈值 = 10GB + 1.25GB")
# 抖动恒等于 0 时（jitter=0）阈值不受 random 影响
eq(constant_size_threshold(1024, 0.0, 0.0), 1024, "jitter=0 时完全不抖")

# ---- 4. IncreasingToUpperBound：initialSize = 2 × flush size ----
p = IncreasingToUpperBoundSplitPolicy()
eq(p.initial_size, 256 * MB, "flush 128MB -> initialSize 256MB")
eq(p.size_to_check(1), 256 * MB, "1 个 region 时阈值 = 256MB × 1^3")
# 官方注释：2 个 region -> 2^3 * 128MB * 2 = 2048MB
eq(p.size_to_check(2), 2048 * MB, "2 个 region -> 2^3 × 256MB = 2048MB")
# 官方注释：3 个 region -> 3^3 * 128MB * 2 = 6912MB
eq(p.size_to_check(3), 6912 * MB, "3 个 region -> 3^3 × 256MB = 6912MB")
# 4 个 region 时 256MB × 64 = 16384MB > 10GB，被 maxFileSize 压住
eq(p.size_to_check(4), DEFAULT_MAX_FILE_SIZE, "4 个 region 算出来 16GB，被 10GB 上限压住")
# 边界：count == 0 与 count > 100 都直接用 maxFileSize
eq(p.size_to_check(0), DEFAULT_MAX_FILE_SIZE, "count=0 直接用最大文件大小")
eq(p.size_to_check(101), DEFAULT_MAX_FILE_SIZE, "count>100 直接用最大文件大小（防溢出）")
eq(p.size_to_check(100), DEFAULT_MAX_FILE_SIZE, "count=100 算出来远超 10GB，仍被压住")
ok(p.size_to_check(50) == DEFAULT_MAX_FILE_SIZE, "50 个 region 时早已顶到上限")

# ---- 5. 分裂判定是「严格大于」 ----
eq(p.should_split(256 * MB, 1), False, "正好等于阈值不分裂（严格大于）")
eq(p.should_split(256 * MB + 1, 1), True, "超过 1 字节就分裂")

# ---- 6. 分裂序列：阈值随 region 数三次方增长，然后撞天花板 ----
seq = split_sequence(p, start_regions=1, rounds=6)
eq([c for c, _ in seq], [1, 2, 3, 4, 5, 6], "region 数逐轮 +1")
eq(seq[0][1], 256 * MB, "第 1 轮阈值 256MB")
eq(seq[1][1], 2048 * MB, "第 2 轮阈值 2048MB")
eq(seq[2][1], 6912 * MB, "第 3 轮阈值 6912MB")
eq(seq[3][1], DEFAULT_MAX_FILE_SIZE, "第 4 轮起顶到 10GB")
eq(seq[4][1], DEFAULT_MAX_FILE_SIZE, "第 5 轮仍是 10GB")
# 三次方增长确实越来越快（在有上限之前）
ok(seq[1][1] > seq[0][1] and seq[2][1] > seq[1][1], "前期阈值递增")

# ---- 7. initialSize 可被覆盖 ----
p2 = IncreasingToUpperBoundSplitPolicy(initial_size_override=64 * MB)
eq(p2.initial_size, 64 * MB, "显式配置优先于 2 × flush size")
eq(p2.size_to_check(2), 64 * MB * 8, "覆盖后 2 个 region -> 512MB")
# 非正数不会覆盖（源码要求 > 0）
p3 = IncreasingToUpperBoundSplitPolicy(initial_size_override=0)
eq(p3.initial_size, 256 * MB, "initial_size <= 0 时回退到 2 × flush size")

# ---- 8. ConstantSize 策略：只看最大文件大小 ----
c = ConstantSizeSplitPolicy(rand_float=0.5)
eq(c.threshold, DEFAULT_MAX_FILE_SIZE, "不抖时恒定 10GB")
eq(c.should_split(9 * 1024 * MB), False, "9GB 不分裂")
eq(c.should_split(11 * 1024 * MB), True, "11GB 分裂")
# 两个策略的差别：小表上新表（1 个 region）阈值差 40 倍
eq(p.size_to_check(1) * 40, DEFAULT_MAX_FILE_SIZE, "单 region 时 IncreasingToUpperBound 阈值是 1/40")

# ---- 9. regionSplitLimit 不是硬上限 ----
big = IncreasingToUpperBoundSplitPolicy()
eq(big._can_split(999), True, "999 个 region 仍可分裂")
eq(big._can_split(1000), False, "达到 1000 后本模型不再分裂")

# ---- 10. HFile 版本 ----
eq(hfile_default_version(), 3, "默认 hfile.format.version 是 3")
eq(can_write_hfile(3), True, "v3 可写")
eq(can_write_hfile(4), True, "更高版本可写")
eq(can_write_hfile(2), False, "v2 已不可写（但仍可读）")

print("PASS=%d" % PASS)
sys.exit(0)
