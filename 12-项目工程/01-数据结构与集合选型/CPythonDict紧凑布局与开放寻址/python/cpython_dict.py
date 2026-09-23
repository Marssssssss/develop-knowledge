"""CPython dict 紧凑布局 + 开放寻址探测的可执行模型。

逐行对齐 CPython main 分支的 `Objects/dictobject.c` 与 `Include/internal/pycore_dict.h`：

- 索引表 `dk_indices` 里放的是 **entries 数组下标**（不是 key/value 指针），
  每一项只占 1/2/4/8 字节（`get_log2_bytes` 决定），因此索引表是"紧凑"的；
- `dk_entries` 按**插入顺序**追加，所以 dict 的迭代顺序 = 插入顺序；
- 探测序列是 `i = (i*5 + perturb + 1) & mask` 且每轮先 `perturb >>= PERTURB_SHIFT`（5）；
- 删除只把索引位改成 `DKIX_DUMMY`、把 entry 的 me_key/me_value 置空，
  **dk_usable 不回增**（源码注释：`We can't dk_usable++ since there is DKIX_DUMMY in indices`），
  因此"删一半再插"同样会触发扩容 —— 这与"删除会腾出空间"的直觉相反；
- `dictresize` 会**压实** entries（跳过 `me_value == NULL` 的洞）并重建索引，
  所以扩容后的表**可能比原来小**。
"""

from __future__ import annotations

PERTURB_SHIFT = 5
PYDICT_LOG_MINSIZE = 3
PYDICT_MINSIZE = 1 << PYDICT_LOG_MINSIZE  # 8

DKIX_EMPTY = -1
DKIX_DUMMY = -2

# 64 位无符号掩码：CPython 里 perturb 是 size_t，右移是逻辑右移
_MASK64 = (1 << 64) - 1


def usable_fraction(n: int) -> int:
    """`#define USABLE_FRACTION(n) (((n) << 1)/3)` —— 最大装载率 2/3。"""
    return (n << 1) // 3


def calculate_log2_keysize(minsize: int) -> int:
    """找最小的 `dk_size = 2**k >= minsize`（且不小于 PyDict_MINSIZE）。

    源码：`minsize = Py_MAX(minsize, PyDict_MINSIZE); return _Py_bit_length(minsize - 1);`
    注意 `bit_length(minsize - 1)` 而不是 `bit_length(minsize)`：
    8 -> 3（而不是 4），正好落在 2 的幂上。
    """
    if minsize < PYDICT_MINSIZE:
        minsize = PYDICT_MINSIZE
    return (minsize - 1).bit_length()


