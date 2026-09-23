"""OpenJDK `java.util.HashMap` 的树化 / 退化 / 扩容拆分模型。

逐行对齐 OpenJDK jdk master 的
`src/java.base/share/classes/java/util/HashMap.java`（100 KB 实读）：

- 六个常量：`DEFAULT_INITIAL_CAPACITY=16`、`MAXIMUM_CAPACITY=1<<30`、
  `DEFAULT_LOAD_FACTOR=0.75f`、`TREEIFY_THRESHOLD=8`、`UNTREEIFY_THRESHOLD=6`、
  `MIN_TREEIFY_CAPACITY=64`；
- `tableSizeFor(cap)` 的 `-1 >>> numberOfLeadingZeros(cap-1)` —— 注意 Java 的
  移位量会 **`& 31`**，`numberOfLeadingZeros(0) = 32` 于是 `-1 >>> 32` 实际是 `-1 >>> 0`；
- `hash(key) = h ^ (h >>> 16)`（无符号右移）、槽位 `= hash & (n-1)`；
- `resize()` 的三分支与 `newThr` 只在 `oldCap >= 16` 时才"翻倍"的细节；
- `split` 的 `(e.hash & oldCap) == 0` 二分配（保持相对顺序）；
- `TreeNode.split` 里 **只有拆成了两半才需要重新 treeify**（`if (hiHead != null)`）。
"""

from __future__ import annotations

DEFAULT_INITIAL_CAPACITY = 1 << 4          # 16
MAXIMUM_CAPACITY = 1 << 30
DEFAULT_LOAD_FACTOR = 0.75
TREEIFY_THRESHOLD = 8
UNTREEIFY_THRESHOLD = 6
MIN_TREEIFY_CAPACITY = 64

INT_MIN = -(1 << 31)
INT_MAX = (1 << 31) - 1
_U32 = (1 << 32) - 1


def as_int32(x: int) -> int:
    """把任意整数折成 Java int（有符号 32 位）。"""
    x &= _U32
    return x - (1 << 32) if x >= (1 << 31) else x


def number_of_leading_zeros(x: int) -> int:
    """`Integer.numberOfLeadingZeros`：0 返回 32。"""
    x &= _U32
    if x == 0:
        return 32
    return 32 - x.bit_length()


def table_size_for(cap: int) -> int:
    """`static final int tableSizeFor(int cap)`。

    关键：Java 移位量取 `& 31`，所以 `numberOfLeadingZeros(0) == 32` 时
    `-1 >>> 32` 等价于 `-1 >>> 0 == -1`，落在 `n < 0` 分支返回 1。
    Python 里必须显式 `& 31`，否则 `>> 32` 直接得到 0，与 Java 不符。
    """
    if cap <= 0:
        cap = 1
    cap = as_int32(cap)
    n_minus = as_int32(as_int32(cap) - 1)
    nlz = number_of_leading_zeros(n_minus)
    n = as_int32((-1 & _U32) >> (nlz & 31))   # -1 >>> nlz
    if n < 0:
        return 1
    if n >= MAXIMUM_CAPACITY:
        return MAXIMUM_CAPACITY
    return as_int32(n + 1)


def spread(h: int) -> int:
    """`hash(key)`：`h ^ (h >>> 16)` —— 高 16 位折叠进低 16 位。"""
    h = as_int32(h)
    return as_int32((h & _U32) ^ ((h & _U32) >> 16))


def index_for(h: int, n: int) -> int:
    """`tab[i = (n - 1) & hash]`。"""
    return spread(h) & (n - 1)


