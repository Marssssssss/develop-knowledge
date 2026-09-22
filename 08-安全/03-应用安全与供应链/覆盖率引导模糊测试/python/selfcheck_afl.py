"""覆盖率引导模糊测试自检。

期望值来自 google/AFL 的官方源码与文档：
- config.h 的常量（MAP_SIZE / CAL_CYCLES / HAVOC_CYCLES / SPLICE_CYCLES / ARITH_MAX /
  INTERESTING_8|16|32 / MAX_FILE / TMOUT_LIMIT）
- afl-fuzz.c 的 count_class_lookup8 分桶表
- docs/technical_details.txt 的规则（边元组计算、has_new_bits 语义、裁剪、跳过概率、超时）
"""

from afl import (
    ARITH_MAX,
    CAL_CYCLES,
    CAL_CYCLES_LONG,
    HAVOC_CYCLES,
    HAVOC_CYCLES_INIT,
    MAP_SIZE,
    MAP_SIZE_POW2,
    MAX_FILE,
    SPLICE_CYCLES,
    TMOUT_LIMIT,
    Coverage,
    QueueEntry,
    bitflip_stages,
    classify_counts,
    count_class,
    cull_queue,
    has_new_bits,
    interesting_values,
    new_virgin,
    run_target,
    skip_probability,
    target_locations,
    timeout_ms,
    trim,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAILED: " + label
    PASS += 1


def eq(a, b, label):
    ok(a == b, "%s (got %r want %r)" % (label, a, b))


# ---- 1. 常量（config.h）----
eq(MAP_SIZE_POW2, 16, "MAP_SIZE_POW2 = 16")
eq(MAP_SIZE, 65536, "MAP_SIZE = 2^16 = 65536")
eq(CAL_CYCLES, 8, "CAL_CYCLES = 8")
eq(CAL_CYCLES_LONG, 40, "CAL_CYCLES_LONG = 40")
eq(TMOUT_LIMIT, 250, "TMOUT_LIMIT = 250")
eq(HAVOC_CYCLES, 256, "HAVOC_CYCLES = 256")
eq(HAVOC_CYCLES_INIT, 1024, "HAVOC_CYCLES_INIT = 1024")
eq(SPLICE_CYCLES, 15, "SPLICE_CYCLES = 15")
eq(ARITH_MAX, 35, "ARITH_MAX = 35")
eq(MAX_FILE, 1024 * 1024, "MAX_FILE = 1 MiB")

# ---- 2. 命中数分桶（afl-fuzz.c 的 count_class_lookup8）----
eq(count_class(0), 0, "0 -> 0（没执行到）")
eq(count_class(1), 1, "1 -> 1")
eq(count_class(2), 2, "2 -> 2")
eq(count_class(3), 4, "3 -> 4（3 单独一档）")
eq(count_class(4), 8, "4 -> 8")
eq(count_class(7), 8, "7 -> 8（桶内上界）")
eq(count_class(8), 16, "8 -> 16（跨桶）")
eq(count_class(15), 16, "15 -> 16")
eq(count_class(16), 32, "16 -> 32")
eq(count_class(31), 32, "31 -> 32")
eq(count_class(32), 64, "32 -> 64")
eq(count_class(127), 64, "127 -> 64")
eq(count_class(128), 128, "128 -> 128")
eq(count_class(255), 128, "255 -> 128（上界桶）")
# 桶的意义：桶内变化被忽略，跨桶才算"有意思"
eq(count_class(47), count_class(48), "47 与 48 同一桶 -> 不算新覆盖")
ok(count_class(31) != count_class(32), "31 -> 32 跨桶 -> 算新覆盖（成对照组）")
eq(classify_counts([0, 1, 3, 100]), [0, 1, 4, 64], "classify_counts 逐字节分桶")

# ---- 3. 边覆盖：index = cur ^ prev，prev 存的是 cur >> 1 ----
cov = Coverage(map_size=64)
cov.visit(10)
i1 = cov.visit(20)
cov2 = Coverage(map_size=64)
cov2.visit(20)
i2 = cov2.visit(10)
ok(i1 != i2, "A->B 与 B->A 落到不同槽（%d vs %d）" % (i1, i2))
eq((10 ^ (10 >> 1)) & 63, (10 ^ 5) & 63, "prev 确实是 cur >> 1")
# 块号 6：prev = 6 >> 1 = 3，所以首次落槽 6，之后的自环落槽 6 ^ 3 = 5
cov3 = Coverage(map_size=64)
cov3.run([6, 6, 6])
eq((6 ^ 0) & 63, 6, "首次进入：prev=0 -> 槽 6")
eq((6 ^ (6 >> 1)) & 63, 5, "自环：prev=3 -> 槽 5")
eq(cov3.trace_bits[6], 1, "槽 6 命中 1 次")
eq(cov3.trace_bits[5], 2, "槽 5 命中 2 次")
# 同一个块序列跑两次，位图必须一致（确定性）
a = Coverage()
b = Coverage()
a.run(target_locations(b"hello"))
b.run(target_locations(b"hello"))
eq(a.trace_bits, b.trace_bits, "同一输入两次执行位图一致")

# ---- 4. has_new_bits 的三态 ----
virgin = new_virgin(64)
cov = Coverage(map_size=64)
cov.run([1, 2])
trace = classify_counts(cov.trace_bits)
eq(has_new_bits(trace, virgin), 2, "全新边 -> 2")
# 同一输入再来一次：什么都没变
eq(has_new_bits(trace, virgin), 0, "重复执行 -> 0")
# 边不变但命中数跨桶 -> 1
virgin2 = new_virgin(64)
c1 = Coverage(map_size=64)
c1.run([1] * 2)          # 命中数 1~2 落在小桶
has_new_bits(classify_counts(c1.trace_bits), virgin2)
c2 = Coverage(map_size=64)
c2.run([1] * 40)         # 命中数涨到 32~127 桶
eq(has_new_bits(classify_counts(c2.trace_bits), virgin2), 1,
   "已知边出现新的命中数桶 -> 1")
# 负控：命中数变化但没跨桶 -> 0
virgin3 = new_virgin(64)
c3 = Coverage(map_size=64)
c3.run([1] * 33)
has_new_bits(classify_counts(c3.trace_bits), virgin3)
c4 = Coverage(map_size=64)
c4.run([1] * 35)         # 33 与 35 都在 32~127 桶
eq(has_new_bits(classify_counts(c4.trace_bits), virgin3), 0,
   "命中数变化但没跨桶 -> 0（成对照组）")

# ---- 5. 队列裁剪：贪心集合覆盖 ----
entries = [
    QueueEntry("slow-big", b"x" * 1000, {1, 2, 3}, exec_us=1000),
    QueueEntry("fast-cover", b"y" * 10, {3, 4}, exec_us=10),
    QueueEntry("redundant", b"z" * 20, {1, 2}, exec_us=50),
    QueueEntry("unique", b"w" * 5, {9}, exec_us=20),
]
favored = cull_queue(entries)
names = sorted(e.name for e in favored)
ok("fast-cover" in names, "低分且覆盖面广的条目被选中（%s）" % names)
ok("unique" in names, "独占一条边的条目必须被选中（否则覆盖不全）")
ok("slow-big" not in names, "又慢又大且被别人覆盖的条目被裁掉")
covered = set()
for e in favored:
    covered |= e.tuples
eq(covered, {1, 2, 3, 4, 9}, "favored 集合覆盖了全部边")
# 打分口径：延迟 × 大小
eq(QueueEntry("a", b"x" * 10, set(), 100).score(), 1000, "score = 执行延迟 × 文件大小")
ok(QueueEntry("a", b"x" * 10, set(), 100).score()
   < QueueEntry("b", b"x" * 10, set(), 200).score(), "同样的大小，慢的分更高（更不受欢迎）")

# ---- 6. 跳过非 favored 条目的概率（文档 4 节）----
eq(skip_probability(True, True), 0.99, "有新 favorites 时跳过 99%")
eq(skip_probability(False, True), 0.95, "没有新 favorites、且以前跑过 -> 95%")
eq(skip_probability(False, False), 0.75, "从没跑过 -> 75%")

# ---- 7. trimming：只接受不改变执行路径的删除 ----
base = b"AAAABBBBCCCCDDDD"


def checksum(data):
    return run_target(data).checksum()


trimmed = trim(base, checksum)
ok(len(trimmed) <= len(base), "trim 后不会变长（%d -> %d）" % (len(base), len(trimmed)))
eq(checksum(trimmed), checksum(base), "trim 后执行路径的校验和不变")
# 尾部填充不影响执行路径时，trimmer 必须能把它们删掉
def dead_tail_checksum(data):
    """玩具目标里 'Z' 字节不产生任何块（模拟"读不到的数据"）。"""
    return run_target(bytes(b for b in data if b != ord("Z"))).checksum()


padded = b"ABCD" + b"Z" * 12
out = trim(padded, dead_tail_checksum)
ok(len(out) < len(padded), "trim 删掉了不影响执行路径的填充（%d -> %d）" % (len(padded), len(out)))
ok(ord("Z") not in out, "填充字节被全部删掉")
eq(dead_tail_checksum(out), dead_tail_checksum(padded), "删除前后执行路径校验和一致")

# ---- 8. 确定性变异阶段的参数 ----
eq(bitflip_stages(16), [(1, 1), (2, 1), (4, 1), (8, 8), (16, 8), (32, 8)],
   "bitflip 六档：1/1 2/1 4/1 8/8 16/8 32/8")
vals = interesting_values()
eq(vals[8], [-128, -1, 0, 1, 16, 32, 64, 100, 127], "INTERESTING_8 取自 config.h")
eq(vals[16][:3], [-32768, -129, 128], "INTERESTING_16 开头三项")
eq(vals[32][0], -2147483648, "INTERESTING_32 的第一项是 INT32_MIN")
eq(len(vals[8]), 9, "INTERESTING_8 共 9 个值")
eq(len(vals[16]), 10, "INTERESTING_16 共 10 个值")
ok(vals[8][0] == -128 and vals[8][-1] == 127,
   "8 位兴趣值的两端分别是减一溢出与加一溢出")

# ---- 9. 超时（文档：5x 初始校准速度，向上取整到 20 ms）----
eq(timeout_ms(1), 20, "1 ms 的 5 倍只有 5 ms -> 抬到 20 ms 粒度")
eq(timeout_ms(4), 20, "4 ms -> 20 ms（恰好等于粒度）")
eq(timeout_ms(10), 60, "10 ms -> 50 ms 向上取整到 60 ms")
eq(timeout_ms(100), 500, "100 ms -> 500 ms（正好是粒度整数倍）")
ok(timeout_ms(10) >= 10 * 5, "超时不会低于 5 倍校准值")

print("afl selfcheck: %d assertions passed" % PASS)
