"""Window TinyLFU —— Caffeine 的默认淘汰/准入策略。

权威依据（ben-manes/caffeine Wiki《Efficiency》，作者本人维护）：
  - "in typical workloads LRU is not optimal and may have a poor hit rate in
     cases like full scans"
  - "W-TinyLfu uses a small admission LRU that evicts to a large Segmented LRU
     if accepted by the TinyLfu admission policy"
  - "TinyLfu relies on a frequency sketch to probabilistically estimate the
     historic usage of an entry"
  - "The window allows the policy to have a high hit rate when entries exhibit
     recency bursts which would otherwise be rejected"
  - "The size of the window vs main space is adaptively determined using a
     hill climbing optimization"
  - "This implementation uses a 4-bit CountMinSketch, growing at 8 bytes per
     cache entry"
  - "Unlike ARC and LIRS, this policy does not retain evicted keys"
"""
from collections import OrderedDict

COUNTER_BITS = 4
COUNTER_MAX = (1 << COUNTER_BITS) - 1      # 15
COUNTERS_PER_LONG = 16                     # 一个 long = 16 个 4-bit 计数器
NUM_HASHES = 4


# ---------------------------------------------------------------- 哈希

def _hash64(x):
    """确定性 64 位混合（不依赖 PYTHONHASHSEED）。"""
    h = 0xC4CEB9FE1A85EC53
    for ch in str(x):
        h = ((h ^ ord(ch)) * 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
    h ^= h >> 29
    h = (h * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    h ^= h >> 32
    return h


def _rehash(h):
    h = (h * 0xFF51AFD7ED558CCD) & 0xFFFFFFFFFFFFFFFF
    h ^= h >> 33
    return h


# ---------------------------------------------------------------- 频率草图

class FrequencySketch:
    """4-bit Count-Min Sketch。

    布局对齐 Caffeine：table 有 ceiling_pow2(capacity) 个 long，
    每个 long 装 16 个 4-bit 计数器 → 合计 16×capacity 个计数器 × 4 bit
    = **8 字节/条目**（与 wiki 的 "8 bytes per cache entry" 一致）。
    """

    def __init__(self, capacity):
        n = 1
        while n < capacity:
            n <<= 1
        self.n_longs = n
        self.n_counters = n * COUNTERS_PER_LONG
        self.table = bytearray(self.n_counters)
        # 老化：达到阈值后全体减半（Count-Min Sketch 的标准做法，
        # 使「历史频率」随时间衰减。具体阈值是本 demo 的设计选择）
        self.reset_threshold = 10 * max(capacity, 1)
        self.size = 0

    def memory_bytes(self):
        return self.n_counters * COUNTER_BITS // 8

    def _indices(self, key):
        h = _hash64(key)
        block = (h & (self.n_longs - 1)) * COUNTERS_PER_LONG
        h2 = _rehash(h)
        return [block + ((h2 >> (i * 4)) & 0xF) for i in range(NUM_HASHES)]

    def increment(self, key):
        self.size += 1
        if self.size >= self.reset_threshold:
            self.reset()
        for i in self._indices(key):
            if self.table[i] < COUNTER_MAX:
                self.table[i] += 1

    def frequency(self, key):
        """Count-Min 的估计量：**取 4 个计数器的最小值**。"""
        return min(self.table[i] for i in self._indices(key))

    def reset(self):
        for i in range(self.n_counters):
            self.table[i] >>= 1
        self.size >>= 1


# ---------------------------------------------------------------- 基线 LRU

class LRUCache:
    def __init__(self, capacity):
        self.cap = capacity
        self.d = OrderedDict()
        self.hits = self.misses = 0

    def access(self, key):
        if key in self.d:
            self.hits += 1
            self.d.move_to_end(key)
            return True
        self.misses += 1
        self.d[key] = True
        if len(self.d) > self.cap:
            self.d.popitem(last=False)
        return False

    def hit_rate(self):
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


# ---------------------------------------------------------------- W-TinyLFU

class WTinyLFU:
    """窗口 LRU（准入区）+ 主区 SLRU（probation/protected）。"""

    def __init__(self, capacity, window_pct=0.01, protected_pct=0.80,
                 adaptive=False):
        self.cap = capacity
        self.window_pct = window_pct
        self.protected_pct = protected_pct
        self.adaptive = adaptive
        self.window = OrderedDict()
        self.probation = OrderedDict()
        self.protected = OrderedDict()
        self.sketch = FrequencySketch(capacity)
        self.hits = self.misses = 0
        self.admitted = 0
        self.rejected = 0
        self._apply_sizes()

    # -- 容量划分 ------------------------------------------------
    def _apply_sizes(self):
        # window_pct=0 表示「没有窗口」= 纯 TinyLFU 准入（用于对比实验）
        self.window_max = int(self.cap * self.window_pct)
        self.main_max = max(1, self.cap - self.window_max)
        self.protected_max = max(1, int(self.main_max * self.protected_pct))

    @property
    def size(self):
        return len(self.window) + len(self.probation) + len(self.protected)

    # -- 访问 ----------------------------------------------------
    def access(self, key):
        self.sketch.increment(key)
        if key in self.window:
            self.hits += 1
            self.window.move_to_end(key)
            return True
        if key in self.protected:
            self.hits += 1
            self.protected.move_to_end(key)
            return True
        if key in self.probation:
            self.hits += 1
            self._promote(key)
            return True
        self.misses += 1
        self.window[key] = True
        self._evict()
        return False

    def _promote(self, key):
        self.probation.pop(key)
        self.protected[key] = True
        while len(self.protected) > self.protected_max:
            k, _ = self.protected.popitem(last=False)   # 降级回 probation
            self.probation[k] = True

    def _main_victim(self):
        """主区受害者：优先取 probation 的 LRU，没有再取 protected 的 LRU。"""
        if self.probation:
            return next(iter(self.probation))
        if self.protected:
            return next(iter(self.protected))
        return None

    def _pop_main(self, key):
        if key in self.probation:
            self.probation.pop(key)
        else:
            self.protected.pop(key)

    def _evict(self):
        # 阶段一：窗口超过配额时，把窗口 LRU 交给主区裁决
        while len(self.window) > self.window_max:
            candidate, _ = self.window.popitem(last=False)
            if self.size < self.cap:
                # 缓存还没装满 —— 直接降级进 probation，**不做准入测试**。
                # 这一点很关键：若从冷启动就做准入，所有条目频率都是 1，
                # 判据 `>` 会把每一个新条目都拒掉，缓存永远装不满。
                self.probation[candidate] = True
                continue
            victim = self._main_victim()
            if victim is not None and (self.sketch.frequency(candidate)
                                       > self.sketch.frequency(victim)):
                self.admitted += 1
                self._pop_main(victim)
                self.probation[candidate] = True
            else:
                self.rejected += 1            # 候选被直接丢弃，不进主区
        # 阶段二：总量超限，从 probation 的 LRU 端淘汰
        while self.size > self.cap:
            if self.probation:
                self.probation.popitem(last=False)
            elif self.protected:
                k, _ = self.protected.popitem(last=False)
                self.probation[k] = True      # protected 满了先降级，不直接丢
            else:
                break

    def hit_rate(self):
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    # -- 爬山自适应窗口 ------------------------------------------
    def hill_climb(self, window_hit_rate, main_hit_rate):
        """简化版 hill climbing：哪边命中率高就把窗口往哪边调。

        Caffeine wiki："The size of the window vs main space is adaptively
        determined using a hill climbing optimization."
        """
        if not self.adaptive:
            return
        step = 0.01
        if window_hit_rate > main_hit_rate:
            self.window_pct = min(0.50, self.window_pct + step)
        else:
            self.window_pct = max(0.01, self.window_pct - step)
        self._apply_sizes()


# ---------------------------------------------------------------- 负载

def zipf_trace(n_hot, hot_reps, cold_start, n_cold, cold_reps=1):
    """热集高频 + 冷集一次性：模拟「热点 + 后台任务扫描」混合负载。"""
    trace = []
    for _ in range(hot_reps):
        trace.extend("hot%d" % i for i in range(n_hot))
    trace.extend("cold%d" % i for i in range(cold_start, cold_start + n_cold)
                 for _ in range(cold_reps))
    return trace


def scan_pollution_trace(n_hot, warm_reps, n_scan):
    """先充分预热热集，再插入一段一次性扫描，最后重新访问热集。

    最后一段的访问结果就是「缓存被污染了多少」的直接度量。
    """
    trace = ["hot%d" % i for _ in range(warm_reps) for i in range(n_hot)]
    trace += ["scan%d" % i for i in range(n_scan)]
    return trace, ["hot%d" % i for i in range(n_hot)]
