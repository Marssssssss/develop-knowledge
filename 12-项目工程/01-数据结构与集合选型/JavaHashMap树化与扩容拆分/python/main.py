"""661 演示入口：把 tableSizeFor、resize 链条、树化门槛、split 的三种结局跑出来。"""

from __future__ import annotations

from java_hashmap import (
    MAXIMUM_CAPACITY,
    MIN_TREEIFY_CAPACITY,
    TREEIFY_THRESHOLD,
    UNTREEIFY_THRESHOLD,
    JavaHashMap,
    Node,
    TreeBin,
    next_capacity_after_resize,
    spread,
    table_size_for,
)


def show_table_size_for() -> None:
    print("== tableSizeFor(cap) = -1 >>> numberOfLeadingZeros(cap-1) ==")
    for cap in (0, 1, 2, 3, 5, 7, 8, 9, 16, 17, 63, 64, 65, 1000, MAXIMUM_CAPACITY):
        print(f"  {cap:>12} -> {table_size_for(cap)}")
    print("  注意：nlz(0)=32，而 Java 的移位量取 & 31 ⇒ -1>>>32 就是 -1>>>0 = -1 ⇒ 返回 1")


def show_spread() -> None:
    print("\n== hash 扰动 h ^ (h >>> 16) ==")
    for h in (0x0000FFFF, 0x00010000, 0x12345678, 0xFFFF0000):
        print(f"  0x{h & 0xFFFFFFFF:08X} -> 0x{spread(h) & 0xFFFFFFFF:08X}")


def show_resize_chain() -> None:
    print("\n== resize 的 newCap / newThr（loadFactor=0.75）==")
    cap, thr = 0, 0
    for _ in range(6):
        cap, thr = next_capacity_after_resize(cap, thr, 0.75)
        print(f"  cap={cap:<12} thr={thr}")
    print(f"  oldCap >= MAXIMUM_CAPACITY({MAXIMUM_CAPACITY}) 之后：")
    print(f"  -> {next_capacity_after_resize(MAXIMUM_CAPACITY, 1 << 29, 0.75)}  (threshold 顶到 Integer.MAX_VALUE)")


def show_new_hashmap_zero() -> None:
    print("\n== new HashMap<>(0)：threshold 被算成 1，于是 cap=1 时 thr=(int)0.75=0 ==")
    m = JavaHashMap(0)
    print(f"  初始: cap={m.capacity} thr={m.threshold}")
    for i in range(1, 9):
        m.put(f"k{i}", i)
        print(f"  第 {i:>2} 次 put: cap={m.capacity:<4} thr={m.threshold:<4} size={m.size}")


def show_treeify_gate() -> None:
    print(f"\n== 树化的两道门：TREEIFY_THRESHOLD={TREEIFY_THRESHOLD} 与 MIN_TREEIFY_CAPACITY={MIN_TREEIFY_CAPACITY} ==")
    m = JavaHashMap(16)
    m.put("t0", 0)
    for i in range(1, 9):
        m.put(f"t{i}", i * 64)
    print(f"  16 槽表 + 9 个同槽 key: cap={m.capacity} 树桶={m.tree_bucket_count()} resizes={m.resizes}")
    print("  -> 表长 16 < 64，第 9 个节点换来的是一次 resize，不是红黑树")

    m2 = JavaHashMap(64)
    m2.put("u0", 0)
    for i in range(1, 9):
        m2.put(f"u{i}", i * 64)
    print(f"  64 槽表 + 9 个同槽 key: cap={m2.capacity} 树桶={m2.tree_bucket_count()} treeifies={m2.treeifies}")
    print("  -> 表长够大才真的树化")


def show_split_outcomes() -> None:
    print(f"\n== TreeNode.split 的三种结局（UNTREEIFY_THRESHOLD={UNTREEIFY_THRESHOLD}）==")

    # 1) 拆成两半，两侧都 > 6：两侧都 treeify
    m = JavaHashMap(64)
    m.put("z", 0)
    m.table[5] = TreeBin([Node(1 + i, f"lo{i}") for i in range(7)]
                         + [Node(64 + 1 + i, f"hi{i}") for i in range(7)])
    m.treeifies = m.untreeifies = 0
    m.resize()
    print(f"  [7 | 7]  cap={m.capacity}  lo 是树={isinstance(m.table[5], TreeBin)}"
          f"  hi 是树={isinstance(m.table[5 + 64], TreeBin)}  treeify 次数={m.treeifies}")

    # 2) 全落一侧：一次 treeify 都不做
    m2 = JavaHashMap(64)
    m2.put("z", 0)
    m2.table[7] = TreeBin([Node(1 + i, f"p{i}") for i in range(9)])
    m2.treeifies = m2.untreeifies = 0
    m2.resize()
    print(f"  [9 | 0]  cap={m2.capacity}  仍是树={isinstance(m2.table[7], TreeBin)}"
          f"  hi 为空={m2.table[7 + 64] is None}  treeify 次数={m2.treeifies}")

    # 3) 拆出的一侧 <= 6：退化成链
    m3 = JavaHashMap(64)
    m3.put("z", 0)
    m3.table[9] = TreeBin([Node(1 + i, f"q{i}") for i in range(8)] + [Node(65, "r0")])
    m3.treeifies = m3.untreeifies = 0
    m3.resize()
    print(f"  [8 | 1]  lo 是树={isinstance(m3.table[9], TreeBin)}"
          f"  hi 是树={isinstance(m3.table[9 + 64], TreeBin)}  untreeify 次数={m3.untreeifies}")


if __name__ == "__main__":
    show_table_size_for()
    show_spread()
    show_resize_chain()
    show_new_hashmap_zero()
    show_treeify_gate()
    show_split_outcomes()