def next_capacity_after_resize(old_cap: int, old_thr: int, load_factor: float) -> tuple[int, int]:
    """复刻 `resize()` 里 newCap / newThr 的推导（不含搬移）。

    三分支：
      1. oldCap > 0：
         - oldCap >= MAXIMUM_CAPACITY → threshold = Integer.MAX_VALUE，表不再动
         - 否则 newCap = oldCap << 1；**只有 oldCap >= 16 时** newThr = oldThr << 1
      2. oldCap == 0 且 oldThr > 0（构造时把 initialCapacity 塞进了 threshold）→ newCap = oldThr
      3. 全默认 → newCap = 16、newThr = 12
    最后若 newThr 仍为 0：`newThr = (int)(newCap * loadFactor)`（**截断**，不是四舍五入）。
    """
    new_thr = 0
    if old_cap > 0:
        if old_cap >= MAXIMUM_CAPACITY:
            return old_cap, INT_MAX
        new_cap = as_int32(old_cap << 1)
        if new_cap < MAXIMUM_CAPACITY and old_cap >= DEFAULT_INITIAL_CAPACITY:
            new_thr = as_int32(old_thr << 1)
    elif old_thr > 0:
        new_cap = old_thr
    else:
        new_cap = DEFAULT_INITIAL_CAPACITY
        new_thr = int(DEFAULT_LOAD_FACTOR * DEFAULT_INITIAL_CAPACITY)
    if new_thr == 0:
        ft = float(new_cap) * load_factor
        new_thr = int(ft) if new_cap < MAXIMUM_CAPACITY and ft < MAXIMUM_CAPACITY else INT_MAX
    return new_cap, new_thr


class Node:
    __slots__ = ("hash", "key", "value")

    def __init__(self, hash_value: int, key: object, value: object = None):
        self.hash = hash_value
        self.key = key
        self.value = value

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Node({self.key!r})"


