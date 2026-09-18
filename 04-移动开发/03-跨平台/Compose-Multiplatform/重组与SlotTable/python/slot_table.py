"""Compose 运行时 SlotTable 的 gap buffer 与 group 树结构模型(纯标准库)。

权威依据(2026-09-18 实读):
  * androidx `compose/runtime/runtime/.../SlotTable.kt` 字段注释原文(经
    github.com/CarGuo/gsy_flutter_book 的引用读到,逐字):
      - `groups`:「An array to store group information that is stored as groups of
        [Group_Fields_Size] elements of the array. The [groups] array can be thought
        of as an array of an inline struct.」
      - `slots`:「The slot elements for a group start at the offset returned by
        [dataAnchor] of [groups] and continue to the next group's slots or to
        [slotsSize] for the last group. When in a writer the [dataAnchor] is an
        anchor instead of an index as [slots] might contain a gap.」
  * androidx 公开 API 面(commit 01940843 的 API dump):SlotTable 有
    `openReader/openWriter/groupsSize/slotsSize/verifyWellFormed`;SlotReader 有
    `groupSize(index)/skipToGroupEnd()/parent(index)/anchor(index)`。
  * 「锚点指向已删除的 group」会抛异常(compose-jb issue #345 栈里有
    `Anchor refers to a group that was removed`)。
  * androidx commit a038886「Fix boundary condition in slot table」:从**没有 gap**
    的表里删 group 时,父锚点更新那段曾被整段跳过 —— 本模型把它做成回归断言。

**口径标注**:group 头部的 5 个字段"各自存什么",官方注释只到"每组占
Group_Fields_Size 个元素、可视为内联结构体"这一层;Key / GroupInfo /
ParentAnchor / DataAnchor / ObjectKey 的具体拆分来自第三方逆向整理
(deepwiki 对 compose-multiplatform-core 的解读),**不是官方逐字表述**。
因此本模型把字段名当"语义占位",断言只用可从公开 API 直接观察的量
(groupSize / parent / skipToGroupEnd / anchors / well-formed),不对位级布局下结论。
"""

GROUP_FIELDS_SIZE = 5
FIELD_NAMES = ("key", "groupInfo", "parentAnchor", "dataAnchor", "objectKey")

ROOT_INDEX = 0


class AnchorInvalidated(Exception):
    """对应运行时那句 "Anchor refers to a group that was removed"。"""


class GapBuffer:
    """[有效区 | 间隙 | 有效区] 三段式数组。move_gap_to 返回本次搬动的元素数,
    这是判断 O(1) 还是 O(n) 的唯一依据。"""

    def __init__(self, capacity=16):
        self.data = [None] * capacity
        self.gap_start = 0
        self.gap_len = capacity
        self.moves = 0
        self.grows = 0

    @property
    def size(self):
        return len(self.data) - self.gap_len

    def physical_of(self, logical):
        """逻辑下标 -> 物理下标(考虑间隙)。"""
        return logical if logical < self.gap_start else logical + self.gap_len

    def logical_of(self, physical):
        return physical if physical < self.gap_start else physical - self.gap_len

    def items(self):
        return self.data[:self.gap_start] + self.data[self.gap_start + self.gap_len:]

    def move_gap_to(self, physical):
        if physical < self.gap_start:
            moved = self.gap_start - physical
            self.data[physical + self.gap_len: self.gap_start + self.gap_len] = \
                self.data[physical:self.gap_start]
        elif physical > self.gap_start:
            moved = physical - self.gap_start
            self.data[self.gap_start:self.gap_start + moved] = \
                self.data[self.gap_start + self.gap_len: physical + self.gap_len]
        else:
            moved = 0
        self.gap_start = physical
        self.moves += moved
        return moved

    def grow(self, need):
        while self.gap_len < need:
            self.data.extend([None] * len(self.data))
            self.gap_len = len(self.data) - self.gap_start
            self.grows += 1

    def insert(self, values):
        self.grow(len(values))
        n = len(values)
        self.data[self.gap_start:self.gap_start + n] = values
        self.gap_start += n
        self.gap_len -= n
        return 0

    def remove(self, count):
        self.gap_len += count
        return 0


