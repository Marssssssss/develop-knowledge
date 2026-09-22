"""覆盖率引导模糊测试（AFL 模型）：边覆盖位图、命中数分桶、语料库裁剪与 trimming。

依据 google/AFL 的官方源码与文档：
- `config.h` 的 MAP_SIZE_POW2 / MAP_SIZE / CAL_CYCLES / TMOUT_LIMIT / HAVOC_CYCLES /
  SPLICE_CYCLES / ARITH_MAX / INTERESTING_8|16|32 / MAX_FILE
- `afl-fuzz.c` 的 count_class_lookup8（命中数分桶）与 classify_counts
- `docs/technical_details.txt` 的第 2/3/4/5 节（覆盖度量、队列演化、裁剪、trimming）

模型口径：
- 目标程序用一个"输入字节 -> 基本块序列"的玩具解释器代替，只为产生真实的覆盖轨迹。
- 超时值按文档"5x 初始校准速度、向上取整到 20 ms 的倍数"实现（文档没给公式，此处是
  一种明确的读法，README 已标注口径）。
"""

# ---- config.h ----
MAP_SIZE_POW2 = 16
MAP_SIZE = 1 << MAP_SIZE_POW2          # 65536
CAL_CYCLES = 8                          # 校准时每个用例跑几遍
CAL_CYCLES_LONG = 40
TMOUT_LIMIT = 250                       # 超时次数到这个比例就报警
EXEC_TIMEOUT = 1000
MAX_FILE = 1 * 1024 * 1024
HAVOC_CYCLES = 256
HAVOC_CYCLES_INIT = 1024
SPLICE_CYCLES = 15
ARITH_MAX = 35

# 超时：文档说是"5x 初始校准速度，向上取整到 20 ms"
TMOUT_GRANULARITY_MS = 20


# ---- afl-fuzz.c：命中数 -> 桶（count_class_lookup8）----
def count_class(count):
    """官方的 8 个桶：1 / 2 / 3 / 4-7 / 8-15 / 16-31 / 32-127 / 128+。"""
    if count <= 0:
        return 0
    if count == 1:
        return 1
    if count == 2:
        return 2
    if count == 3:
        return 4
    if count <= 7:
        return 8
    if count <= 15:
        return 16
    if count <= 31:
        return 32
    if count <= 127:
        return 64
    return 128


CLASSIFY_BUCKETS = [(1, 1), (2, 2), (3, 3), (4, 7), (8, 15), (16, 31), (32, 127), (128, None)]


def classify_counts(counters):
    """对每个字节计数做分桶（官方 classify_counts 的逻辑）。"""
    return [count_class(c) for c in counters]


# ---- 边覆盖位图 ----
class Coverage:
    """AFL 的共享位图：index = cur_location ^ prev_location，prev 存的是 cur >> 1。"""

    def __init__(self, map_size=MAP_SIZE):
        self.map_size = map_size
        self.trace_bits = [0] * map_size
        self.prev_location = 0

    def reset(self):
        self.trace_bits = [0] * self.map_size
        self.prev_location = 0

    def visit(self, cur_location):
        index = (cur_location ^ self.prev_location) & (self.map_size - 1)
        self.trace_bits[index] += 1
        # 官方：prev_location = cur_location >> 1，让 A->B 与 B->A 落到不同槽
        self.prev_location = cur_location >> 1
        return index

    def run(self, locations):
        self.reset()
        for loc in locations:
            self.visit(loc)
        return self.trace_bits

    def checksum(self):
        """afl 的 trace 校验和（trimming 阶段靠它判断"删掉这段没影响执行路径"）。"""
        classified = classify_counts(self.trace_bits)
        h = 0
        for v in classified:
            h = (h * 31 + v) & 0xFFFFFFFF
        return h


# ---- has_new_bits（afl-fuzz.c）----
def has_new_bits(trace, virgin_bits):
    """返回 0=没新东西 / 1=已知边出现了新的命中数桶 / 2=全新的边。

    官方实现里 `virgin_bits[i] == 0xff` 表示这条边从没被碰过（字节全 1）。
    """
    ret = 0
    for i, cur in enumerate(trace):
        if not cur:
            continue
        if virgin_bits[i] & cur:
            if ret < 2:
                ret = 2 if virgin_bits[i] == 0xFF else 1
            virgin_bits[i] &= ~cur
    return ret


def new_virgin(map_size=MAP_SIZE):
    return [0xFF] * map_size


