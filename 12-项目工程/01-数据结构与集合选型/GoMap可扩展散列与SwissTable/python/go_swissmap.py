"""Go 1.24+ `map` 的可扩展散列目录 + Swiss table 模型。

Go 1.24 把 map 换成了 swiss table，实现从 `runtime/map.go` 迁到了
`internal/runtime/maps/`（本 demo 依据的是 golang/go master 的这三份原文）：

- `internal/runtime/maps/table.go`：`maxTableCapacity = 1024`、`table` 的四个计数器
  （`used` / `capacity` / `growthLeft` / `localDepth`）、`maxGrowthLeft`、`rehash`、`split`、`grow`；
- `internal/runtime/maps/map.go`：`Map.directory`、`globalDepth` / `globalShift`、
  `directoryIndex`、`NewMap(hint)` 的容量推导、`installTableSplit`、小图快路径
  `putSlotSmall` / `growToSmall` / `growToTable`；
- `internal/runtime/maps/group.go`：`maxAvgGroupLoad = 7`、`ctrlEmpty = 0x80`、
  `ctrlDeleted = 0xFE`、`bitset*` 掩码。

一句话概括：**一张表最多 1024 槽，超了不整体翻倍，而是拆成两张并按需把目录翻倍** ——
这就是"增量扩容"：任何一次插入最多搬一张表（1024 槽），而不是整张 2^n 的表。
"""

from __future__ import annotations

MAP_GROUP_SLOTS = 8            # abi.MapGroupSlots
MAX_TABLE_CAPACITY = 1024
MAX_AVG_GROUP_LOAD = 7         # 负载因子 7/8

CTRL_EMPTY = 0x80
CTRL_DELETED = 0xFE

_U64 = (1 << 64) - 1


# --------------------------------------------------------------- 哈希切分
def h1(h: int) -> int:
    """`func h1(h uintptr) uintptr { return h >> 7 }` —— 高 57 位，用来选组。"""
    return (h & _U64) >> 7


def h2(h: int) -> int:
    """`func h2(h uintptr) uintptr { return h & 0x7f }` —— 低 7 位，当控制字节。"""
    return h & 0x7F


# --------------------------------------------------------------- 容量推导
def align_up_pow2(n: int) -> int:
    """`alignUpPow2`：向上取到 2 的幂（group 数必须是 2 的幂，probeSeq 才能遍历所有组）。"""
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def new_table_capacity(requested: int) -> int:
    """`newTable`：下限 `MapGroupSlots`，上限 `maxTableCapacity`，再向上取 2 的幂。"""
    cap_ = max(requested, MAP_GROUP_SLOTS)
    if cap_ > MAX_TABLE_CAPACITY:
        raise ValueError("initial table capacity too large")
    return align_up_pow2(cap_)


def max_growth_left(capacity: int) -> int:
    """`table.maxGrowthLeft`：这张表还能插多少个才需要 rehash。

    源码两分支：
      - `capacity <= MapGroupSlots`（单组表）：`capacity - 1` —— 必须留一个空槽终止探测
      - 否则：`(capacity * maxAvgGroupLoad) / MapGroupSlots` = `capacity * 7 / 8`
    """
    if capacity == 0:
        raise ValueError("table must have positive capacity")
    if capacity <= MAP_GROUP_SLOTS:
        return capacity - 1
    return (capacity * MAX_AVG_GROUP_LOAD) // MAP_GROUP_SLOTS


def new_map_layout(hint: int) -> dict:
    """`NewMap(mt, hint, ...)` 的容量推导（不含分配）。

    - `hint <= MapGroupSlots`：**不分配**，走单组小图（`dirLen == 0`）
    - 否则 `targetCapacity = (hint * MapGroupSlots) / maxAvgGroupLoad`（即 `hint * 8/7`），
      `dirSize = ceil(targetCapacity / maxTableCapacity)` 再向上取 2 的幂
    - `globalDepth = TrailingZeros64(dirSize)`、`globalShift = 64 - globalDepth`
    - 每张表的初始容量 = `targetCapacity / dirSize`（再走 newTable 的取整）
    """
    if hint <= MAP_GROUP_SLOTS:
        return {"small": True, "dir_len": 0, "global_depth": 0,
                "global_shift": 64, "table_capacity": 0, "target_capacity": 0}
    target = (hint * MAP_GROUP_SLOTS) // MAX_AVG_GROUP_LOAD
    dir_size = (target + MAX_TABLE_CAPACITY - 1) // MAX_TABLE_CAPACITY
    dir_size = align_up_pow2(max(dir_size, 1))
    global_depth = (dir_size & -dir_size).bit_length() - 1   # TrailingZeros64
    per_table = target // dir_size
    return {
        "small": False,
        "dir_len": dir_size,
        "global_depth": global_depth,
        "global_shift": 64 - global_depth,
        "table_capacity": new_table_capacity(per_table),
        "target_capacity": target,
    }


