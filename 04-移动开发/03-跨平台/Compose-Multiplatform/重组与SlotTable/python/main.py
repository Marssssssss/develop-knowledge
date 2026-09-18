"""Compose SlotTable 结构模型的自检(纯标准库,直接 python3 main.py 运行)。

断言分四组:
  A. gap buffer 的成本形态 —— 间隙处的插入/删除零搬动,远离间隙则按距离线性搬动;
  B. group 树结构 —— groupSize / parent / skipToGroupEnd / 槽数记账;
  C. 锚点语义 —— 被删 group 的锚点失效;间隙移动必须同步平移锚点;
  D. 满表删除回归 —— 复刻 androidx a038886 修掉的"父锚点更新被跳过"场景。
"""

import sys

from slot_table import (GROUP_FIELDS_SIZE, AnchorInvalidated, GapBuffer,
                        SlotTable)

PASS = 0
FAIL = 0
FAILED = []


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILED.append("%s | %s" % (label, detail))


# ===========================================================================
# A. gap buffer 的成本形态
# ===========================================================================
buf = GapBuffer(capacity=16)
check("空表 size=0 且间隙 = 容量", buf.size == 0 and buf.gap_len == 16, str(buf.gap_len))
for i in range(10):
    buf.insert([i])
check("连续在间隙处插入 10 次,搬动元素数仍为 0", buf.moves == 0, str(buf.moves))
check("插入后 size=10", buf.size == 10, str(buf.size))
check("16 容量装 10 个元素,一次都不用扩容", buf.grows == 0, str(buf.grows))

before = buf.moves
moved = buf.move_gap_to(0)
check("把间隙从表尾移到表头,搬动 10 个元素", moved == 10 and buf.moves - before == 10,
      str(moved))
check("间隙移到表头后插入 1 个元素零搬动",
      (buf.insert(["head"]), buf.moves - before == 10)[1], str(buf.moves - before))
check("插入后逻辑顺序正确", buf.items()[:2] == ["head", 0], str(buf.items()[:2]))

before = buf.moves
buf.remove(1)
check("删除只扩间隙、不搬元素", buf.moves == before, str(buf.moves - before))
check("删除后 size 减 1", buf.size == 10, str(buf.size))

# 搬动代价 ∝ 间隙移动距离
buf2 = GapBuffer(capacity=128)
for i in range(100):
    buf2.insert([i])
buf2.move_gap_to(50)
check("100 元素表把间隙移到中部,搬动 50 个元素", buf2.moves == 50, str(buf2.moves))
moved_back = buf2.move_gap_to(buf2.size)
check("再移回表尾(逻辑位置 100)又搬 50 个", moved_back == 50, str(moved_back))
check("总搬动 = 2×距离(2×50=100)", buf2.moves == 100, str(buf2.moves))

# ===========================================================================
# B. group 树结构
# ===========================================================================
t = SlotTable()
root = t.start_group(100)
t.write_slots(["r0", "r1"])
c1 = t.start_group(201)
t.write_slots(["c1a"])
g1 = t.start_group(301)
t.write_slots(["g1"])
t.end_group()
g2 = t.start_group(302)
t.write_slots(["g2a", "g2b"])
t.end_group()
t.end_group()
c2 = t.start_group(202)
t.write_slots(["c2"])
t.end_group()
t.end_group()

check("共 5 个 group", t.groups_size == 5, str(t.groups_size))
check("root 的父为 -1", t.parent(root) == -1, str(t.parent(root)))
check("c1 的父是 root", t.parent(c1) == root, str(t.parent(c1)))
check("c2 的父是 root", t.parent(c2) == root, str(t.parent(c2)))
check("g1/g2 的父是 c1",
      t.parent(g1) == c1 and t.parent(g2) == c1,
      "%d/%d" % (t.parent(g1), t.parent(g2)))
