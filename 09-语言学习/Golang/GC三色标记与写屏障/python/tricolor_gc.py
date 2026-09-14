# tricolor_gc.py — 三色标记 + 混合写屏障的最小可运行模拟
# 依据:
#   proposal 17503《Eliminate STW stack re-scanning》(Clements & Hudson, 2016)
#     Go 1.7 Dijkstra 插入屏障 -> 栈 permagrey -> 周期末 STW 重扫(10s~100s ms)
#     Go 1.8 混合写屏障(初实验最坏 STW < 50µs):
#       writePointer(slot, ptr):
#           shade(*slot)                 # Yuasa 删除侧:无条件
#           if current stack is grey:    # Dijkstra 插入侧:仅栈未扫描时
#               shade(ptr)
#           *slot = ptr
#     弱三色不变式:黑对象可指白对象,但白对象必须被某灰对象经白色链保护
#   gc-guide《A Guide to the Go Garbage Collector》: mark-sweep / GOGC /
#     mark assist / 非移动 GC
# 模型:goroutine 栈(可灰可黑)+ 堆对象图;标记与应用交错推进;
# 结尾三类断言:屏障下的 soundness / 浮动垃圾 / 无屏障反例。
import itertools

WHITE, GREY, BLACK = "white", "grey", "black"


class Heap:
    def __init__(self):
        self.objs = {}      # id -> {"slots": dict, "color": str}
        self._ids = itertools.count(1)

    def new(self, **slots):
        oid = next(self._ids)
        self.objs[oid] = {"slots": slots, "color": WHITE}
        return oid

    def shade(self, oid):
        """着色:白 -> 灰(黑/灰不变)。对象进灰色工作集"""
        if oid is not None and oid in self.objs and self.objs[oid]["color"] == WHITE:
            self.objs[oid]["color"] = GREY

    def color(self, oid):
        return self.objs[oid]["color"]


class GC:
    """三色标记器:roots = 各 goroutine 栈;栈自身有灰/黑状态"""

    def __init__(self, heap, stacks):
        self.heap = heap
        self.stacks = stacks   # list[dict]: {"slots": {...}, "black": False}
        self.swept = []

    # ---------- 标记侧 ----------
    def scan_stack(self, s):
        """扫描一个栈:把它指向的全部对象 shade,然后把栈标黑。
        混合屏障的核心承诺:栈一旦扫描变黑,永久保持黑,不再重扫"""
        for ptr in s["slots"].values():
            self.heap.shade(ptr)
        s["black"] = True

    def mark_step(self):
        """处理一个灰色对象:黑化自己、shade 自己的槽位目标"""
        for oid, o in self.heap.objs.items():
            if o["color"] == GREY:
                o["color"] = BLACK
                for ptr in o["slots"].values():
                    self.heap.shade(ptr)
                return True
        # 堆里没灰色了,但栈还没扫描的话先扫栈(并发标记早期扫栈)
        for s in self.stacks:
            if not s["black"]:
                self.scan_stack(s)
                return True
        return False  # 标记完成(无灰、栈全黑)

    # ---------- 应用侧:混合写屏障 ----------
    def heap_write(self, s, obj, slot, ptr):
        """goroutine s 执行 堆对象写:obj.slot = ptr(经混合屏障)"""
        self.heap.shade(self.heap.objs[obj]["slots"].get(slot))  # shade(*slot) 无条件
        if not s["black"]:                                       # 当前栈还是灰
            self.heap.shade(ptr)                                 # shade(ptr)
        self.heap.objs[obj]["slots"][slot] = ptr

    def stack_write(self, s, slot, ptr):
        """栈上写(真实 Go 不给栈写加屏障——这正是 1.7 栈 permagrey 的原因)"""
        s["slots"][slot] = ptr

    # ---------- 清扫 ----------
    def sweep(self):
        """标记完成后清扫:白=死。非移动 GC 不搬对象,只回收白"""
        dead = [oid for oid, o in self.heap.objs.items() if o["color"] == WHITE]
        for oid in dead:
            del self.heap.objs[oid]
        self.swept = dead
        return dead

    def reachable(self):
        """清扫时刻从根(各栈)实际可达的对象集合"""
        seen, work = set(), []
        for s in self.stacks:
            work.extend(s["slots"].values())
        while work:
            oid = work.pop()
            if oid is None or oid in seen or oid not in self.heap.objs:
                continue
            seen.add(oid)
            work.extend(self.heap.objs[oid]["slots"].values())
        return seen


def finish_mark(gc):
    while gc.mark_step():
        pass


