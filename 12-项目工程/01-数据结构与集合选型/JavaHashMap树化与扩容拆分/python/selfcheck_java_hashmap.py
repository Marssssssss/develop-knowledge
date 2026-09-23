"""661 Java HashMap 自检：期望值全部来自实读的 OpenJDK HashMap.java。"""

from __future__ import annotations

import sys

from java_hashmap import (
    DEFAULT_INITIAL_CAPACITY,
    DEFAULT_LOAD_FACTOR,
    INT_MAX,
    MAXIMUM_CAPACITY,
    MIN_TREEIFY_CAPACITY,
    TREEIFY_THRESHOLD,
    UNTREEIFY_THRESHOLD,
    JavaHashMap,
    Node,
    TreeBin,
    as_int32,
    index_for,
    next_capacity_after_resize,
    number_of_leading_zeros,
    spread,
    table_size_for,
)

PASS = 0


def check(cond, msg):
    global PASS
    assert cond, msg
    PASS += 1


# ------------------------------------------------------------- 一、常量
check(DEFAULT_INITIAL_CAPACITY == 16, "DEFAULT_INITIAL_CAPACITY = 1 << 4")
check(MAXIMUM_CAPACITY == 1 << 30, "MAXIMUM_CAPACITY = 1 << 30")
check(DEFAULT_LOAD_FACTOR == 0.75, "DEFAULT_LOAD_FACTOR = 0.75f")
check(TREEIFY_THRESHOLD == 8, "TREEIFY_THRESHOLD = 8")
check(UNTREEIFY_THRESHOLD == 6, "UNTREEIFY_THRESHOLD = 6")
check(MIN_TREEIFY_CAPACITY == 64, "MIN_TREEIFY_CAPACITY = 64")
check(TREEIFY_THRESHOLD > UNTREEIFY_THRESHOLD, "源码注释：退化阈值必须小于树化阈值")
check(MIN_TREEIFY_CAPACITY >= 4 * TREEIFY_THRESHOLD, "源码注释：至少 4 * TREEIFY_THRESHOLD")

# ------------------------------------------------- 二、tableSizeFor
# numberOfLeadingZeros(0) == 32，而 Java 移位量 & 31 ⇒ -1 >>> 32 就是 -1 >>> 0
check(number_of_leading_zeros(0) == 32, "Integer.numberOfLeadingZeros(0) == 32")
check(number_of_leading_zeros(1) == 31, "nlz(1) == 31")
check(number_of_leading_zeros(1 << 30) == 1, "nlz(1<<30) == 1")
for cap, want in [
    (0, 1), (1, 1), (2, 2), (3, 4), (4, 4), (5, 8), (7, 8), (8, 8),
    (9, 16), (15, 16), (16, 16), (17, 32), (31, 32), (32, 32),
    (63, 64), (64, 64), (65, 128), (1000, 1024), (1024, 1024), (1025, 2048),
]:
    check(table_size_for(cap) == want, f"tableSizeFor({cap}) 应为 {want}，实得 {table_size_for(cap)}")
check(table_size_for(MAXIMUM_CAPACITY) == MAXIMUM_CAPACITY, "tableSizeFor(1<<30) == 1<<30")
check(table_size_for(MAXIMUM_CAPACITY + 1) == MAXIMUM_CAPACITY, "超过 MAX 一律压回 MAX")
check(table_size_for(INT_MAX) == MAXIMUM_CAPACITY, "tableSizeFor(Integer.MAX_VALUE) == 1<<30")

# --------------------------------------------------------- 三、hash 扰动
check(spread(0) == 0, "hash 0 仍是 0")
check(spread(0x0000FFFF) == 0x0000FFFF, "高 16 位为 0 时扰动无效")
check(spread(as_int32(0xFFFF0000)) == -1, "0xFFFF0000 >>> 16 = 0xFFFF，异或得 0xFFFFFFFF = -1")
check(spread(0x12345678) == 0x1234444C, "0x5678 ^ 0x1234 = 0x444C")
check(spread(0x00010000) == 0x00010001, "只有第 16 位时，扰动把它折到第 0 位")
# 扰动只影响低 16 位：高 16 位原样保留
for h in (0x12345678, 0xDEADBEEF & 0xFFFFFFFF, 0x0F0F0F0F):
    check(spread(h) >> 16 == as_int32(h) >> 16, "扰动不改高 16 位")

# ---------------------------------------------------------- 四、槽位
check(index_for(0, 16) == 0, "(n-1) & hash")
check(index_for(1, 16) == 1, "hash 1 -> 槽 1")
check(index_for(17, 16) == 1, "hash 17 -> 槽 1（高位被 n-1 掩掉）")
check(index_for(0x12345678, 16) == spread(0x12345678) & 15 == 12,
      "index 用扰动后的 hash 再 & (n-1)")
# 扰动不是幂等的：把已扰动的值再扰动一次会回到原值的高位折叠
check(spread(spread(0x12345678)) == 0x12345678, "spread(spread(h)) 会折回原值（别重复扰动）")