class JavaHashMap:
    """只建模影响"扩容 / 树化 / 拆分"的那部分状态。

    桶有两种形态：`list[Node]`（链）与 `TreeBin(list[Node])`（树，但保留 next 链顺序，
    这与 `TreeNode` 同时维护 prev/next 与红黑链接的事实一致）。
    """

    def __init__(self, initial_capacity: int | None = None,
                 load_factor: float = DEFAULT_LOAD_FACTOR):
        """`initial_capacity is None` 对应**无参构造器** `new HashMap()`：
        它只设 `loadFactor`，`threshold` 保持 0，于是首次 resize 走"全默认"分支得到 16/12。
        传了数字（含 0）则对应 `new HashMap(int)`，`threshold = tableSizeFor(cap)`。
        """
        if load_factor <= 0:
            raise ValueError("loadFactor must be > 0")
        self.load_factor = load_factor
        self.table: list[list[Node] | None] | None = None
        self.threshold = 0 if initial_capacity is None else table_size_for(initial_capacity)
        self.size = 0
        self.resizes = 0
        self.treeifies = 0
        self.untreeifies = 0

    # -- 基本量 -----------------------------------------------------------
    @property
    def capacity(self) -> int:
        return 0 if self.table is None else len(self.table)

    def is_tree(self, bin_nodes) -> bool:
        return isinstance(bin_nodes, TreeBin)

    # -- resize -----------------------------------------------------------
    def resize(self) -> None:
        old_cap = self.capacity
        old_tab = self.table
        new_cap, new_thr = next_capacity_after_resize(old_cap, self.threshold, self.load_factor)
        if old_cap >= MAXIMUM_CAPACITY:
            self.threshold = INT_MAX
            return
        self.threshold = new_thr
        new_tab: list[list[Node] | None] = [None] * new_cap
        if old_tab is not None:
            for j, bucket in enumerate(old_tab):
                if bucket is None:
                    continue
                nodes = bucket.nodes if isinstance(bucket, TreeBin) else bucket
                if len(nodes) == 1:
                    new_tab[spread(nodes[0].hash) & (new_cap - 1)] = [nodes[0]]
                elif isinstance(bucket, TreeBin):
                    self._split_tree(new_tab, bucket, j, old_cap)
                else:
                    lo, hi = self._split_chain(nodes, old_cap)
                    if lo:
                        new_tab[j] = lo
                    if hi:
                        new_tab[j + old_cap] = hi
        self.table = new_tab
        self.resizes += 1

    @staticmethod
    def _split_chain(nodes: list[Node], bit: int) -> tuple[list[Node], list[Node]]:
        """`resize()` 里的 lo/hi 拆分：`(e.hash & oldCap) == 0` 留在 j，否则去 j+oldCap。

        注意判据用的是 **spread 之后的 hash**，且**保持链表原有顺序**（尾插到 loTail/hiTail）。
        """
        lo: list[Node] = []
        hi: list[Node] = []
        for e in nodes:
            if spread(e.hash) & bit == 0:
                lo.append(e)
            else:
                hi.append(e)
        return lo, hi

    def _split_tree(self, new_tab, tree: "TreeBin", index: int, bit: int) -> None:
        """`TreeNode.split`：分两堆后各自决定"退树"还是"建树"。

        源码里只有 **`hiHead != null`** 时才 `loHead.treeify(tab)` —— 若整棵树都落在
        同一侧，红黑结构原样搬过去仍然合法，**不需要重建**；`hi` 侧对称地看 `loHead`。
        """
        lo, hi = self._split_chain(tree.nodes, bit)
        if lo:
            if len(lo) <= UNTREEIFY_THRESHOLD:
                new_tab[index] = lo
                self.untreeifies += 1
            else:
                new_tab[index] = TreeBin(lo)
                if hi:
                    self.treeifies += 1
        if hi:
            if len(hi) <= UNTREEIFY_THRESHOLD:
                new_tab[index + bit] = hi
                self.untreeifies += 1
            else:
                new_tab[index + bit] = TreeBin(hi)
                if lo:
                    self.treeifies += 1

    # -- put --------------------------------------------------------------
    def treeify_bin(self, tab, hash_value: int) -> str:
        """`treeifyBin`：表长 < 64 时**先扩容而不是树化**。"""
        n = len(tab)
        if n < MIN_TREEIFY_CAPACITY:
            return "resize"
        return "treeify"

    def put(self, key: object, hash_value: int, value: object = None) -> None:
        if self.table is None:
            self.resize()
        tab = self.table
        n = len(tab)
        i = spread(hash_value) & (n - 1)
        bucket = tab[i]
        if bucket is None:
            tab[i] = [Node(hash_value, key, value)]
        else:
            nodes = bucket.nodes if isinstance(bucket, TreeBin) else bucket
            for e in nodes:                       # 已存在则覆盖
                if e.hash == hash_value and e.key == key:
                    e.value = value
                    return
            nodes.append(Node(hash_value, key, value))
            bin_count = len(nodes) - 2            # putVal 里 binCount 从 0 起算
            if bin_count >= TREEIFY_THRESHOLD - 1:
                if self.treeify_bin(tab, hash_value) == "resize":
                    self.resize()
                else:
                    tab[i] = TreeBin(nodes)
                    self.treeifies += 1
        self.size += 1
        if self.size > self.threshold:
            self.resize()

    # -- 观察 -------------------------------------------------------------
    def bucket_sizes(self) -> list[int]:
        if self.table is None:
            return []
        return [0 if b is None else len(b.nodes if isinstance(b, TreeBin) else b)
                for b in self.table]

    def tree_bucket_count(self) -> int:
        if self.table is None:
            return 0
        return sum(1 for b in self.table if isinstance(b, TreeBin))

    def snapshot(self) -> dict:
        return {
            "capacity": self.capacity,
            "threshold": self.threshold,
            "size": self.size,
            "resizes": self.resizes,
            "trees": self.tree_bucket_count(),
            "treeifies": self.treeifies,
            "untreeifies": self.untreeifies,
        }


class TreeBin:
    """只保留 `next` 链顺序与"是不是树"这一位信息。"""

    __slots__ = ("nodes",)

    def __init__(self, nodes: list[Node]):
        self.nodes = nodes

    def __len__(self) -> int:
        return len(self.nodes)
