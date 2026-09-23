"""662 的兼容入口：把 table 与 map 两个模块的名字原样导出（历史上是一个文件）。"""

from __future__ import annotations

from go_swiss_map import GoMap
from go_swiss_table import (
    CTRL_DELETED,
    CTRL_EMPTY,
    MAP_GROUP_SLOTS,
    MAX_AVG_GROUP_LOAD,
    MAX_TABLE_CAPACITY,
    Table,
    align_up_pow2,
    directory_index,
    h1,
    h2,
    local_depth_mask,
    max_growth_left,
    new_map_layout,
    new_table_capacity,
    probe_groups,
)

__all__ = [
    "GoMap", "Table", "CTRL_EMPTY", "CTRL_DELETED", "MAP_GROUP_SLOTS",
    "MAX_TABLE_CAPACITY", "MAX_AVG_GROUP_LOAD", "align_up_pow2", "directory_index",
    "h1", "h2", "local_depth_mask", "max_growth_left", "new_map_layout",
    "new_table_capacity", "probe_groups",
]
