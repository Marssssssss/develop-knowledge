#!/usr/bin/env python3
"""CPython 三代 GC 的判定逻辑模型(逐条对应 Python/gc.c)。

权威来源(实际联网阅读):
  - 官方库文档 gc 模块(docs.python.org/3/library/gc.html):
    "The GC classifies objects into three generations depending on how many collection
     sweeps they have survived. New objects are placed in the youngest generation
     (generation 0). If an object survives a collection it is moved into the next older
     generation. Since generation 2 is the oldest generation, objects in that generation
     remain there after a collection. In order to decide when to run, the collector keeps
     track of the number object allocations and deallocations since the last collection.
     When the number of allocations minus the number of deallocations exceeds threshold0,
     collection starts. Initially only generation 0 is examined. If generation 0 has been
     examined more than threshold1 times since generation 1 has been examined, then
     generation 1 is examined as well."
    "gc.collect(generation=2): ... The sum of collected objects and uncollectable objects
     is returned."
    "gc.get_stats(): ... collections is the number of times this generation was collected;
     collected is the total number of objects collected inside this generation;
     uncollectable is the total number of objects which were found to be uncollectable
     (and were therefore moved to the garbage list) inside this generation."
  - CPython `Python/gc.c`:
    * `record_allocation`: generations[0].count++("number of allocated GC objects");
    * `_PyObject_GC_UnTrack` 路径里`if (generations[0].count > 0) generations[0].count--;`
      —— 所以 count **不会变负**;
    * `gc_collect_main`: `if (generation+1 < NUM_GENERATIONS) generations[generation+1].count += 1;`
      然后`for (i = 0; i <= generation; i++) generations[i].count = 0;`
    * `gc_select_generation()`:从最老一代往下找第一个 `count > threshold` 的代号 i;
      但**若 i 是最老一代且 long_lived_pending < long_lived_total / 4 就跳过**(硬编码 25%),
      注释解释了这是为了避免"每固定次数分配就做一次全量收集"造成的二次复杂度(issue #4074)
      —— "each full garbage collection is more and more costly as the number of objects
      grows, but we do fewer and fewer of them"。
    * 晋升时的 long_lived 记账:`if (generation == NUM_GENERATIONS - 2)
      long_lived_pending += gc_list_size(young)`;收到最老一代时
      `long_lived_pending = 0; long_lived_total = gc_list_size(young)`。
  - CPython `Include/internal/pycore_runtime_init.h` 的默认阈值:
    3.13+ 为 `{ .threshold = 2000 }, { 10 }, { 10 }`;3.12 及更早为 `{ 700 }, { 10 }, { 10 }`。
    实测:`python3 -c "import gc;print(gc.get_threshold())"` 在 3.13.14 / 3.14.6 上均为 (2000, 10, 10)。
"""

NUM_GENERATIONS = 3
LONG_LIVED_RATIO = 4            # 硬编码 25%:pending < total / 4 就不做全量收集


class Tracked:
    __slots__ = ("oid", "gen", "reachable", "uncollectable")

    def __init__(self, oid, reachable=False, uncollectable=False):
        self.oid = oid
        self.gen = 0
        self.reachable = reachable
        self.uncollectable = uncollectable


class GenState:
    """对应 CPython 的 struct _gc_runtime_state(只保留三代 GC 相关的部分)。"""

    def __init__(self, thresholds=(2000, 10, 10)):
        self.thresholds = list(thresholds)
        self.counts = [0, 0, 0]
        self.objs = [[], [], []]
        self.garbage = []                   # 不可回收对象"移到 garbage 列表"
        self.long_lived_total = 0
        self.long_lived_pending = 0
        self.stats = [{"collections": 0, "collected": 0, "uncollectable": 0}
                      for _ in range(NUM_GENERATIONS)]

    # ---------------- 计数 ---------------- #
    def record_alloc(self, n=1):
        """record_allocation:只有第 0 代的 count 增长。"""
        self.counts[0] += n

    def record_dealloc(self, n=1):
        """count **不会变负**(CPython 里是 `if (count > 0) count--;`)。"""
        for _ in range(n):
            if self.counts[0] > 0:
                self.counts[0] -= 1

    def track(self, oid, reachable=False, uncollectable=False):
        o = Tracked(oid, reachable, uncollectable)
        self.objs[o.gen].append(o)
        return o

    def enabled(self):
        """自动触发还要看 threshold0 是否为 0(为 0 即关闭收集)。"""
        return self.counts[0] > self.thresholds[0] and self.thresholds[0] != 0

    def select_generation(self):
        """复现 gc_select_generation():返回要收集的最老代号;-1 表示本次不收集。"""
        for i in range(NUM_GENERATIONS - 1, -1, -1):
            if self.counts[i] > self.thresholds[i]:
                if (i == NUM_GENERATIONS - 1
                        and self.long_lived_pending < self.long_lived_total // LONG_LIVED_RATIO):
                    continue                # 全量收集被 25% 启发式推迟
                return i
        return -1

    # ---------------- 收集 ---------------- #
    def collect(self, gen):
        """收掉 0..gen 各代;返回值 = collected + uncollectable(与 gc.collect() 一致)。"""
        # a) 计数更新(顺序与 gc_collect_main 相同)
        if gen + 1 < NUM_GENERATIONS:
            self.counts[gen + 1] += 1
        for i in range(gen + 1):
            self.counts[i] = 0

        # b) 把更年轻的各代并入被收集的最老代(gc_list_merge)
        young = []
        for g in range(gen + 1):
            young.extend(self.objs[g])
            self.objs[g] = []

        # c) 分出 存活 / 不可回收 / 已死
        collected = uncollectable = 0
        alive = []
        for o in young:
            if o.uncollectable:
                uncollectable += 1
                self.garbage.append(o)
                self.stats[gen]["uncollectable"] += 1
            elif o.reachable:
                alive.append(o)
            else:
                collected += 1
                self.stats[gen]["collected"] += 1

        # d) 存活者晋升一代;两处 long_lived 记账(顺序与 gc_collect_main 相同)
        if gen < NUM_GENERATIONS - 1:
            if gen == NUM_GENERATIONS - 2:
                self.long_lived_pending += len(alive)
            for o in alive:
                o.gen = gen + 1
            self.objs[gen + 1].extend(alive)
        else:
            for o in alive:
                o.gen = NUM_GENERATIONS - 1     # 封顶在最老代,不再晋升
            self.objs[gen].extend(alive)
            self.long_lived_pending = 0
            self.long_lived_total = len(self.objs[gen])

        self.stats[gen]["collections"] += 1
        return collected + uncollectable