def directory_index(hash_value: int, dir_len: int, global_shift: int) -> int:
    """`Map.directoryIndex`：`dirLen == 1` 时直接返回 0，否则 `hash >> globalShift`。"""
    if dir_len == 1:
        return 0
    return (hash_value & _U64) >> (global_shift & 63)


def local_depth_mask(local_depth: int, use64: bool = True) -> int:
    """`localDepthMask`：`1 << (64 - localDepth)`（32 位哈希时是 `1 << (32 - localDepth)`）。"""
    bits = 64 if use64 else 32
    return 1 << (bits - local_depth)


# --------------------------------------------------------------- 探测序列
def probe_groups(h1_value: int, groups_mask: int, limit: int | None = None):
    """`probeSeq` 的组下标序列。

    源码：
        offset = h1 & mask
        next(): index++; offset = (offset + index) & mask
    即三角数推进 `p(i) = h1 + i(i+1)/2 (mod mask+1)`。组数是 2 的幂时它是个双射，
    所以**每个组恰好被访问一次**。
    """
    n = groups_mask + 1 if limit is None else limit
    offset = h1_value & groups_mask
    index = 0
    for _ in range(n):
        yield offset
        index += 1
        offset = (offset + index) & groups_mask


class Table:
    """`internal/runtime/maps.table` 的建模：控制字节 + 槽位 + 四个计数器。"""

    def __init__(self, capacity: int, index: int = 0, local_depth: int = 0):
        self.capacity = new_table_capacity(capacity)
        self.ctrl = [CTRL_EMPTY] * self.capacity
        self.slots: list[tuple[object, int] | None] = [None] * self.capacity
        self.used = 0
        self.growth_left = max_growth_left(self.capacity)
        self.local_depth = local_depth
        self.index = index
        self.group_count = self.capacity // MAP_GROUP_SLOTS

    # -- 观察 -----------------------------------------------------------
    @property
    def groups_mask(self) -> int:
        return self.group_count - 1

    def tombstones(self) -> int:
        return sum(1 for c in self.ctrl if c == CTRL_DELETED)

    def slot_ctrl(self, j: int) -> int:
        return self.ctrl[j]

    # -- rehash / grow / split -----------------------------------------
    def rehash(self, m: "GoMap") -> None:
        """`table.rehash`：`newCapacity = 2 * capacity`，能装下就 grow，否则 split。"""
        new_capacity = 2 * self.capacity
        if new_capacity <= MAX_TABLE_CAPACITY:
            self.grow(m, new_capacity)
        else:
            self.split(m)

    def grow(self, m: "GoMap", new_capacity: int) -> None:
        """`table.grow`：新表继承 index / localDepth，元素重新散列进去。"""
        new_table = Table(new_capacity, self.index, self.local_depth)
        for item in self.slots:
            if item is None:
                continue
            new_table.unchecked_put(item[0], item[1])
        m.replace_table(new_table)
        self.index = -1

    def split(self, m: "GoMap") -> None:
        """`table.split`：localDepth+1，按该位把元素分到左右两张 1024 槽表。"""
        local_depth = self.local_depth + 1
        left = Table(MAX_TABLE_CAPACITY, -1, local_depth)
        right = Table(MAX_TABLE_CAPACITY, -1, local_depth)
        mask = local_depth_mask(local_depth)
        for item in self.slots:
            if item is None:
                continue
            key, hash_value = item
            target = left if (hash_value & mask) == 0 else right
            target.unchecked_put(key, hash_value)
        m.install_table_split(self, left, right)
        self.index = -1

    # -- 基本操作 -------------------------------------------------------
    def unchecked_put(self, key: object, hash_value: int) -> None:
        """`uncheckedPutSlot`：grow / split 时用，已知 key 不在表里。"""
        tag = h2(hash_value)
        for gi in probe_groups(h1(hash_value), self.groups_mask, self.group_count):
            base = gi * MAP_GROUP_SLOTS
            for j in range(base, base + MAP_GROUP_SLOTS):
                if self.ctrl[j] == CTRL_EMPTY:
                    self.ctrl[j] = tag
                    self.slots[j] = (key, hash_value)
                    self.used += 1
                    self.growth_left -= 1
                    return
        raise RuntimeError("no empty slot")

    def put(self, key: object, hash_value: int) -> bool:
        """`table.PutSlot`：返回 True 表示落位成功，False 表示触发了 rehash（调用方重试）。"""
        tag = h2(hash_value)
        first_deleted = -1
        for gi in probe_groups(h1(hash_value), self.groups_mask, self.group_count):
            base = gi * MAP_GROUP_SLOTS
            # 1) 先看有没有同 tag 且同 key 的槽
            for j in range(base, base + MAP_GROUP_SLOTS):
                if self.ctrl[j] == tag and self.slots[j] is not None and self.slots[j][0] == key:
                    return True
            # 2) 记下第一个墓碑
            if first_deleted < 0:
                for j in range(base, base + MAP_GROUP_SLOTS):
                    if self.ctrl[j] == CTRL_DELETED:
                        first_deleted = j
                        break
            # 3) 有空槽 ⇒ 探测序列到此结束
            for j in range(base, base + MAP_GROUP_SLOTS):
                if self.ctrl[j] == CTRL_EMPTY:
                    target = first_deleted if first_deleted >= 0 else j
                    if first_deleted >= 0:
                        self.growth_left += 1   # 紧接着的 -- 变成空操作
                    if self.growth_left == 0:
                        self.prune_tombstones()
                    if self.growth_left > 0:
                        self.ctrl[target] = tag
                        self.slots[target] = (key, hash_value)
                        self.growth_left -= 1
                        self.used += 1
                        return True
                    return False
        return False

    def delete(self, key: object, hash_value: int) -> bool:
        """`table.Delete`：组里还有空槽就直接置 EMPTY 并归还 growthLeft，否则留墓碑。"""
        tag = h2(hash_value)
        for gi in probe_groups(h1(hash_value), self.groups_mask, self.group_count):
            base = gi * MAP_GROUP_SLOTS
            for j in range(base, base + MAP_GROUP_SLOTS):
                if self.ctrl[j] == tag and self.slots[j] is not None and self.slots[j][0] == key:
                    self.used -= 1
                    self.slots[j] = None
                    group_has_empty = any(
                        self.ctrl[k] == CTRL_EMPTY for k in range(base, base + MAP_GROUP_SLOTS)
                    )
                    if group_has_empty:
                        self.ctrl[j] = CTRL_EMPTY
                        self.growth_left += 1
                        return False          # 没有产生墓碑
                    self.ctrl[j] = CTRL_DELETED
                    return True               # 产生了墓碑
            # 组里有空槽 ⇒ 探测结束
            if any(self.ctrl[k] == CTRL_EMPTY for k in range(base, base + MAP_GROUP_SLOTS)):
                return False
        return False

    def prune_tombstones(self) -> int:
        """`pruneTombstones`：墓碑不足容量 10% 时直接放弃（宁可 grow 也不做亏本扫描）。"""
        if self.tombstones() * 10 < self.capacity:
            return 0
        removed = 0
        for gi in range(self.group_count):
            base = gi * MAP_GROUP_SLOTS
            if any(self.ctrl[k] == CTRL_EMPTY for k in range(base, base + MAP_GROUP_SLOTS)):
                for j in range(base, base + MAP_GROUP_SLOTS):
                    if self.ctrl[j] == CTRL_DELETED:
                        self.ctrl[j] = CTRL_EMPTY
                        self.growth_left += 1
                        removed += 1
        return removed


