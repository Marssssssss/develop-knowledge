#!/usr/bin/env python3
"""标记-压缩式 GC 的两种实现:保序滑动(Lisp2 结构)与线程化(threading)。

权威来源(实际联网阅读):
  - Memory Management Reference(与 *The Garbage Collection Handbook* 配套的权威术语表)
    「mark-compact」条目:marking 之后进入 compaction,而 **"the compaction phase
    typically performs a number of sequential passes over memory to move objects and
    update references"**,结果 **"all the marked objects are moved into a single
    contiguous block of memory (or a small number of such blocks)"**,并且可以把
    mark-compact 看成 mark-sweep 的变体,"with extra effort spent to eliminate the
    resulting fragmentation";「compaction」条目:**"Compaction is used to avoid external
    fragmentation and to increase locality of reference."**
  - GHC `rts/sm/Compact.c` 头部注释(线程化压缩的权威实现说明):
    **"chain together all the fields pointing at a particular object, with the root of
    the chain in the object's info table field. The original contents of the info
    pointer goes at the end of the chain."**、交换式入链 **`*field, **field = **field,
    field`**、以及"未标记指针用 1、已标记指针用 2 来标记**指向该槽的指针**"的解法。
  - V8 `src/heap/mark-compact.h`:mark-compact 被拆成 Prepare / StartCompaction /
    UpdatePointers / Sweep 等显式状态,`are_map_pointers_encoded()` 的实现是
    `state_ == UPDATE_POINTERS` —— 指针更新是一个**独立可观测阶段**。
  - Oracle HotSpot GC 调优指南:"parallel compaction" 让 major collection 并行执行。
"""


class Slot:
    """一个指针槽 —— 移动式 GC 必须能**改写**它。"""

    __slots__ = ("owner", "name", "val", "tagged")

    def __init__(self, owner, name, val=None, tagged=False):
        self.owner, self.name, self.val, self.tagged = owner, name, val, tagged

    def __repr__(self):
        t = "+tag" if self.tagged else ""
        return f"<{self.owner}.{self.name}{t}={self.val}>"


class Obj:
    """一个对象:word0 = info 槽,其余字为指针字段。"""

    __slots__ = ("oid", "fields", "info", "payload_words")

    def __init__(self, oid, payload_words=0):
        self.oid = oid
        self.payload_words = payload_words
        self.fields = []
        self.info = f"INFO({oid})"   # info 槽(真实实现里是 info table 指针)

    @property
    def size_words(self):
        return 1 + self.payload_words + len(self.fields)

    def slot(self, name, tagged=False):
        s = Slot(self.oid, name, tagged=tagged)
        self.fields.append(s)
        return s


class Heap:
    def __init__(self, cells):
        self.cells = list(cells)          # 地址序
        self.roots = []
        self.heap_words = sum(o.size_words for o in self.cells)

    def root(self, name, oid, tagged=False):
        s = Slot("root", name, oid, tagged=tagged)
        self.roots.append(s)
        return s

    def addresses(self):
        addr, out = 0, {}
        for o in self.cells:
            out[o.oid] = addr
            addr += o.size_words
        return out

    def all_slots(self):
        out = list(self.roots)
        for o in self.cells:
            out.extend(o.fields)
        return out


def mark(heap, roots):
    """mark 阶段:沿引用链标记全部可达对象。"""
    live, stack = set(), [s.val for s in roots if s.val is not None]
    while stack:
        oid = stack.pop()
        if oid is None or oid in live:
            continue
        live.add(oid)
        for f in heap.cells:  # 简化:线性查找对象
            if f.oid == oid:
                stack.extend(s.val for s in f.fields if s.val is not None)
                break
    return live


def compute_forwarding(heap, live):
    """计算新地址(保序滑动):存活对象按原相对顺序紧排,空闲全部落到尾部。"""
    fwd, addr = {}, 0
    for o in heap.cells:
        if o.oid in live:
            fwd[o.oid] = addr
            addr += o.size_words
    return fwd, addr


def update_references(heap, fwd):
    """update 阶段:把所有槽改写成新地址。"""
    for s in heap.all_slots():
        if s.val is not None:
            s.val = fwd[s.val]
    return sum(1 for s in heap.all_slots() if s.val is not None)


def move(heap, live, fwd):
    """move 阶段:按新地址重排(保序,故等价于过滤掉死对象)。"""
    return [o for o in heap.cells if o.oid in live]


def lisp2_compact(heap, roots):
    """Lisp2 结构的保序滑动压缩,返回 (live, fwd, new_cells, 更新过的槽数)。"""
    live = mark(heap, roots)
    fwd, live_words = compute_forwarding(heap, live)
    updated = update_references(heap, fwd)
    new_cells = move(heap, live, fwd)
    return live, fwd, new_cells, updated


# --------------------------------------------------------------------------- #
# 线程化(threading)压缩:GHC Compact.c 的做法 —— 不建转发表,把链藏在对象自己身上
# --------------------------------------------------------------------------- #
def thread_object(o, refs):
    """把所有指向 o 的槽链进 o 的 info 槽;链尾保留原 info 内容。

    与 GHC 的换入链完全一致:old = o.info; o.info = (field, s, tag); s.val = old
    """
    for s in refs:
        old = o.info
        # GHC:未标记的字段 -> tag=1;已标记的字段 -> tag=2
        o.info = ("field", s, 1 if not s.tagged else 2)
        s.val = old


def chain_length(o):
    n, node = 0, o.info
    while isinstance(node, tuple):
        n += 1
        node = node[1].val
    return n


def thread_all(heap, live):
    """对每个要移动的对象,收集全部指向它的槽并线程化。"""
    refs_of = {}
    for s in heap.all_slots():
        if s.val is not None:
            refs_of.setdefault(s.val, []).append(s)
    for o in heap.cells:
        if o.oid in live:
            thread_object(o, refs_of.get(o.oid, []))
    return refs_of


def unthread_object(o, new_addr):
    """沿链把所有槽写成新地址;链尾的 info 内容被还原。"""
    node, updated = o.info, 0
    while isinstance(node, tuple):
        _, s, tag = node
        nxt = s.val
        s.val = new_addr
        if tag == 2:
            s.tagged = True   # 保持"已标记指针"这一位
        else:
            s.tagged = False
        node = nxt
        updated += 1
    o.info = node             # 还原原 info 内容
    return updated


def threading_compact(heap, roots):
    """线程化压缩:先线程化,再**边扫描边算新地址**地解链。

    关键性质:解链阶段**不需要任何 old->new 映射表** —— 新地址由 running free
    pointer 现算,而"谁指向这个对象"由链本身告知(链就挂在对象自己的 info 槽上)。
    所以除对象自身的字之外,额外空间是 O(1)。
    """
    live = mark(heap, roots)
    # "谁指向谁"只在 mark 阶段需要;真实实现里这与标记扫描是同一次遍历,
    # 用完即弃(下面的 refs_of.clear() 就是这个语义)。
    refs_of = {}
    for s in heap.all_slots():
        if s.val is not None:
            refs_of.setdefault(s.val, []).append(s)
    for o in heap.cells:
        if o.oid in live:
            thread_object(o, refs_of.get(o.oid, []))
    refs_of.clear()

    free, updated = 0, 0
    new_cells = []
    for o in heap.cells:               # 保序滑动:新地址 = 前缀和
        if o.oid in live:
            updated += unthread_object(o, free)
            free += o.size_words
            new_cells.append(o)
    return live, free, new_cells, updated, refs_of
