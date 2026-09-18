#!/usr/bin/env python3
"""分代式 GC 模型:eden + 两个 survivor + 老年代 + 写屏障/记忆集。

权威来源(实际联网阅读):
  - Oracle《Java SE 8 HotSpot VM GC Tuning Guide》第 3 章 *Generations*:
    "the weak generational hypothesis, which states that most objects survive for
     only a short period of time";"The young generation consists of eden and two
     survivor spaces";"Objects are copied between survivor spaces in this way until
     they are old enough to be tenured";"it causes a minor collection in which only
     the young generation is collected";"a major collection, in which the entire
     heap is collected";"The costs of such collections are, to the first order,
     proportional to the number of live objects being collected"
  - V8 博客 *Orinoco: young generation garbage collection*:
    "objects are initially allocated in the "nursery" of the young generation. Upon
     surviving a garbage collection, objects are copied into the intermediate
     generation ... After surviving another garbage collection, these objects are
     moved into the old generation";"Old-to-young generation references are roots for
     the young generation garbage collection. These references are recorded to
     provide efficient root identification and reference updates when objects are
     moved."

建模简化(README 中已声明):
  - 对象在各空间之间"搬动"用列表归属表示(同一个 Python 对象换列表),不模拟真实地址;
  - 卡表粒度 = 一个槽(真实实现里是一张卡 = 512 B / 一页)。
"""

EDEN_CAPACITY = 6
SURVIVOR_CAPACITY = 6
YOUNG_CAPACITY = EDEN_CAPACITY + SURVIVOR_CAPACITY
OLD_CAPACITY = 24
TENURING_THRESHOLD = 2          # 对应 HotSpot 的 MaxTenuringThreshold


class Obj:
    __slots__ = ("oid", "words", "age", "gen", "fields")

    def __init__(self, oid, words=1):
        self.oid = oid
        self.words = words
        self.age = 0
        self.gen = 0            # 0 = 新生代(eden/survivor),1 = 老年代
        self.fields = {}

    def __repr__(self):
        return f"<{self.oid} words={self.words} age={self.age} gen={self.gen}>"