# ---- 语料库：队列条目与裁剪 ----
class QueueEntry:
    def __init__(self, name, data, tuples, exec_us, favored=False):
        self.name = name
        self.data = data
        self.tuples = set(tuples)     # 该用例命中的边（位图下标）
        self.exec_us = exec_us
        self.favored = favored

    def score(self):
        """官方：score ∝ 执行延迟 × 文件大小。越小越"划算"。"""
        return self.exec_us * max(1, len(self.data))


def cull_queue(entries):
    """afl 的队列裁剪：贪心集合覆盖 —— 每次挑"还没覆盖的边里分数最低"的用例。

    官方描述：为每条 tuple 选一个最优（分最低）的候选，然后顺序遍历未覆盖的 tuple，
    把胜者的**全部** tuple 并入工作集，直到覆盖完整。
    """
    best_for = {}
    for e in entries:
        for t in e.tuples:
            cur = best_for.get(t)
            if cur is None or e.score() < cur.score():
                best_for[t] = e
    covered = set()
    favored = []
    for e in entries:
        if e.tuples <= covered:
            # 已经被选过的条目要保住 favored 标记，不能因为"现在被覆盖了"就清掉
            if e not in favored:
                e.favored = False
            continue
        # 找一条还没覆盖的边，取它的最优候选，把候选的全部边并入
        seed = next(iter(e.tuples - covered))
        winner = best_for[seed]
        if winner in favored:
            # 已经选过，直接并入当前条目的边（避免死循环）
            covered |= e.tuples
            e.favored = False
            continue
        winner.favored = True
        favored.append(winner)
        covered |= winner.tuples
    return favored


# ---- trimming ----
def trim(data, run_checksum, block_sizes=(16, 8, 4, 2, 1)):
    """afl-fuzz 内置 trimmer：按块删，只要 trace 校验和不变就落盘。

    官方说它"不追求彻底"，只在精度与 execve 次数之间取平衡，平均收益 5-20%。
    """
    baseline = run_checksum(data)
    current = bytearray(data)
    for size in block_sizes:
        i = 0
        while i < len(current):
            candidate = current[:i] + current[i + size:]
            if not candidate:
                break
            if run_checksum(bytes(candidate)) == baseline:
                current = bytearray(candidate)
            else:
                i += size
    return bytes(current)


# ---- 变异：确定性阶段 ----
def interesting_values():
    """config.h 的 INTERESTING_8 / 16 / 32。"""
    return {
        8: [-128, -1, 0, 1, 16, 32, 64, 100, 127],
        16: [-32768, -129, 128, 255, 256, 512, 1000, 1024, 4096, 32767],
        32: [-2147483648, -100663046, -32769, 32768, 65535, 65536, 100663045, 2147483647],
    }


def bitflip_stages(length):
    """确定性 bitflip 的步长序列（bitflip 1/1, 2/1, 4/1, 8/8, 16/8, 32/8）。"""
    return [(1, 1), (2, 1), (4, 1), (8, 8), (16, 8), (32, 8)]


def arith_offsets():
    """确定性 arith：对每个字节/字/双字加减 [1, ARITH_MAX]。"""
    return list(range(1, ARITH_MAX + 1))


def timeout_ms(exec_ms):
    """文档：5x 初始校准速度，向上取整到 20 ms 的倍数。"""
    raw = exec_ms * 5
    if raw <= 0:
        return TMOUT_GRANULARITY_MS
    rounded = ((raw + TMOUT_GRANULARITY_MS - 1) // TMOUT_GRANULARITY_MS) * TMOUT_GRANULARITY_MS
    return max(TMOUT_GRANULARITY_MS, rounded)


def skip_probability(has_new_favorites, fuzzed_before):
    """非 favored 条目被跳过的概率（文档 4 节）。"""
    if has_new_favorites:
        return 0.99
    return 0.95 if fuzzed_before else 0.75


# ---- 玩具目标程序：把输入字节解释成"块序列 + 循环次数" ----
def target_locations(data):
    """每个字节 -> 一个块号，并按低 3 位决定这个块被进入几次（制造命中数）。"""
    locs = []
    for b in data:
        block = (b >> 3) & 0x3F
        times = (b & 0x07) + 1
        locs.extend([block] * times)
    return locs


def run_target(data, cov=None):
    cov = cov or Coverage()
    cov.run(target_locations(data))
    return cov
