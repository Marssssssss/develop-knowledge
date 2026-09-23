"""662 演示入口：小图快路径 → grow → split → 目录翻倍的全过程。"""

from __future__ import annotations

from go_swissmap import (
    CTRL_DELETED,
    CTRL_EMPTY,
    MAP_GROUP_SLOTS,
    MAX_AVG_GROUP_LOAD,
    MAX_TABLE_CAPACITY,
    GoMap,
    Table,
    align_up_pow2,
    h1,
    h2,
    local_depth_mask,
    max_growth_left,
    new_map_layout,
    new_table_capacity,
    probe_groups,
)

GOLDEN = 0x9E3779B97F4A7C15
U64 = (1 << 64) - 1


def mix(i: int) -> int:
    """确定性伪随机：用黄金比例常数打散，避免断言受随机源影响。"""
    return (i * GOLDEN) & U64


def show_constants() -> None:
    print("== 常量 ==")
    print(f"  MapGroupSlots={MAP_GROUP_SLOTS}  maxAvgGroupLoad={MAX_AVG_GROUP_LOAD}"
          f"  maxTableCapacity={MAX_TABLE_CAPACITY}")
    print(f"  ctrlEmpty=0x{CTRL_EMPTY:02X}  ctrlDeleted=0x{CTRL_DELETED:02X}")
    print("  （Go 与 Abseil 用同一组哨兵；Rust hashbrown 是反过来的 0xFF / 0x80，见 663）")


def show_hash_split() -> None:
    print("\n== h1 / h2 ==")
    for h in (0x00, 0x7F, 0x80, 0xFF, 0x1FF, U64):
        print(f"  hash=0x{h:016X} -> h1=0x{h1(h):014X} ({h1(h)})  h2=0x{h2(h):02X}")


def show_capacity() -> None:
    print("\n== 单表容量与 growthLeft ==")
    for cap_ in (8, 16, 64, 256, 1024):
        print(f"  capacity={cap_:<6} growthLeft={max_growth_left(cap_):<6}"
              f"  ({max_growth_left(cap_)}/{cap_} = {max_growth_left(cap_) / cap_:.3f})")
    print(f"  newTable: 1 -> {new_table_capacity(1)}, 9 -> {new_table_capacity(9)},"
          f" 100 -> {new_table_capacity(100)}, 700 -> {new_table_capacity(700)}")


def show_new_map() -> None:
    print("\n== NewMap(hint) 的目录推导 ==")
    for hint in (8, 9, 100, 1000, 10000, 100000):
        lay = new_map_layout(hint)
        if lay["small"]:
            print(f"  hint={hint:<8} 单组小图（dirLen=0，8 槽全可用）")
        else:
            print(f"  hint={hint:<8} target={lay['target_capacity']:<8} dirLen={lay['dir_len']:<4}"
                  f" globalDepth={lay['global_depth']} globalShift={lay['global_shift']}"
                  f" 每表 {lay['table_capacity']} 槽")


def show_probe() -> None:
    print("\n== 三角探测（组下标）==")
    for groups in (8, 16):
        print(f"  组数 {groups:>3} 从 h1=0 起:", list(probe_groups(0, groups - 1, 8)))
    print("  源码 next(): index++; offset = (offset + index) & mask —— 步长 1,2,3,4,…")


def show_lifecycle() -> None:
    print("\n== 一次完整生命周期：小图 -> grow -> split -> 目录翻倍 ==")
    m = GoMap(0)
    prev = m.snapshot()
    print(f"  start   {prev}")
    for i in range(1, 2600):
        m.put(f"k{i}", mix(i))
        s = m.snapshot()
        if (s["capacity"], s["dir_len"], s["global_depth"]) != (
            prev["capacity"], prev["dir_len"], prev["global_depth"]
        ):
            print(f"  第 {i:>4} 个 {s}")
            prev = s
    print(f"  终态    {m.snapshot()}")
    print(f"  localDepthMask(globalDepth) = 1 << {64 - m.global_depth}")


def show_tombstone() -> None:
    print("\n== 墓碑：只有『整组都满』时删除才产生墓碑 ==")
    t = Table(8, 0, 0)
    for i in range(7):
        t.unchecked_put(f"g{i}", (i + 1) * 0x400)
    t.delete("g0", 0x400)
    print(f"  单组表(8槽) 删 1 个 -> 墓碑={t.tombstones()} growthLeft={t.growth_left}"
          "   （单组表恒留一个空槽，永不产生墓碑）")
    t2 = Table(16, 0, 0)
    for j in range(8):
        t2.unchecked_put(f"h{j}", j + 1)      # h1 = 0，全落组 0
    gl = t2.growth_left
    t2.delete("h0", 1)
    print(f"  16 槽表 组0 填满后删 1 个 -> 墓碑={t2.tombstones()} "
          f"growthLeft {gl} -> {t2.growth_left}   （墓碑不归还配额）")
    t2.put("new", 0x7F01)
    print(f"  再插 1 个复用墓碑 -> 墓碑={t2.tombstones()} growthLeft={t2.growth_left}")


def show_split() -> None:
    print("\n== 1024 槽表满了之后：split 而不是继续翻倍 ==")
    t = Table(1024, 0, 3)
    gm = GoMap(0)
    gm.small = False
    gm.dir = [t]
    gm.global_depth = 3
    for i in range(896):
        t.unchecked_put(f"f{i}", mix(i))
    print(f"  拆分前: dirLen={len(gm.dir)} globalDepth={gm.global_depth} "
          f"capacity={gm.total_capacity()} used={t.used} growthLeft={t.growth_left}")
    t.rehash(gm)
    print(f"  拆分后: dirLen={len(gm.dir)} globalDepth={gm.global_depth} "
          f"capacity={gm.total_capacity()} 表数={len(gm.tables())} "
          f"splits={gm.splits} 目录翻倍={gm.dir_doublings}")
    print(f"  分裂位 localDepthMask(4) = 1 << {64 - 4} = 0x{local_depth_mask(4):X}")


if __name__ == "__main__":
    show_constants()
    show_hash_split()
    show_capacity()
    show_new_map()
    show_probe()
    show_lifecycle()
    show_tombstone()
    show_split()