class GenHeap:
    """eden + 两个 survivor + 老年代;`barrier` 可关掉写屏障做对照。"""

    def __init__(self, tenuring=TENURING_THRESHOLD, barrier=True, generational=True,
                 young_capacity=YOUNG_CAPACITY, old_capacity=OLD_CAPACITY):
        self.eden = []
        self.survivors = [[], []]       # 只有 survivors[cur] 是"活"的那个
        self.cur = 0
        self.old = []
        self.roots = []
        self.remembered = []            # 记忆集:老年代里指向新生代的槽 (owner, name)
        self.cards = set()              # 脏卡(粒度 = 一个槽)
        self.tenuring = tenuring
        self.barrier = barrier
        self.generational = generational
        self.young_capacity = young_capacity
        self.old_capacity = old_capacity
        self.heap_capacity = young_capacity + old_capacity
        self.minor_gcs = 0
        self.major_gcs = 0
        self.promoted = 0
        self.traced_words = 0           # 累计被扫描的字数 = 成本代理量

    # ---------------- 分配与写 ---------------- #
    def alloc(self, oid, words=1):
        o = Obj(oid, words)
        self.eden.append(o)
        return o

    def store(self, owner, name, target):
        """带写屏障的存储:**老 -> 新** 引用必须进记忆集,否则 minor GC 会漏掉它。"""
        owner.fields[name] = target
        if self.barrier and target is not None and owner.gen == 1 and target.gen == 0:
            self.cards.add((owner.oid, name))
            if (owner, name) not in self.remembered:
                self.remembered.append((owner, name))
        return owner

    def raw_store(self, owner, name, target):
        """无屏障写(对照实验用)。"""
        owner.fields[name] = target
        return owner

    # ---------------- 空间与统计 ---------------- #
    def young(self):
        return self.eden + self.survivors[self.cur]

    def young_words(self):
        return sum(o.words for o in self.young())

    def old_words(self):
        return sum(o.words for o in self.old)

    def resident(self):
        return {id(o) for o in self.young() + self.old}

    def dangling(self):
        """仍被引用、却不在任何空间里的对象 —— 移动/回收后的悬空引用。"""
        res, out = self.resident(), []
        for o in self.young() + self.old:
            for name, t in o.fields.items():
                if t is not None and id(t) not in res:
                    out.append((o.oid, name, t.oid))
        for r in self.roots:
            if r is not None and id(r) not in res:
                out.append(("root", "-", r.oid))
        return out

    # ---------------- minor GC:只收新生代 ---------------- #
    def minor(self, extra_seeds=()):
        self.minor_gcs += 1
        self.traced_words += self.young_words()      # 成本 ∝ 新生代,与老年代无关
        to = 1 - self.cur
        dest = []
        fwd = {}

        # 根 = mutator 根 + **记忆集**(老年代 -> 新生代 的槽)
        seeds = [r for r in self.roots if r is not None and r.gen == 0]
        for owner, name in self.remembered:
            t = owner.fields.get(name)
            if t is not None and t.gen == 0:
                seeds.append(t)
        seeds.extend(s for s in extra_seeds if s is not None)

        work = list(seeds)
        while work:
            o = work.pop(0)
            if o.oid in fwd:
                continue
            fwd[o.oid] = o
            o.age += 1
            promoted = o.age >= self.tenuring
            if promoted:
                o.gen = 1
                self.old.append(o)
                self.promoted += 1
            else:
                dest.append(o)
            for name, t in list(o.fields.items()):
                if t is not None and t.gen == 0:
                    if promoted and self.barrier:
                        # 晋升**新造**了 老->新 引用,必须补进记忆集
                        self.cards.add((o.oid, name))
                        if (o, name) not in self.remembered:
                            self.remembered.append((o, name))
                    work.append(t)

        # 老年代里指向"已被回收对象"的槽必须清空,否则就是悬空引用
        self._drop_dead_slots(fwd)
        self.eden = []
        self.survivors[self.cur] = []
        self.survivors[to] = dest
        self.cur = to
        return fwd

    def _drop_dead_slots(self, live_ids):
        for owner, name in list(self.remembered):
            t = owner.fields.get(name)
            if t is not None and t.gen == 0 and t.oid not in live_ids:
                owner.fields[name] = None
                self.remembered.remove((owner, name))
                self.cards.discard((owner.oid, name))

    # ---------------- major GC:全堆 ---------------- #
    def major(self):
        """全堆收集:根 = mutator 根 —— **不需要记忆集**,因为整堆都在扫描范围内。"""
        self.major_gcs += 1
        self.traced_words += self.young_words() + self.old_words()
        reachable, work = set(), [r for r in self.roots if r is not None]
        while work:
            o = work.pop()
            if o.oid in reachable:
                continue
            reachable.add(o.oid)
            for t in o.fields.values():
                if t is not None:
                    work.append(t)
        before = self.young() + self.old
        self.old = [o for o in self.old if o.oid in reachable]
        self.eden = [o for o in self.young() if o.oid in reachable]
        self.survivors[self.cur] = []
        dead = len(before) - len(self.old) - len(self.eden)
        self.remembered, self.cards = [], set()
        self._rebuild_remembered()
        return dead

    def _rebuild_remembered(self):
        if not self.barrier:
            return
        for o in self.old:
            for name, t in o.fields.items():
                if t is not None and t.gen == 0:
                    self.cards.add((o.oid, name))
                    self.remembered.append((o, name))

    # ---------------- 触发条件 ---------------- #
    def step(self):
        """一次"分配之后"的容量检查。

        分代模式:新生代(nursery)满 -> minor —— 只看 nursery 那点预算。
        非分代基线:**只把这一层关掉** —— 没有 nursery,"堆满"就是整个堆预算满,
        于是同样的负载下它只能一次一次地做全堆收集。
        两边共用同一套 `young_capacity + old_capacity` 的堆预算,根集与对象大小完全一致。
        """
        if self.generational:
            if self.young_words() >= self.young_capacity:
                self.minor()
        elif self.young_words() + self.old_words() >= self.heap_capacity:
            self.major()
        if self.old_words() >= self.old_capacity:
            self.major()