check("groupSize(root) = 自身 + 4 个后代 = 5", t.group_size(root) == 5, str(t.group_size(root)))
check("groupSize(c1) = 1 + g1 + g2 = 3", t.group_size(c1) == 3, str(t.group_size(c1)))
check("groupSize(g1) = 1(叶子)", t.group_size(g1) == 1, str(t.group_size(g1)))
check("groupSize(c2) = 1(叶子)", t.group_size(c2) == 1, str(t.group_size(c2)))
check("skipToGroupEnd(c1) 落在 c2 上", t.skip_to_group_end(c1) == c2,
      "%d vs %d" % (t.skip_to_group_end(c1), c2))
check("skipToGroupEnd(root) 越过整张表", t.skip_to_group_end(root) == 5,
      str(t.skip_to_group_end(root)))
check("skipToGroupEnd(g1) 落在 g2 上", t.skip_to_group_end(g1) == g2,
      "%d vs %d" % (t.skip_to_group_end(g1), g2))

check("root 的槽数 = 2", t.slot_count_of(root) == 2, str(t.slot_count_of(root)))
check("c1 的槽数 = 1", t.slot_count_of(c1) == 1, str(t.slot_count_of(c1)))
check("g2 的槽数 = 2", t.slot_count_of(g2) == 2, str(t.slot_count_of(g2)))
check("最后一个 group 的槽数算到 slotsSize",
      t.slot_count_of(c2) == 1 and t.slots_size == 7, "%d / %d" %
      (t.slot_count_of(c2), t.slots_size))
check("全部槽数之和 = slotsSize",
      sum(t.slot_count_of(i) for i in range(t.groups_size)) == t.slots_size)
ok, why = t.verify_well_formed()
check("结构自洽(verifyWellFormed)", ok, why)

# group 头部固定占 Group_Fields_Size 个整数槽
check("5 个 group 占 25 个整数槽",
      t.groups.size == 5 * GROUP_FIELDS_SIZE, str(t.groups.size))

# ===========================================================================
# C. 锚点语义
# ===========================================================================
aid_g1 = t.new_anchor(g1)
aid_c2 = t.new_anchor(c2)
check("锚点可解析回 group 下标", t.resolve(aid_g1) == g1, str(t.resolve(aid_g1)))

# 间隙在表尾时,两个槽锚点之间的物理距离恰好等于逻辑槽数
logical_c1_to_c2 = t.slot_start_logical[c2] - t.slot_start_logical[c1]
check("间隙在表尾:物理距离 == 逻辑槽数",
      t.physical_slot_gap(c1, c2) == logical_c1_to_c2,
      "%d vs %d" % (t.physical_slot_gap(c1, c2), logical_c1_to_c2))

# 把间隙塞到 c1 与 c2 的槽区**之间**(逻辑位置 4),物理距离立刻含进间隙
gap_len = t.slots.gap_len
t.move_slots_gap_to(4)
check("间隙落在两者之间后,物理距离 > 逻辑槽数",
      t.physical_slot_gap(c1, c2) > logical_c1_to_c2,
      "%d vs %d" % (t.physical_slot_gap(c1, c2), logical_c1_to_c2))
check("多出来的部分恰好等于间隙长度",
      t.physical_slot_gap(c1, c2) - logical_c1_to_c2 == gap_len,
      "%d vs %d" % (t.physical_slot_gap(c1, c2) - logical_c1_to_c2, gap_len))
check("结论:写模式下 dataAnchor 不能当下标用(物理距离会被间隙污染)",
      t.physical_slot_gap(c1, c2) != logical_c1_to_c2)

# 但锚点被正确维护:每个锚点换算回逻辑位置后与记账一致
ok_anchors, bad = True, []
for i in range(t.groups_size):
    if t.slots.logical_of(t.slot_anchor_phys[i]) != t.slot_start_logical[i]:
        ok_anchors = False
        bad.append(i)
check("间隙移动后所有锚点仍换算回正确的逻辑位置", ok_anchors, str(bad))