def estimate_log2_keysize(n: int) -> int:
    """`USABLE_FRACTION` 的反函数：预留能装下 n 个 entry 且**不需要再扩容**的表。

    源码：`calculate_log2_keysize((n*3 + 1) / 2)`。
    """
    return calculate_log2_keysize((n * 3 + 1) // 2)


def growth_rate(used: int) -> int:
    """`#define GROWTH_RATE(d) ((d)->ma_used*3)`。

    历史：3.2 之前 used*4；3.3.0 used*2；3.4.0~3.6.0 used*2 + capacity/2。
    取 used*3 的动机是"无删除时表翻倍，删除量与插入量相当时留更多余量"。
    """
    return used * 3


def get_log2_bytes(log2_size: int) -> int:
    """索引表总字节数的 log2。源码分四档：

        log2_size < 8   -> log2_size      （每槽 1 字节）
        8  <=  ..  < 16 -> log2_size + 1  （每槽 2 字节）
        16 <=  ..  < 32 -> log2_size + 2  （每槽 4 字节）
        >= 32          -> log2_size + 3  （每槽 8 字节）

    64 位才有最后一档（`#if SIZEOF_VOID_P > 4`）。
    """
    if log2_size < PYDICT_LOG_MINSIZE:
        raise ValueError("dk_size must be >= PyDict_MINSIZE")
    if log2_size < 8:
        return log2_size
    if log2_size < 16:
        return log2_size + 1
    if log2_size >= 32:
        return log2_size + 3
    return log2_size + 2


def index_bytes_per_slot(log2_size: int) -> int:
    """每个索引槽占多少字节 = 2**(log2_bytes - log2_size)。"""
    return 1 << (get_log2_bytes(log2_size) - log2_size)


def probe_indices(hash_value: int, dk_size: int, limit: int | None = None):
    """复刻 `lookdict` / `find_empty_slot` 的探测序列。

    起始：`i = hash & mask`（**不加 perturb**）；
    递推：先 `perturb >>= PERTURB_SHIFT`，再 `i = (i*5 + perturb + 1) & mask`。
    perturb 初值是完整 hash（当作无符号 64 位），无限右移后终会归零，
    此时退化为纯 `5*j+1` 递推 —— 而它在 2**i 上生成**全部**整数，故必然找到空槽。
    """
    mask = dk_size - 1
    i = (hash_value & _MASK64) & mask
    perturb = hash_value & _MASK64
    n = 0
    stop = dk_size if limit is None else limit
    while n < stop:
        yield i
        n += 1
        perturb >>= PERTURB_SHIFT
        i = (i * 5 + perturb + 1) & mask


class CompactDict:
    """把 CPython 组合表（combined dict）的四个计数器都显式建模出来。

    - `indices`：`dk_indices`，元素 ∈ {DKIX_EMPTY, DKIX_DUMMY} ∪ {entry 下标}
    - `entries`：`dk_entries`，元素是 `(key, hash)` 或 `None`（被删的洞）
    - `dk_usable`：**剩余可插入槽位**，只在 `new_keys_object` 里赋成 `USABLE_FRACTION(size)`
      并在每次插入 -1；删除**不**回增
    - `dk_nentries`：entries 数组已用掉的尾部位置（含洞）
    - `ma_used`：真正的存活条目数
    """

    def __init__(self, log2_size: int = PYDICT_LOG_MINSIZE, unicode_only: bool = True):
        self.log2_size = calculate_log2_keysize(1 << log2_size)
        self.indices = [DKIX_EMPTY] * (1 << self.log2_size)
        self.entries: list[tuple[object, int] | None] = []
        self.dk_usable = usable_fraction(1 << self.log2_size)
        self.dk_nentries = 0
        self.ma_used = 0
        self.resizes = 0
        self.unicode_only = unicode_only
        self.generic = not unicode_only

    # -- 基本量 -----------------------------------------------------------
    @property
    def dk_size(self) -> int:
        return 1 << self.log2_size

    @property
    def capacity(self) -> int:
        """`USABLE_FRACTION(dk_size)`：这张表最多能装多少 entry。"""
        return usable_fraction(self.dk_size)

    def index_bytes(self) -> int:
        return index_bytes_per_slot(self.log2_size)

    # -- 探测 -------------------------------------------------------------
    def _find_empty_slot(self, hash_value: int) -> int:
        """`find_empty_slot`：跳过 DUMMY 与已占，停在第一个 EMPTY。"""
        for i in probe_indices(hash_value, self.dk_size):
            if self.indices[i] == DKIX_EMPTY:
                return i
        raise RuntimeError("table has no empty slot")

    def _lookup_slot(self, hash_value: int) -> int:
        """返回 entry 下标（找到）或 DKIX_EMPTY（撞到空槽即停）。"""
        for i in probe_indices(hash_value, self.dk_size):
            ix = self.indices[i]
            if ix >= 0:
                return ix
            if ix == DKIX_EMPTY:
                return DKIX_EMPTY
        raise RuntimeError("table has no empty slot")

    # -- 变更 -------------------------------------------------------------
    def _build_indices(self) -> None:
        """`build_indices_*`：按 entry 下标顺序重新散列，用的是**同一套**探测式。"""
        self.indices = [DKIX_EMPTY] * self.dk_size
        for ix, item in enumerate(self.entries):
            if item is None:
                continue
            _, h = item
            for i in probe_indices(h, self.dk_size):
                if self.indices[i] == DKIX_EMPTY:
                    self.indices[i] = ix
                    break

    def _resize(self, minsize: int) -> None:
        """`dictresize`：压实 entries + 重建索引 + 重排两个计数器。"""
        log2_newsize = calculate_log2_keysize(minsize)
        alive = [e for e in self.entries if e is not None]
        self.log2_size = log2_newsize
        self.entries = alive
        self.dk_nentries = len(alive)
        self.dk_usable = usable_fraction(self.dk_size) - len(alive)
        self._build_indices()
        self.resizes += 1
        assert self.dk_usable >= 0, "new table must be large enough"

    def insert(self, key: object, hash_value: int) -> None:
        """复刻 `insert_combined_dict` 的顺序：先判非 unicode key，再判 usable，再落位。"""
        if self.unicode_only and not isinstance(key, str):
            raise TypeError("unicode-only table cannot hold non-str key")
        if self.dk_usable <= 0:
            self._resize(growth_rate(self.ma_used))
        i = self._find_empty_slot(hash_value)
        self.indices[i] = self.dk_nentries
        self.entries.append((key, hash_value))
        self.dk_nentries += 1
        self.dk_usable -= 1
        self.ma_used += 1

    def lookup(self, key: object, hash_value: int):
        ix = self._lookup_slot(hash_value)
        if ix >= 0 and self.entries[ix] is not None and self.entries[ix][0] == key:
            return self.entries[ix]
        return None

    def delete(self, key: object, hash_value: int) -> bool:
        """`delitem_common` 的合并表分支：置 DUMMY、清 entry、used--，其余一概不动。"""
        for i in probe_indices(hash_value, self.dk_size):
            ix = self.indices[i]
            if ix >= 0:
                item = self.entries[ix]
                if item is not None and item[0] == key:
                    self.indices[i] = DKIX_DUMMY
                    self.entries[ix] = None
                    self.ma_used -= 1
                    return True
            elif ix == DKIX_EMPTY:
                return False
        return False

    # -- 观察 -------------------------------------------------------------
    def iteration_order(self) -> list[object]:
        """dict 的迭代顺序 = entries 数组顺序（洞被跳过）。"""
        return [e[0] for e in self.entries if e is not None]

    def dummy_count(self) -> int:
        return sum(1 for x in self.indices if x == DKIX_DUMMY)

    def snapshot(self) -> dict[str, int]:
        return {
            "log2_size": self.log2_size,
            "dk_size": self.dk_size,
            "capacity": self.capacity,
            "dk_usable": self.dk_usable,
            "dk_nentries": self.dk_nentries,
            "ma_used": self.ma_used,
            "dummies": self.dummy_count(),
            "index_bytes": self.index_bytes(),
            "resizes": self.resizes,
        }


def documented_probe_order(dk_size: int = 8) -> list[int]:
    """源码注释里那张表：`0 -> 1 -> 6 -> 7 -> 4 -> 5 -> 2 -> 3 -> 0`。

    条件是 hash 的低 log2(size) 位为 0 且 perturb 初值为 0（即 hash == 0）。
    """
    return list(probe_indices(0, dk_size))