class SlotTable:
    def __init__(self):
        self.groups = GapBuffer()
        self.slots = GapBuffer()
        self.parents = []
        self.keys = []
        self.open_stack = []
        self.slot_start_logical = []      # 每个 group 的槽区逻辑起点
        self.slot_anchor_phys = []        # 每个 group 的槽区锚点(物理位置,需随 gap 移动更新)
        self.anchors = {}                 # anchor id -> group 下标

    # ---------------- 只读 ----------------
    @property
    def groups_size(self):
        return self.groups.size // GROUP_FIELDS_SIZE

    @property
    def slots_size(self):
        return self.slots.size

    def descendants(self, index):
        out, stack = [], [index]
        while stack:
            cur = stack.pop()
            for i, p in enumerate(self.parents):
                if p == cur and i != cur:
                    out.append(i)
                    stack.append(i)
        return out

    def group_size(self, index):
        """子树规模(自身 + 后代),对应 SlotReader.groupSize(index)。"""
        return 1 + len(self.descendants(index))

    def parent(self, index):
        return self.parents[index] if 0 <= index < len(self.parents) else -1

    def skip_to_group_end(self, index):
        """对应 skipToGroupEnd:跳过整棵子树,落在下一个兄弟上。"""
        return index + self.group_size(index)

    def slot_count_of(self, index):
        """group 槽数 = 到下一个 group 的槽区起点或 slotsSize 为止(源码注释口径)。"""
        start = self.slot_start_logical[index]
        end = (self.slots_size if index == self.groups_size - 1
               else self.slot_start_logical[index + 1])
        return end - start

    def physical_slot_gap(self, a, b):
        """两个 group 的槽锚点之间的**物理**距离(写模式下会含间隙)。"""
        return self.slot_anchor_phys[b] - self.slot_anchor_phys[a]

    # ---------------- 写 ----------------
    def start_group(self, key):
        parent = self.open_stack[-1] if self.open_stack else -1
        self.groups.grow(GROUP_FIELDS_SIZE)
        self.groups.insert([key, 0, parent, self.slots.gap_start, 0])
        self.parents.append(parent)
        self.keys.append(key)
        self.slot_start_logical.append(self.slots.size)
        self.slot_anchor_phys.append(self.slots.gap_start)
        self.open_stack.append(len(self.parents) - 1)
        return len(self.parents) - 1

    def end_group(self):
        self.open_stack.pop()

    def write_slots(self, values):
        self.slots.insert(list(values))
        return len(values)

    def move_slots_gap_to(self, logical_index):
        """把 slots 的间隙移到某个**逻辑**位置,同时按 gap 移动量平移所有存活锚点。

        这正是源码注释说"写模式下 dataAnchor 是锚点而不是下标"的原因:
        下标会失效,锚点必须被主动维护。
        """
        old_start = self.slots.gap_start
        new_start = logical_index          # 间隙的逻辑位置就是"前面有多少个有效元素"
        gap_len = self.slots.gap_len
        self.slots.move_gap_to(new_start)
        for i in range(len(self.slot_anchor_phys)):
            p = self.slot_anchor_phys[i]
            if new_start < old_start and old_start > p >= new_start:
                self.slot_anchor_phys[i] = p + gap_len
            elif new_start > old_start and old_start + gap_len <= p < new_start + gap_len:
                self.slot_anchor_phys[i] = p - gap_len

    def new_anchor(self, index):
        aid = len(self.anchors)
        self.anchors[aid] = index
        return aid

    def resolve(self, aid):
        if aid not in self.anchors:
            raise AnchorInvalidated("Anchor refers to a group that was removed")
        return self.anchors[aid]

    def remove_group(self, index):
        """删掉一个 group 及其后代。注意"表曾经满过(没有 gap)"是历史 bug 的触发条件:
        此时父锚点更新容易被整段跳过(androidx a038886),因此这里无条件更新。"""
        span = self.group_size(index)
        self.groups.move_gap_to(index * GROUP_FIELDS_SIZE)
        self.groups.remove(span * GROUP_FIELDS_SIZE)

        kept, kept_keys, kept_logical, kept_phys = [], [], [], []
        for i, p in enumerate(self.parents):
            if index <= i < index + span:
                continue
            kept.append(p - span if p >= index + span else p)
            kept_keys.append(self.keys[i])
            kept_logical.append(self.slot_start_logical[i])
            kept_phys.append(self.slot_anchor_phys[i])
        self.parents, self.keys = kept, kept_keys
        self.slot_start_logical, self.slot_anchor_phys = kept_logical, kept_phys

        # 锚点维护是写者(Writer)的责任:子树内的锚点失效,其后的锚点整体前移 span
        for aid, gi in list(self.anchors.items()):
            if index <= gi < index + span:
                del self.anchors[aid]
            elif gi >= index + span:
                self.anchors[aid] = gi - span
        return span

    def verify_well_formed(self):
        for i, p in enumerate(self.parents):
            if i == ROOT_INDEX:
                if p != -1:
                    return False, "root 的父不是 -1"
            elif not (0 <= p < i):
                return False, "group %d 的父 %d 非法" % (i, p)
        if any(v >= len(self.parents) for v in self.anchors.values()):
            return False, "有锚点指向已不存在的 group"
        return True, "ok"