# -------------------------------------------- 五、resize 的三分支
# 首次 put：oldCap=0、oldThr=threshold -> newCap = oldThr
check(next_capacity_after_resize(0, 16, 0.75) == (16, 12), "默认：newCap=16、newThr=(int)12")
check(next_capacity_after_resize(0, 4, 0.75) == (4, 3), "new HashMap<>(3)：cap=4、thr=(int)3")
check(next_capacity_after_resize(0, 1, 0.75) == (1, 0), "cap=1 时 (int)0.75 截断成 0")
# oldCap >= 16 时 newThr 直接翻倍
check(next_capacity_after_resize(16, 12, 0.75) == (32, 24), "16/12 -> 32/24")
check(next_capacity_after_resize(32, 24, 0.75) == (64, 48), "32/24 -> 64/48")
# oldCap < 16 时**不**翻倍，改成按 loadFactor 重算
check(next_capacity_after_resize(8, 6, 0.75) == (16, 12), "8/6 -> 16/12（重算也是 12）")
check(next_capacity_after_resize(4, 3, 0.75) == (8, 6), "4/3 -> 8/6")
check(next_capacity_after_resize(2, 1, 0.75) == (4, 3), "2/1 -> 4/3")
check(next_capacity_after_resize(1, 0, 0.75) == (2, 1), "1/0 -> 2/1（(int)1.5 = 1）")
# 非默认 loadFactor 才能看出"翻倍"与"重算"的差别
check(next_capacity_after_resize(16, 16, 1.0) == (32, 32), "lf=1.0：32/32")
check(next_capacity_after_resize(8, 8, 1.0) == (16, 16), "lf=1.0 且 oldCap<16：重算 16")
# 到顶：threshold 变成 Integer.MAX_VALUE，表不再动
check(next_capacity_after_resize(MAXIMUM_CAPACITY, 1 << 29, 0.75) == (MAXIMUM_CAPACITY, INT_MAX),
      "oldCap >= MAXIMUM_CAPACITY -> threshold = Integer.MAX_VALUE")
check(next_capacity_after_resize(1 << 29, 1 << 28, 0.75)[0] == MAXIMUM_CAPACITY,
      "1<<29 翻倍到 1<<30")

# ------------------------------------ 六、new HashMap<>(0) 的扩容链条
m = JavaHashMap(0)
check(m.threshold == 1 and m.capacity == 0, "tableSizeFor(0) == 1，表尚未分配")
chain: list[tuple[int, int]] = [(0, 1)]          # (cap, thr) 的初始态
m.put("a", 0)
chain.append((m.capacity, m.threshold))
for i in range(1, 12):
    m.put(f"k{i}", i)          # 散开落槽，避免触发树化分支干扰扩容观察
    chain.append((m.capacity, m.threshold))
# cap=1 时 (int)0.75 == 0 ⇒ 插一个就超阈值，于是连续抬： 2 -> 4 -> 8 -> 16
check(chain[1] == (2, 1), f"第 1 次 put 后应为 (2,1)，实得 {chain[1]}")
check(chain[2] == (4, 3), f"第 2 次 put 后应为 (4,3)，实得 {chain[2]}")
check(chain[3] == (4, 3), "第 3 次 put 不扩容（size 3 == threshold 3）")
check(chain[4] == (8, 6), f"第 4 次 put 后应为 (8,6)，实得 {chain[4]}")
check(chain[7] == (16, 12), f"第 7 次 put 后应为 (16,12)，实得 {chain[7]}")
caps = [c for c, _ in chain]
check(caps == sorted(caps), "容量单调不减")
check(caps[-1] == 16, "12 次 put 后稳定在 16 槽")

# ------------------------------- 七、默认构造：第 13 个元素触发扩容
m2 = JavaHashMap()
m2.put("x0", 0)
check((m2.capacity, m2.threshold) == (16, 12), "默认首次 put 得到 16/12")
for i in range(1, 12):
    m2.put(f"x{i}", i)         # 12 个互不碰撞
check(m2.capacity == 16 and m2.size == 12, "装到 12 个仍在 16 槽（size == threshold 不扩）")
m2.put("x12", 12)
check(m2.capacity == 32 and m2.threshold == 24, "第 13 个（size 13 > 12）扩容到 32/24")

# ------------------------------------------- 八、树化：表太小就先扩容
# 9 个 hash 都落在同一个槽：h = i*64（低 6 位为 0，对 16/32/64 槽表都落在槽 0）
m3 = JavaHashMap(16)          # 首次 put 得到 cap 16 / thr 12
m3.put("t0", 0)
check(m3.capacity == 16, "起手 16 槽")
for i in range(1, 8):
    m3.put(f"t{i}", i * 64)
check(m3.tree_bucket_count() == 0, "8 个节点还不树化（binCount=6 < 7）")
check(m3.bucket_sizes()[0] == 8, "8 个全在槽 0")
m3.put("t8", 8 * 64)
check(m3.tree_bucket_count() == 0, "表长 16 < MIN_TREEIFY_CAPACITY，走 resize 而不是树化")
check(m3.capacity == 32, "第 9 个节点触发的是 resize（16 -> 32）")

