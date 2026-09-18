"""Kotlin/Native 新旧内存模型的三个可观测行为模型(纯标准库)。

全部行为描述来自以下权威页面(2026-09-18 实读):
  * kotlinlang.org/docs/native-memory-manager.html —— 新内存管理器:共享堆、
    concurrent mark and sweep、GC 触发方式与并行标记、手写 collect、GC 日志开关。
  * kotlinlang.org/docs/native-migration-guide.html —— 新旧对比:顶层属性、
    Worker.execute/executeAfter 的限制解除、全局属性的**惰性初始化**、
    AtomicReference 环不再泄漏、freeze 系列 API 全部废弃。
  * kotlinlang.org/docs/native-arc-integration.html —— 与 Swift/ObjC ARC 的集成:
    deinit 在哪个线程、对象只在 GC 时被回收、GC 日志里的 stable refs 持续增长
    以及用 autoreleasepool 包循环体的对策。

模型只复刻**文档明确写过的因果链**,不引入任何未文档化的数值(GC 触发阈值、
代数、页大小等一律不写死,只当作"外部不确定量"参与推理)。
"""


# ---------------------------------------------------------------------------
# 实验一:stable refs —— 长循环里跨边界的临时对象会不会被及时释放
# ---------------------------------------------------------------------------

class InteropRootSet:
    """模拟 GC 日志里的 "number of stable refs in the root set"。

    文档原话:长循环每轮都造出若干跨 interop 边界的临时对象时,这个数会一直涨,
    说明 Swift/ObjC 对象没有在"该释放的时候"释放;对策是把循环体包进 autoreleasepool。
    """

    def __init__(self):
        self.stable_refs = 0          # 当前根集合里的临时对象数
        self.peak = 0
        self.drains = 0
        self.bytes_held = 0           # 每个临时对象按 1 单位占用计

    def create_temporary(self):
        self.stable_refs += 1
        self.bytes_held += 1
        self.peak = max(self.peak, self.stable_refs)

    def drain_pool(self):
        """autoreleasepool 块结束时排空 —— doc: 特殊 GC 线程有 run loop 并排空 autorelease pool。"""
        if self.stable_refs:
            self.drains += 1
        self.bytes_held -= self.stable_refs
        self.stable_refs = 0

    def gc_collect(self, reachable):
        """GC 只在真正跑起来时才回收跨边界对象;reachable 为真正被引用的集合。"""
        before = self.stable_refs
        self.stable_refs = len(reachable)
        self.bytes_held = len(reachable)
        return before - self.stable_refs


def loop_with_temporaries(iterations, use_autoreleasepool):
    """文档里的那段长循环:每轮造一个临时跨边界对象。

    返回 (peak, drains, held)。held 是循环结束时仍被根集合扣住的量。
    """
    roots = InteropRootSet()
    for _ in range(iterations):
        if use_autoreleasepool:
            # 每轮开一个 pool,轮末排空
            roots.create_temporary()
            roots.drain_pool()
        else:
            roots.create_temporary()
    return roots.peak, roots.drains, roots.bytes_held


# ---------------------------------------------------------------------------
# 实验二:顶层/全局属性的初始化时机 —— 启动时 vs 首次访问所属文件时
# ---------------------------------------------------------------------------

class GlobalInit:
    """legacy:全局属性在程序启动时初始化;new:所属文件首次被访问时才初始化。"""

    def __init__(self, eager, eager_marked=()):
        self.eager = eager
        # 新模型下想让某个全局在程序启动时就初始化,要靠 @EagerInitialization 标注
        self.eager_marked = set(eager_marked)
        self.initialized = set()
        self.log = []

    def startup(self, all_globals):
        if self.eager:
            for name in all_globals:
                self._init(name, "startup")
        else:
            # 惰性模型:只有被 @EagerInitialization 标注的才在启动时初始化
            for name in all_globals:
                if name in self.eager_marked:
                    self._init(name, "startup@EagerInitialization")
            self.log.append(("startup", "lazy-rest-deferred"))

    def access_file(self, file_name, globals_in_file):
        if not self.eager:
            for name in globals_in_file:
                if name not in self.initialized:
                    self._init(name, "first-access:%s" % file_name)

    def _init(self, name, reason):
        self.initialized.add(name)
        self.log.append(("init", name, reason))


# ---------------------------------------------------------------------------
# 实验三:AtomicReference 构成的引用环能否被回收
# ---------------------------------------------------------------------------

class Node:
    def __init__(self, name):
        self.name = name
        self.edges = []
        self.refcount = 0        # 仅 legacy 路径使用


def build_two_cycle():
    """A -> B -> A 的引用环;外部不再持有它们。"""
    a, b = Node("A"), Node("B")
    a.edges.append(b)
    b.edges.append(a)
    a.refcount, b.refcount = 1, 1    # 环内互相引用各 +1
    return [a, b]


def reclaim_by_refcount(two_cycle, external_refs=0):
    """legacy:引用计数式回收。环内互引使计数永远降不到 0。"""
    nodes = list(two_cycle)
    for node in nodes:
        node.refcount += external_refs
    freed = []
    changed = True
    while changed:
        changed = False
        for node in list(nodes):
            if node.refcount == 0:
                nodes.remove(node)
                freed.append(node.name)
                for dst in node.edges:
                    dst.refcount -= 1
                changed = True
    return freed


def reclaim_by_tracing(two_cycle, external_refs=0):
    """new:从根做可达性分析。环内互引不构成"从根可达",整体被回收。"""
    nodes = list(two_cycle)
    roots = nodes[:external_refs]
    reachable = set()
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node.name in reachable:
            continue
        reachable.add(node.name)
        for dst in node.edges:
            if dst.name not in reachable:
                stack.append(dst)
    return sorted(n.name for n in nodes if n.name not in reachable)
