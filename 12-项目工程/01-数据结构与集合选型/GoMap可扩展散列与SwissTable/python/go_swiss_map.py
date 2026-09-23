"""GoMap：目录 + 全局深度 + 小图快路径（拆分自 go_swissmap.py 的 662 模型）。"""

from __future__ import annotations

from go_swiss_table import (
    MAP_GROUP_SLOTS,
    MAX_TABLE_CAPACITY,
    Table,
    new_map_layout,
    new_table_capacity,
)

_U64 = (1 << 64) - 1

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