m4 = JavaHashMap(64)          # 首次 put 得到 cap 64 / thr 48
m4.put("u0", 0)
check(m4.capacity == 64, "起手 64 槽")
for i in range(1, 8):
    m4.put(f"u{i}", i * 64)
check(m4.tree_bucket_count() == 0, "8 个节点仍是链")
m4.put("u8", 8 * 64)
check(m4.tree_bucket_count() == 1, "表长 64 >= 64，第 9 个节点真正树化")
check(m4.bucket_sizes()[0] == 9, "树桶里 9 个节点")
check(m4.capacity == 64, "树化不伴随扩容")

# ------------------------- 九、TreeNode.split：只有拆成两半才重建树
# 造一棵 9 节点的树，hash 的 oldCap 位各不相同
mk = JavaHashMap(64)
mk.put("v0", 0)
for i in range(1, 8):
    mk.put(f"v{i}", i * 64)
mk.put("v8", 8 * 64)
check(mk.tree_bucket_count() == 1, "先造出一棵树")
# 现在把 8 个节点的 hash 换成低位为 0、oldCap(64) 位为 0 与 1 各半
nodes = [Node(64 * i, f"w{i}") for i in range(4)]          # 64*i & 64 == 0（i 偶数）不定，见下
# 直接构造：bit=64，取 hash = 0..3（bit 位为 0）与 hash = 64,65,66（bit 位为 1）
lo_part = [Node(1 + i, f"lo{i}") for i in range(7)]        # 7 个：& 64 == 0
hi_part = [Node(64 + 1 + i, f"hi{i}") for i in range(7)]   # 7 个：& 64 == 64
mk.table[5] = TreeBin(list(lo_part + hi_part))
mk.treeifies = 0
mk.untreeifies = 0
mk.resize()   # 64 -> 128，bit = 64
check(mk.capacity == 128, "resize 后 128 槽")
check(isinstance(mk.table[5], TreeBin), "lo 侧 7 > 6，仍是树")
check(isinstance(mk.table[5 + 64], TreeBin), "hi 侧 7 > 6，仍是树")
check(len(mk.table[5].nodes) == 7 and len(mk.table[5 + 64].nodes) == 7, "各 7 个")
check([n.key for n in mk.table[5].nodes] == [f"lo{i}" for i in range(7)], "lo 侧保持原顺序")
check(mk.treeifies == 2, "两侧都拆出来了 ⇒ 两次 treeify（lo 看 hi、hi 看 lo）")

# 全部落在同一侧：不 treeify
mk2 = JavaHashMap(64)
mk2.put("z", 0)
mk2.table[7] = TreeBin([Node(1 + i, f"p{i}") for i in range(9)])
mk2.treeifies = 0
mk2.untreeifies = 0
mk2.resize()
check(mk2.capacity == 128, "64 -> 128")
check(isinstance(mk2.table[7], TreeBin), "整体搬到槽 7，仍是树")
check(mk2.table[7 + 64] is None, "hi 侧为空")
check(mk2.treeifies == 0, "整棵树没被拆开 ⇒ 不调用 treeify（源码 if (hiHead != null)）")

# 拆开后某一侧 <= 6 ⇒ 退化成链
mk3 = JavaHashMap(64)
mk3.put("z", 0)
mk3.table[9] = TreeBin([Node(1 + i, f"q{i}") for i in range(8)] + [Node(64 + 1, "r0")])
mk3.untreeifies = 0
mk3.resize()
check(isinstance(mk3.table[9], TreeBin), "lo 侧 8 > 6，保持树")
check(not isinstance(mk3.table[9 + 64], TreeBin), "hi 侧 1 <= 6 ⇒ untreeify 成链")
check(mk3.untreeifies == 1, "发生一次退化")

# --------------------------------------- 十、链拆分保持相对顺序
mk4 = JavaHashMap(32)
mk4.put("s", 0)
seq = [Node(3, "c"), Node(32 + 5, "d"), Node(7, "e"), Node(32 + 9, "f"), Node(11, "g")]
mk4.table[2] = list(seq)
mk4.resize()
lo_keys = [n.key for n in mk4.table[2]] if mk4.table[2] else []
hi_keys = [n.key for n in mk4.table[2 + 32]] if mk4.table[2 + 32] else []
check(lo_keys == ["c", "e", "g"], f"lo 保持相对顺序，实得 {lo_keys}")
check(hi_keys == ["d", "f"], f"hi 保持相对顺序，实得 {hi_keys}")

# ------------------------------------- 十一、单节点桶直接按新掩码重定位
mk5 = JavaHashMap(32)
mk5.put("m", 0)
mk5.table[1] = [Node(32 + 1, "solo")]     # spread(33) & 63 == 33
mk5.resize()
check(mk5.table[33] is not None and mk5.table[33][0].key == "solo",
      "单节点桶跳过 lo/hi 拆分，直接 (n-1) & hash 落位")
check(mk5.table[1] is None, "原位清空")

print(f"OK: {PASS} assertions passed")
sys.exit(0)