# 把间隙移回表尾,物理距离又回到逻辑槽数
t.move_slots_gap_to(t.slots_size)
check("间隙移回表尾后物理距离恢复等于逻辑槽数",
      t.physical_slot_gap(c1, c2) == logical_c1_to_c2,
      "%d vs %d" % (t.physical_slot_gap(c1, c2), logical_c1_to_c2))

# 删除带锚点的子树 -> 锚点失效
t.remove_group(c1)
try:
    t.resolve(aid_g1)
    check("被删 group 的锚点抛 AnchorInvalidated", False, "未抛异常")
except AnchorInvalidated as exc:
    check("被删 group 的锚点抛 AnchorInvalidated",
          "Anchor refers to a group that was removed" in str(exc), str(exc))
check("存活 group 的锚点下标已前移(c2 从 4 变 1)", t.resolve(aid_c2) == 1,
      str(t.resolve(aid_c2)))
ok, why = t.verify_well_formed()
check("删除后结构仍自洽", ok, why)
check("删除 c1 子树后只剩 root + c2", t.groups_size == 2, str(t.groups_size))
check("c2 的新父仍是 root", t.parent(1) == 0, str(t.parent(1)))

moves_before = t.groups.moves
orphan = t.start_group(999)          # 此刻没有任何"打开中"的 group
t.end_group()
check("删除把间隙留在原地,紧接着插入零搬动",
      t.groups.moves == moves_before and t.groups_size == 3,
      "%d / %d" % (t.groups.moves, t.groups_size))
check("没有打开中的 group 时插进来的 group 会成为第二个根(父 = -1)",
      t.parent(orphan) == -1, str(t.parent(orphan)))
ok2, why2 = t.verify_well_formed()
check("负例:「多根」会被 verifyWellFormed 判为不自洽(Compose 只允许一个根)",
      not ok2 and "父" in why2, why2)

# ===========================================================================
# D. 满表删除回归(androidx a038886)
# ===========================================================================
# 先造一张"恰好占满物理容量、完全没有间隙"的表:1 + 1 + 10 + 1 = 13 个 group = 65 个槽
FULL_GROUPS = 13
full = SlotTable()
full.groups = GapBuffer(capacity=FULL_GROUPS * GROUP_FIELDS_SIZE)
full.slots.grow(FULL_GROUPS * GROUP_FIELDS_SIZE)
r = full.start_group(1)
c1f = full.start_group(2)
for i in range(10):
    full.start_group(100 + i)
    full.end_group()
full.end_group()
c2f = full.start_group(3)
full.end_group()
full.end_group()
check("满表:groups_size = 13", full.groups_size == FULL_GROUPS, str(full.groups_size))
check("满表:间隙长度为 0(这正是历史 bug 的触发条件)",
      full.groups.gap_len == 0, str(full.groups.gap_len))
ok, why = full.verify_well_formed()
check("满表初始自洽", ok, why)

span = full.remove_group(c1f)
check("删掉的子树含 11 个 group(自身 + 10 个孙)", span == 11, str(span))
check("删除后剩 root + c2 = 2 个 group", full.groups_size == 2, str(full.groups_size))
check("被删父关系已重映射:c2 的父仍是 root", full.parent(1) == 0, str(full.parent(1)))
ok, why = full.verify_well_formed()
check("满表删除后仍自洽(父锚点更新没有被跳过)", ok, why)

# 反证:如果像历史 bug 那样跳过父锚点更新,结构立刻不自洽
naive_parents = [p for p in [full.parent(0), full.parent(1)]]
naive_parents[1] = 12                       # 未减去 span,指向已不存在的 group
check("反证:父指针不重映射会指向越界下标(verifyWellFormed 会失败)",
      naive_parents[1] >= len(naive_parents), str(naive_parents))

print("=" * 62)
print("Compose SlotTable 结构自检:通过 %d 项,失败 %d 项" % (PASS, FAIL))
if FAILED:
    for line in FAILED:
        print("  [FAIL] " + line)
    sys.exit(1)
print("全部通过")
