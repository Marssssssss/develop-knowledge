"""Cheney 半空间复制式 GC 的算法实现(可复用模块)。

权威来源(实际联网阅读):
  v8.dev/blog/trash-talk               —— V8 Orinoco 的 young generation Scavenger
  v8.dev/blog/orinoco-parallel-scavenger —— Cheney 半空间复制的完整描述

按'字'计账(1 字 = 一个指针槽),只保留算法的结构性质。
"""

WORD = 8
HEADER_WORDS = 2  # forward 字 + size 字


class Slot:
    """一个指针槽 —— 移动式 GC 必须能**改写**它,这就是"精确根"的含义。"""

    __slots__ = ("owner", "name", "val")

    def __init__(self, owner, name, val=None):
        self.owner, self.name, self.val = owner, name, val

    def __repr__(self):
        return f"<{self.owner or 'root'}.{self.name}={self.val}>"


class Obj:
    __slots__ = ("oid", "fields", "payload_words")

    def __init__(self, oid, payload_words=1):
        self.oid = oid
        self.fields = []
        self.payload_words = payload_words

    @property
    def words(self):
        return HEADER_WORDS + self.payload_words + len(self.fields)

    def slot(self, name):
        s = Slot(self.oid, name)
        self.fields.append(s)
        return s


class Chain:
    """一个可被 Cheney 复制的对象图。"""

    def __init__(self, size_words):
        self.size_words = size_words
        self.objs = {}
        self.roots = []
        self.stats = {"alloc_words": 0, "scan_words": 0, "copied": 0, "forward_hits": 0}

    def add(self, oid, payload_words=1):
        o = Obj(oid, payload_words)
        self.objs[oid] = o
        return o

    def root(self, name, oid):
        s = Slot("root", name, oid)
        self.roots.append(s)
        return s

    def edge(self, a, name):
        return self.objs[a].slot(name)


def cheney(src):
    """执行一次 Cheney 半空间复制。返回 (to_space, forward, trace)。

    trace 记录每一步的 alloc/scan 指针 —— Cheney 不需要递归栈,是因为它把
    "待扫描队列"直接编码成 to_space 上的 [scan, alloc) 区间。
    """
    to_space = []   # [(offset_words, Obj)],按复制顺序
    forward = {}    # old oid -> new oid
    next_oid = [1000]
    alloc = [0]
    scan = [0]
    trace = []

    def evacuate(slot):
        old = slot.val
        if old is None:
            return
        if old in forward:
            src.stats["forward_hits"] += 1
            slot.val = forward[old]           # 转发指针命中:只改引用,不再复制
            trace.append(("forward", old, forward[old], alloc[0], scan[0]))
            return
        o = src.objs[old]
        new = Obj(next_oid[0], o.payload_words)
        next_oid[0] += 1
        to_space.append((alloc[0], new))
        forward[old] = new.oid
        slot.val = new.oid
        for f in o.fields:                    # 槽对象整体搬过去,槽值稍后由 scan 改写
            new.slot(f.name).val = f.val
        src.stats["alloc_words"] += o.words
        src.stats["copied"] += 1
        alloc[0] += o.words
        trace.append(("copy", old, new.oid, alloc[0], scan[0]))

    for s in src.roots:
        evacuate(s)
    while scan[0] < alloc[0]:
        cur = None
        for off, o in to_space:
            if off == scan[0]:
                cur = o
                break
        if cur is None:
            raise AssertionError(f"scan={scan[0]} 未对齐到对象起点")
        src.stats["scan_words"] += cur.words
        for f in cur.fields:
            evacuate(f)
        scan[0] += cur.words
        trace.append(("scan", cur.oid, None, alloc[0], scan[0]))
    return to_space, forward, trace