# ================= 场景 1:混合屏障下的 soundness =================
# 时序关键:B 比 G 先插入堆,mark_step 一次只黑化一个灰色对象,
# 保证 mutator 动手时 W 还是白色、G 还是灰色。
def scenario_soundness():
    h = Heap()
    B = h.new()            # 将先被黑化(黑对象)
    G = h.new(w=None)      # 灰对象,持白 W 的唯一堆内引用
    W = h.new()            # 待藏匿的白对象
    J = h.new()            # 真垃圾(无人引用)
    h.objs[G]["slots"]["w"] = W
    s1 = {"slots": {"b": B, "g": G}, "black": False}
    gc = GC(h, [s1])

    # 并发标记早期:扫栈 s1(B、G 变灰,s1 变黑),再黑化 B;G 尚灰、W 尚白
    gc.scan_stack(s1)
    assert gc.mark_step() and h.color(B) == BLACK and h.color(G) == GREY
    assert h.color(W) == WHITE, "动手前 W 必须还是白色"

    # ---- mutator 藏匿序列(Dijkstra 插入屏障的经典失败场景)----
    # 1) 从灰 G 读出白 W,写进自己已黑的栈(栈写无屏障)
    gc.stack_write(s1, "tmp", h.objs[G]["slots"]["w"])
    assert h.color(W) == WHITE
    # 2) 把 W 装进黑对象 B(插入侧:s1 栈已黑,不触发 shade(ptr))
    gc.heap_write(s1, B, "b", W)
    # 3) 从灰 G 删除唯一堆引用(删除侧:无条件 shade(*slot) -> W 变灰!)
    gc.heap_write(s1, G, "w", None)
    assert h.color(W) == GREY, "删除侧 shade(*slot) 应保住 W"
    print("场景 1a ✓  藏匿序列被删除屏障拦下:W 已着灰")

    # 插入侧验证:灰栈 s2 把白 W2 装进黑对象 -> shade(ptr) 生效
    W2 = h.new()
    s2 = {"slots": {}, "black": False}
    gc.stacks.append(s2)
    gc.heap_write(s2, B, "b2", W2)   # s2 尚灰 -> 插入屏障 shade(W2)
    assert h.color(W2) == GREY
    print("场景 1b ✓  灰栈写黑对象触发插入屏障:W2 已着灰")

    finish_mark(gc)
    dead = gc.sweep()
    reach = gc.reachable()
    assert W in h.objs and W in reach, "W 清扫时仍可达,必须存活"
    assert dead == [J], "只应回收真垃圾 J"
    print(f"场景 1c ✓  soundness:可达对象零丢失,仅回收 {len(dead)} 个真垃圾")


# ================= 场景 2:浮动垃圾(删除屏障的代价)=================
def scenario_floating_garbage():
    h = Heap()
    root = h.new(ref=None)   # 将被 unlink 的对象
    junk = h.new()            # 真垃圾
    s = {"slots": {"r": root}, "black": False}
    gc = GC(h, [s])
    # 标记推进:root 已被 shade(灰/黑)后,mutator 断开它的唯一引用。
    # 模型外断链(相当于弹栈帧);若走堆写,shade(*slot) 同样保它一轮
    gc.scan_stack(s)
    finish_mark(gc)
    s["slots"]["r"] = None
    dead = gc.sweep()
    assert root in h.objs, "删除屏障制造浮动垃圾:被 unlink 的对象活过本轮"
    assert root not in gc.reachable(), "但它确实已不可达"
    assert dead == [junk]
    print(f"场景 2 ✓  浮动垃圾:root 已不可达却活过本轮(仅回收 {len(dead)} 个真垃圾);"
          "下一轮 GC 才回收它 —— proposal 17503 承认的代价")


# ================= 场景 3:反例 —— 没有屏障会怎样 =================
def scenario_no_barrier():
    h = Heap()
    B = h.new()
    G = h.new(w=None)
    W = h.new()
    h.objs[G]["slots"]["w"] = W
    s1 = {"slots": {"b": B, "g": G}, "black": False}
    gc = GC(h, [s1])

    gc.scan_stack(s1)
    assert gc.mark_step() and h.color(B) == BLACK and h.color(G) == GREY

    # 同样的藏匿序列,但堆写不走屏障(naive 实现)
    gc.stack_write(s1, "tmp", h.objs[G]["slots"]["w"])  # 栈拿到 W(栈写无屏障)
    h.objs[B]["slots"]["b"] = W                         # 装进黑 B,不 shade(ptr)
    h.objs[G]["slots"]["w"] = None                      # 删堆引用,不 shade(*slot)
    assert h.color(W) == WHITE, "无屏障时 W 仍是白色,无灰对象保护它"

    finish_mark(gc)   # G 黑化时已无 W 槽位;栈 s1 已黑且不再重扫
    reach = gc.reachable()  # 必须在清扫前取可达集(清扫会删对象)
    dead = gc.sweep()
    assert W in dead and W in reach, "W 清扫时仍从栈可达,却被当垃圾回收(丢对象!)"
    print("场景 3 ✓  无屏障反例:弱三色不变式被破坏,可达对象 W 被误回收 —— "
          "这就是必须有写屏障的原因")


if __name__ == "__main__":
    scenario_soundness()
    scenario_floating_garbage()
    scenario_no_barrier()
    print("\n全部场景断言通过 ✓")