class GoMap:
    """`internal/runtime/maps.Map`：目录 + 全局深度 + 小图快路径。"""

    def __init__(self, hint: int = 0):
        layout = new_map_layout(hint)
        self.small = layout["small"]
        self.dir: list[Table] = []
        self.global_depth = layout["global_depth"]
        self.dir_len = layout["dir_len"]
        self.global_shift = layout["global_shift"]
        self.used = 0
        self.grows = 0
        self.splits = 0
        self.dir_doublings = 0
        self.pruned = 0
        if not self.small:
            self._init_directory(new_table_capacity(layout["table_capacity"]))
        else:
            self.small_slots: list[tuple[object, int] | None] = [None] * MAP_GROUP_SLOTS

    def _init_directory(self, table_capacity: int) -> None:
        for i in range(self.dir_len):
            self.dir.append(Table(table_capacity, i, self.global_depth))

    @property
    def dir_len_actual(self) -> int:
        return 1 if self.dir_len == 0 else len(self.dir)

    def global_shift_actual(self) -> int:
        return 64 - self.global_depth

    def directory_index(self, hash_value: int) -> int:
        if len(self.dir) == 1:
            return 0
        return (hash_value & _U64) >> (self.global_shift_actual() & 63)

    def directory_at(self, i: int) -> Table:
        return self.dir[i]

    def replace_table(self, nt: Table) -> None:
        """`Map.replaceTable`：一个 table 可能占据连续 `2**(globalDepth-localDepth)` 个目录项。"""
        entries = 1 << (self.global_depth - nt.local_depth)
        for k in range(entries):
            self.dir[nt.index + k] = nt

    def install_table_split(self, old: Table, left: Table, right: Table) -> None:
        """`Map.installTableSplit`：目录不够深就先翻倍，再放左右两张表。"""
        if old.local_depth == self.global_depth:
            new_dir: list[Table] = []
            for i in range(len(self.dir)):
                t = self.dir[i]
                new_dir.append(t)
                new_dir.append(t)
                if t.index == i:
                    t.index = 2 * i
            self.dir = new_dir
            self.global_depth += 1
            self.dir_doublings += 1
        left.index = old.index
        self.replace_table(left)
        entries = 1 << (self.global_depth - left.local_depth)
        right.index = left.index + entries
        self.replace_table(right)
        self.splits += 1

    # -- 插入 -----------------------------------------------------------
    def put(self, key: object, hash_value: int) -> None:
        if self.small:
            self._put_small(key, hash_value)
            return
        for _ in range(64):
            idx = self.directory_index(hash_value)
            table = self.directory_at(idx)
            if table.put(key, hash_value):
                self.used += 1
                return
            before = (len(self.dir), self.global_depth)
            table.rehash(self)
            if (len(self.dir), self.global_depth) == before:
                self.grows += 1
        raise RuntimeError("put did not settle")

    def _put_small(self, key: object, hash_value: int) -> None:
        """`putSlotSmall`：单组 8 槽，**没有墓碑**，8 个槽全部可用。"""
        for i, item in enumerate(self.small_slots):
            if item is not None and item[0] == key:
                return
        for i, item in enumerate(self.small_slots):
            if item is None:
                self.small_slots[i] = (key, hash_value)
                self.used += 1
                return
        # 满了：`growToTable` 分配 **2 * MapGroupSlots = 16** 槽的表
        self._grow_to_table()

    def _grow_to_table(self) -> None:
        self.small = False
        t = Table(2 * MAP_GROUP_SLOTS, 0, 0)
        for item in self.small_slots:
            if item is not None:
                t.unchecked_put(item[0], item[1])
        self.dir = [t]
        self.dir_len = 1
        self.global_depth = 0

    # -- 删除 -----------------------------------------------------------
    def delete(self, key: object, hash_value: int) -> bool:
        if self.small:
            for i, item in enumerate(self.small_slots):
                if item is not None and item[0] == key:
                    self.small_slots[i] = None
                    self.used -= 1
                    return True
            return False
        idx = self.directory_index(hash_value)
        ok = self.directory_at(idx).delete(key, hash_value)
        if self.used > 0:
            self.used -= 1
        return ok

    # -- 观察 -----------------------------------------------------------
    def tables(self) -> list[Table]:
        seen: list[Table] = []
        for t in self.dir:
            if t not in seen:
                seen.append(t)
        return seen

    def total_capacity(self) -> int:
        return sum(t.capacity for t in self.tables())

    def total_tombstones(self) -> int:
        return sum(t.tombstones() for t in self.tables())

    def snapshot(self) -> dict:
        return {
            "small": self.small,
            "dir_len": 0 if self.small else len(self.dir),
            "global_depth": self.global_depth,
            "global_shift": 0 if self.small else self.global_shift_actual(),
            "tables": 0 if self.small else len(self.tables()),
            "capacity": MAP_GROUP_SLOTS if self.small else self.total_capacity(),
            "used": self.used,
            "tombstones": self.total_tombstones(),
            "grows": self.grows,
            "splits": self.splits,
            "dir_doublings": self.dir_doublings,
        }
