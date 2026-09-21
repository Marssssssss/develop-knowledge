"""LazyColumn 的测量与按 key 的滚动位置修正。

对应 androidx `compose/foundation/foundation`：
  - `lazy/LazyListMeasure.kt` 的 `measureLazyList(...)`：空数据集、索引越界钳位、spacing 只加在非末项
  - `lazy/LazyList.kt` 里 `Snapshot.withoutReadObservation { state.updateScrollPositionIfTheFirstItemWasMoved(...) }`
"""

from snapshot_core import (
    StateObject, StateRecord, MutableSnapshot, SnapshotSystem, SnapshotApplyResult,
    PREEXISTING_SNAPSHOT_ID, INVALID_SNAPSHOT,
)


# ---------- LazyColumn 测量 ----------

class LazyListMeasureResult:
    def __init__(self, first_index, first_offset, visible, consumed_scroll,
                 can_scroll_forward, total_items_count, has_visible_items):
        self.first_index = first_index
        self.first_offset = first_offset
        self.visible = visible
        self.consumed_scroll = consumed_scroll
        self.can_scroll_forward = can_scroll_forward
        self.total_items_count = total_items_count
        self.has_visible_items = has_visible_items


def measure_lazy_list(items_count, item_sizes, viewport,
                      first_visible_item_index=0, first_visible_item_scroll_offset=0,
                      scroll_to_be_consumed=0.0, space_between_items=0,
                      before_content_padding=0, after_content_padding=0,
                      beyond_bounds_item_count=0, pinned_indices=()):
    """复现 measureLazyList 的主循环（去掉动画、预取与 sticky 头）。"""
    if items_count <= 0 or not item_sizes:
        # 空数据集：重置滚动、报告零尺寸
        return LazyListMeasureResult(0, 0, [], 0.0, False, 0, False)

    current_first = first_visible_item_index
    current_offset = first_visible_item_scroll_offset
    if current_first >= items_count:
        # 数据集变小了，钳到最后一项
        current_first = items_count - 1
        current_offset = 0

    scroll_delta = int(round(scroll_to_be_consumed))
    current_offset -= scroll_delta
    if current_first == 0 and current_offset < 0:
        scroll_delta += current_offset      # 头部的滚动被吃掉一部分
        current_offset = 0

    main_axis_available = viewport + before_content_padding + after_content_padding
    visible = []
    used = -current_offset                       # 第一项已经滚出多少
    idx = current_first
    while idx < items_count:
        size = item_sizes[idx % len(item_sizes)]
        spacing = 0 if idx == items_count - 1 else space_between_items
        visible.append(idx)
        used += size + spacing
        idx += 1
        if used >= main_axis_available:
            break

    # beyondBoundsItemCount：可见区域之外再多测几项
    extra = 0
    while extra < beyond_bounds_item_count and idx < items_count:
        visible.append(idx)
        idx += 1
        extra += 1
    # pinnedItems 无论是否可见都要测
    for p in pinned_indices:
        if 0 <= p < items_count and p not in visible:
            visible.append(p)

    return LazyListMeasureResult(
        first_index=current_first,
        first_offset=current_offset,
        visible=visible,
        consumed_scroll=float(scroll_delta),
        can_scroll_forward=idx < items_count,
        total_items_count=items_count,
        has_visible_items=True,
    )


# ---------- 按 key 的滚动位置修正 ----------

class LazyListState:
    """对应 LazyListState：用 key 而不是位置来锚定滚动位置。"""

    def __init__(self, first_visible_item_index=0, first_visible_item_scroll_offset=0):
        self.first_visible_item_index = first_visible_item_index
        self.first_visible_item_scroll_offset = first_visible_item_scroll_offset
        self.last_known_key = None

    def update_scroll_position_if_the_first_item_was_moved(self, keys, index):
        """官方：在 Snapshot.withoutReadObservation 里调用，避免产生读观测。"""
        if not keys:
            self.last_known_key = None
            return 0
        if self.last_known_key is None:
            self.last_known_key = keys[index] if index < len(keys) else keys[0]
            return index if index < len(keys) else 0
        current_key = keys[index] if index < len(keys) else None
        if current_key == self.last_known_key:
            return index
        if self.last_known_key in keys:
            new_index = keys.index(self.last_known_key)
            self.first_visible_item_index = new_index
            return new_index
        # key 找不到：退回 0
        self.first_visible_item_index = 0
        self.first_visible_item_scroll_offset = 0
        return 0


def without_read_observation(block, observer_log):
    """对应 Snapshot.withoutReadObservation：执行期间读操作不上报。"""
    observer_log.append("withoutReadObservation")
    return block()


# ---------- 复用：key 决定是否能复用 composition ----------

def reused_slots(prev_keys, prev_content_types, next_keys, next_content_types):
    """按 key 匹配可复用的槽位；contentType 决定槽位池是否可共享。"""
    prev_by_key = {}
    for i, k in enumerate(prev_keys):
        prev_by_key.setdefault(k, i)
    reused = []
    for i, k in enumerate(next_keys):
        old = prev_by_key.get(k)
        if old is not None and prev_content_types[old] == next_content_types[i]:
            reused.append((old, i))
    return reused
