"""662 Go map 自检：期望值全部来自实读的 internal/runtime/maps 三份源码。"""

from __future__ import annotations

import sys

from go_swissmap import (
    CTRL_DELETED,
    CTRL_EMPTY,
    MAP_GROUP_SLOTS,
    MAX_AVG_GROUP_LOAD,
    MAX_TABLE_CAPACITY,
    GoMap,
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

PASS = 0


def check(cond, msg):
    global PASS
    assert cond, msg
    PASS += 1


# -------------------------------------------------------------- 一、常量
check(MAP_GROUP_SLOTS == 8, "abi.MapGroupSlots == 8")
check(MAX_TABLE_CAPACITY == 1024, "maxTableCapacity == 1024（源码注释：Completely made up value）")
check(MAX_AVG_GROUP_LOAD == 7, "maxAvgGroupLoad == 7，即 7/8 负载（与 Abseil 一致）")
check(CTRL_EMPTY == 0x80, "ctrlEmpty == 0b10000000")
check(CTRL_DELETED == 0xFE, "ctrlDeleted == 0b11111110")
# 注意：Go 的哨兵与 hashbrown 相反（见 663），但与 Abseil 相同
check(CTRL_EMPTY != 0xFF and CTRL_DELETED != 0x80, "Go 用 0x80/0xFE，不是 hashbrown 的 0xFF/0x80")

# ---------------------------------------------------------- 二、h1 / h2
check(h1(0xFF) == 1, "h1 = hash >> 7")
check(h1(0x7F) == 0, "低 7 位不进 h1")
check(h2(0xFF) == 0x7F, "h2 = hash & 0x7f")
check(h2(0x1FF) == 0x7F, "h2 只看低 7 位")
big = (1 << 64) - 1
check(h1(big) == (1 << 57) - 1, "h1 是**高 57 位**")
check(h2(big) == 0x7F, "h2 是低 7 位")

# ------------------------------------------------- 三、容量与 growthLeft
check(align_up_pow2(1) == 1 and align_up_pow2(3) == 4 and align_up_pow2(1023) == 1024,
      "alignUpPow2 向上取 2 的幂")
check(new_table_capacity(1) == 8, "newTable 下限是 MapGroupSlots")
check(new_table_capacity(9) == 16, "9 -> 16")
check(new_table_capacity(100) == 128, "100 -> 128")
check(new_table_capacity(1024) == 1024, "1024 正好是上限")
try:
    new_table_capacity(2048)
    raise AssertionError("超过 maxTableCapacity 应 panic")
except ValueError:
    PASS += 1

# 单组表留一个空槽终止探测；大表按 7/8
check(max_growth_left(8) == 7, "单组表 growthLeft = capacity - 1 = 7（必须留空槽）")
check(max_growth_left(16) == 14, "16 槽 -> 16*7/8 = 14")
check(max_growth_left(1024) == 896, "1024 槽 -> 896")
check(max_growth_left(64) == 56, "64 槽 -> 56")
for cap_ in (16, 32, 64, 128, 256, 512, 1024):
    check(max_growth_left(cap_) * 8 == cap_ * 7, f"{cap_} 槽的 growthLeft 严格等于 7/8")

# ------------------------------------------------- 四、NewMap(hint) 推导
lay = new_map_layout(8)
check(lay["small"] is True and lay["dir_len"] == 0, "hint <= 8 走单组小图，dirLen == 0")
lay = new_map_layout(9)
check(lay["small"] is False, "hint 9 已经是完整图")
check(lay["target_capacity"] == (9 * 8) // 7 == 10, "targetCapacity = hint*8/7 = 10")
check(lay["dir_len"] == 1 and lay["global_depth"] == 0 and lay["global_shift"] == 64,
      "dirSize=1 -> globalDepth=0")
check(lay["table_capacity"] == 16, "每张表 10 -> 向上取到 16")
lay = new_map_layout(1000)
check(lay["target_capacity"] == 1142, f"1000*8/7 = 1142，实得 {lay['target_capacity']}")
check(lay["dir_len"] == 2 and lay["global_depth"] == 1, "ceil(1142/1024)=2 -> globalDepth=1")
check(lay["table_capacity"] == 1024, "1142/2 = 571 -> newTable 取到 1024")
lay = new_map_layout(10000)
check(lay["target_capacity"] == 11428, "10000*8/7 = 11428")
check(lay["dir_len"] == 16 and lay["global_depth"] == 4,
      f"ceil(11428/1024)=12 -> 向上取到 16，实得 dir_len={lay['dir_len']}")
check(lay["global_shift"] == 60, "globalShift = 64 - 4 = 60")

# ------------------------------------------------------ 五、directoryIndex
check(directory_index(0xFFFFFFFFFFFFFFFF, 1, 64) == 0, "dirLen == 1 时恒为 0")
check(directory_index(0xFFFFFFFFFFFFFFFF, 4, 62) == 3, "globalShift=62 时取最高 2 位")
check(directory_index(0, 4, 62) == 0, "hash 0 -> 目录项 0")
mid = 1 << 63
check(directory_index(mid, 4, 62) == 2, "最高两位是 10 -> 目录项 2")
check(local_depth_mask(1) == 1 << 63, "localDepthMask(1) = 1 << (64-1)")
check(local_depth_mask(4) == 1 << 60, "localDepthMask(4) = 1 << 60")
check(local_depth_mask(3, use64=False) == 1 << 29, "32 位哈希时是 1 << (32-3)")

# --------------------------------------------------------- 六、探测序列
for groups in (2, 4, 8, 16, 64):
    mask = groups - 1
    seq = list(probe_groups(0, mask, groups))
    check(len(set(seq)) == groups, f"组数 {groups}：三角探测应恰好访问每个组一次")
    check(set(seq) == set(range(groups)), f"组数 {groups}：三角探测是组下标的一个排列")
# 三角推进：p(i) = h1 + i(i+1)/2
seq = list(probe_groups(0, 7, 5))
check(seq == [0, 1, 3, 6, 2], f"三角数序列应为 [0,1,3,6,2]，实得 {seq}")
seq2 = list(probe_groups(5, 15, 4))
check(seq2 == [5, 6, 8, 11], f"起始 5 的三角推进，实得 {seq2}")

# -------------------------------------------------------- 七、单组小图
m = GoMap(0)
check(m.small is True, "hint 0 -> 小图")
for i in range(8):
    m.put(f"k{i}", (i + 1) * 0x101)
check(m.used == 8 and m.small is True, "8 个槽全部能被填满（小图没有墓碑、不留空槽）")
m.put("k8", 9 * 0x101)
check(m.small is False, "第 9 个触发 growToTable")
check(m.snapshot()["capacity"] == 16, f"growToTable 分配 2*MapGroupSlots = 16 槽，实得 {m.snapshot()['capacity']}")
check(m.dir_len_actual == 1 and m.global_depth == 0, "目录长度 1、globalDepth 0")

# --------------------------------------- 八、grow：16 -> 32 -> ... -> 1024
m2 = GoMap(0)
sizes = []
for i in range(2000):
    m2.put(f"x{i}", (i * 0x9E3779B97F4A7C15) & ((1 << 64) - 1))
    cap_ = m2.snapshot()["capacity"]
    if not sizes or sizes[-1] != cap_:
        sizes.append(cap_)
check(sizes[:3] == [8, 16, 32], f"容量序列前三个应为 8,16,32，实得 {sizes[:3]}")
check(1024 in sizes, "容量会经过 1024")
check(all(c <= 1024 for c in sizes[:8]), "在拆分之前单表不超过 1024")

# ------------------------------ 九、1024 之后：split 而不是继续翻倍
t = Table(1024, 0, 3)
check(t.capacity == 1024 and t.growth_left == 896, "1024 槽表 growthLeft = 896")
gm = GoMap(0)
gm.dir = [t]
gm.dir_len = 1
gm.global_depth = 3
gm.small = False
# 填满 896 个
for i in range(896):
    t.unchecked_put(f"f{i}", (i * 0x9E3779B97F4A7C15) & ((1 << 64) - 1))
check(t.growth_left == 0 and t.used == 896, "恰好 896 个把 growthLeft 用光")
check(gm.total_tombstones() == 0, "没有墓碑")
t.rehash(gm)
check(gm.splits == 1, "2*1024 > maxTableCapacity ⇒ 走 split 而不是 grow")
check(len(gm.dir) == 2, f"目录翻倍到 2，实得 {len(gm.dir)}")
check(gm.global_depth == 4, "split 时 localDepth == globalDepth ⇒ 目录深度 +1")
check(gm.dir_doublings == 1, "发生一次目录翻倍")
left, right = gm.dir[0], gm.dir[1]
check(left is not right, "拆成了两张不同的表")
check(left.capacity == 1024 and right.capacity == 1024, "两张新表都是 maxTableCapacity")
check(left.local_depth == 4 and right.local_depth == 4, "localDepth 都 +1")
check(left.used + right.used == 896, "元素不增不减")
check(left.index == 0 and right.index == 1, "分裂后各自占据一个目录项")
# 拆分依据的是 localDepthMask(4) = 1<<60 那一位
mask = local_depth_mask(4)
for tab in (left, right):
    for item in tab.slots:
        if item is None:
            continue
        _, hv = item
        if tab is left:
            check(hv & mask == 0, "左表的 hash 该位必须是 0")
        else:
            check(hv & mask != 0, "右表的 hash 该位必须是 1")

# ------------------------- 十、删除：组里还有空槽就不产生墓碑
t2 = Table(16, 0, 0)
check(t2.growth_left == 14, "16 槽 -> growthLeft 14")
# 只填 1 个：组里必然还有空槽
t2.unchecked_put("a", 0x1234)
before_gl = t2.growth_left
made_tomb = t2.delete("a", 0x1234)
check(made_tomb is False, "组里还有空槽 ⇒ 置 EMPTY，不留墓碑")
check(t2.growth_left == before_gl + 1, "并且把 growthLeft 还回来了")
check(t2.tombstones() == 0, "没有墓碑")
check(t2.used == 0, "used 减到 0")

# 单组表（capacity == 8）最多只装 7 个，组里**恒有**空槽 ⇒ 永远不会产生墓碑
t3 = Table(8, 0, 0)
for i in range(7):
    t3.unchecked_put(f"g{i}", (i + 1) * 0x400)
check(t3.growth_left == 0, "7 个插满")
made = t3.delete("g0", 0x400)
check(made is False, "单组表恒留空槽 ⇒ 删除走 EMPTY 分支，不留墓碑")
check(t3.tombstones() == 0, "一个墓碑都没有")
check(t3.growth_left == 1, "growthLeft 被还回来 1")

# 要造墓碑必须让**整个组**满：造一张 16 槽（2 组）的表，把组 0 填满
t3b = Table(16, 0, 0)
# hash = (h1 << 7) | tag：h1 = 0 ⇒ 起始组恒为 0；tag 取 1..8 互不相等
for j in range(8):
    t3b.unchecked_put(f"h{j}", (j + 1))
check(t3b.slots[7] is not None and t3b.slots[8] is None, "8 个都落在组 0")
check(t3b.ctrl[7] != CTRL_EMPTY, "组 0 满了")
gl_before = t3b.growth_left
made = t3b.delete("h0", 1)
check(made is True, "组里没有空槽 ⇒ 留墓碑")
check(t3b.tombstones() == 1, "产生 1 个墓碑")
check(t3b.growth_left == gl_before, "墓碑**不**归还 growthLeft")
# 复用墓碑不消耗配额（PutSlot 里 growthLeft++ 紧接着 --，净效果为 0）
gl2 = t3b.growth_left
ok = t3b.put("new", 0x7F01)
check(ok is True and t3b.used == 8, "复用墓碑插入成功")
check(t3b.tombstones() == 0, "墓碑被覆盖掉")
check(t3b.growth_left == gl2, "复用墓碑不消耗 growthLeft")

# --------------------------------------- 十一、pruneTombstones 的 10% 门槛
# 源码：`if t.tombstones()*10 < t.capacity { return }`，且只清"组里有空槽"的那些墓碑
def make_tombstone_table(groups: int) -> Table:
    t = Table(1024, 0, 3)
    for g in range(groups):                       # 每组 7 个墓碑 + 1 个空槽
        base = g * MAP_GROUP_SLOTS
        for j in range(7):
            t.ctrl[base + j] = CTRL_DELETED
        t.ctrl[base + 7] = CTRL_EMPTY
    return t

t4 = make_tombstone_table(5)                      # 35 个墓碑
check(t4.tombstones() == 35, "造出 35 个墓碑")
check(t4.prune_tombstones() == 0, "35*10 = 350 < 1024 ⇒ prune 直接放弃")
check(t4.tombstones() == 35, "放弃时一个都没清")

t5 = make_tombstone_table(25)                     # 175 个墓碑
check(t5.tombstones() == 175, "造出 175 个墓碑")
check(175 * 10 >= t5.capacity, "175*10 = 1750 >= 1024，达到门槛")
removed = t5.prune_tombstones()
check(removed == 175, f"一次清光 175 个，实得 {removed}")
check(t5.tombstones() == 0, "清完没有残留")
check(t5.growth_left == max_growth_left(1024) + 175, "每清一个归还一格 growthLeft")

# -------------------------------------------- 十二、h2 冲突只影响比较次数
t5 = Table(8, 0, 0)
# 两个 hash 不同但 h2 相同的 key：tag 相同 ⇒ 同组内需要逐个比 key
ha, hb = 0x01, 0x81          # h2 都是 0x01
check(h2(ha) == h2(hb), "构造同 h2 的两个 hash")
t5.unchecked_put("A", ha)
t5.unchecked_put("B", hb)
check(t5.used == 2, "两个 key 都进去了")
t5.delete("A", ha)
t5.delete("B", hb)
check(t5.used == 0, "两个都能删掉（靠 key 比较区分）")

print(f"OK: {PASS} assertions passed")
sys.exit(0)
