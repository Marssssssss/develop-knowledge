"""Compose 快照 + LazyColumn 模型自检：判据来自 androidx compose 源码。"""

from snapshot_core import (
    StateObject, StateRecord, MutableSnapshot, SnapshotSystem, GlobalSnapshot,
    PREEXISTING_SNAPSHOT_ID, INVALID_SNAPSHOT,
)
from main import (
    measure_lazy_list, LazyListState, without_read_observation, reused_slots,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, label
    PASS += 1


def raises(exc, fn, label, needle=None):
    global PASS
    try:
        fn()
    except exc as e:
        if needle is not None:
            assert needle in str(e), "%s: 消息里没有 %r，实得 %r" % (label, needle, str(e))
        PASS += 1
    except Exception as e:  # noqa: BLE001
        raise AssertionError("%s: 期望 %s，实得 %s(%s)" % (label, exc.__name__, type(e).__name__, e))
    else:
        raise AssertionError("%s: 期望抛 %s，但没有抛" % (label, exc.__name__))


def advanced(system, sid):
    """把全局快照推进到 sid（等价于 resetGlobalSnapshotLocked 之后的全局 id）。"""
    system.global_snapshot = GlobalSnapshot(sid)
    return system


# ---- 1. 常量 ----
ok(PREEXISTING_SNAPSHOT_ID == 1, "PreexistingSnapshotId = 1（源码常量）")
ok(INVALID_SNAPSHOT == 0, "INVALID_SNAPSHOT = 0")

# ---- 2. 无冲突的 apply ----
sysm = SnapshotSystem()
s = StateObject(0)
snap = sysm.take_mutable_snapshot()
snap.set(s, 10)
ok(snap.apply(100).is_success, "take 之后无人写过 ⇒ apply 成功")
ok(s.readable(100).value == 10, "提交后新快照下读到新值")
ok(s.readable(snap.taken_from).value == 0, "旧快照 id 下仍读到旧值（多版本并存）")

# ---- 3. 空修改的 apply 永不失败 ----
snap2 = SnapshotSystem().take_mutable_snapshot()
ok(snap2.apply(200).is_success, "modified 为空时直接 Success（源码 closeLocked 分支）")

# ---- 4. 初始记录（PreexistingSnapshotId）不参与冲突判定 ----
sysm1 = SnapshotSystem()
st0 = StateObject(0)
base = sysm1.take_mutable_snapshot()
base.set(st0, 1)
ok(base.apply(2).is_success, "previous 的 id 恰为 PreexistingSnapshotId ⇒ 直接跳过冲突判定")
ok(st0.readable(2).value == 1, "首次写入总能成功")

# ---- 5. 碰撞写 → Failure ----
sysm2 = SnapshotSystem()
st = StateObject(0)
seed = sysm2.take_mutable_snapshot()
seed.set(st, 1)
seed.apply(2)
advanced(sysm2, 2)
a = sysm2.take_mutable_snapshot()      # taken_from = 2
b = sysm2.take_mutable_snapshot()      # taken_from = 2
a.set(st, 10)
ok(a.apply(3).is_success, "a 先提交成功")
advanced(sysm2, 3)
b.set(st, 20)
ok(b.apply(4).is_success is False,
   "b 的 previous(id=2) 与全局最新(id=3) 不同 ⇒ 碰撞，返回 Failure")
ok(st.readable(3).value == 10, "失败后全局值保持为 a 的写入")
ok(st.readable(4) is not None and st.readable(4).value == 10, "失败不写入新记录")

# ---- 6. merge 返回 None → Failure；返回 local → 保留本地 ----
sysm3 = SnapshotSystem()
st2 = StateObject(0, merge=lambda prev, cur, loc: None)
s3 = sysm3.take_mutable_snapshot(); s3.set(st2, 1); s3.apply(2)
advanced(sysm3, 2)
a3 = sysm3.take_mutable_snapshot(); b3 = sysm3.take_mutable_snapshot()
a3.set(st2, 10); a3.apply(3)
advanced(sysm3, 3)
b3.set(st2, 20)
ok(b3.apply(4).is_success is False, "merge 返回 null ⇒ Failure（源码 when(merged) 首支）")

sysm4 = SnapshotSystem()
st3 = StateObject(0, merge=lambda prev, cur, loc: loc)
s4 = sysm4.take_mutable_snapshot(); s4.set(st3, 1); s4.apply(2)
advanced(sysm4, 2)
a4 = sysm4.take_mutable_snapshot(); b4 = sysm4.take_mutable_snapshot()
a4.set(st3, 10); a4.apply(3)
advanced(sysm4, 3)
b4.set(st3, 20)
ok(b4.apply(4).is_success, "merge 返回本地记录 ⇒ 冲突被忽略，apply 成功")
ok(st3.readable(4).value == 20, "本地变更覆盖全局值")

# ---- 7. merge 返回全局最新 → 本地修改作废 ----
sysm5 = SnapshotSystem()
st4 = StateObject(0, merge=lambda prev, cur, loc: cur)
s5 = sysm5.take_mutable_snapshot(); s5.set(st4, 1); s5.apply(2)
advanced(sysm5, 2)
a5 = sysm5.take_mutable_snapshot(); b5 = sysm5.take_mutable_snapshot()
a5.set(st4, 10); a5.apply(3)
advanced(sysm5, 3)
b5.set(st4, 20)
ok(b5.apply(4).is_success, "merge 返回全局记录 ⇒ 仍然成功")
ok(st4.readable(4).value == 10, "采用全局值，本地修改被丢弃")

# ---- 8. merge 返回协商值 ----
sysm6 = SnapshotSystem()
st5 = StateObject(0, merge=lambda prev, cur, loc: StateRecord(0, loc.value + cur.value))
s6 = sysm6.take_mutable_snapshot(); s6.set(st5, 10); s6.apply(2)
advanced(sysm6, 2)
a6 = sysm6.take_mutable_snapshot(); b6 = sysm6.take_mutable_snapshot()
a6.set(st5, 10); a6.apply(3)
advanced(sysm6, 3)
b6.set(st5, 5)
ok(b6.apply(4).is_success, "自定义合并成功")
ok(st5.readable(4).value == 15, "采用协商值 10 + 5")

# ---- 9. previous 缺失的状态对象不判冲突 ----
sysm7 = SnapshotSystem()
st6 = StateObject(None)
st6.records = []
sn = sysm7.take_mutable_snapshot()
sn.set(st6, "x")
ok(sn.apply(101).is_success, "previous 读不到 ⇒ 视为新建，不判冲突")

# ---- 10. enter / leave 的 checkPrecondition ----
sysm8 = SnapshotSystem()
sa = sysm8.take_mutable_snapshot()
prev = sysm8.enter(sa)
ok(prev is None, "enter 返回上一个当前快照")
raises(AssertionError, lambda: sa.leave(object()), "leave 的不是当前快照", "is not the current snapshot")
sysm8.leave(prev)

# ---- 11. 已 dispose 的快照不能再 apply ----
sd = SnapshotSystem().take_mutable_snapshot()
sd.dispose()
raises(AssertionError, lambda: sd.apply(300), "dispose 后 apply", "Cannot use a disposed snapshot")

# ---- 12. LazyColumn：空数据集 ----
r = measure_lazy_list(0, [], viewport=100)
ok(r.has_visible_items is False, "itemsCount <= 0 ⇒ 没有可见项")
ok(r.first_index == 0 and r.first_offset == 0, "空数据集重置滚动位置")
ok(r.can_scroll_forward is False, "空数据集不能向前滚")

# ---- 13. 索引越界钳位 ----
r2 = measure_lazy_list(3, [50, 50, 50], viewport=100, first_visible_item_index=9)
ok(r2.first_index == 2, "firstVisibleItemIndex >= itemsCount ⇒ 钳到 itemsCount - 1")
ok(r2.first_offset == 0, "钳位时 offset 归零")

# ---- 14. 可见范围与 spacing ----
r3 = measure_lazy_list(10, [40] * 10, viewport=100)
ok(r3.visible == [0, 1, 2], "视口 100 / 单项 40 ⇒ 可见 3 项（超出即停）")
r4 = measure_lazy_list(10, [40] * 10, viewport=100, space_between_items=10)
ok(r4.visible == [0, 1], "有 10px 间隔后同样的视口只能放下 2 项")
r5 = measure_lazy_list(3, [40, 40, 40], viewport=1000, space_between_items=10)
ok(r5.can_scroll_forward is False, "全部项都放得下时不能再向前滚")

# ---- 15. 末项不加间隔 ----
r5b = measure_lazy_list(2, [40, 40], viewport=1000, space_between_items=10)
ok(r5b.consumed_scroll == 0.0, "末项 spacing=0 不影响滚动消费")

# ---- 16. 头部滚动被吃掉 ----
r6 = measure_lazy_list(10, [40] * 10, viewport=100, scroll_to_be_consumed=30.0)
ok(r6.first_offset == 0, "已在第 0 项且 offset 会变负 ⇒ 被钳到 0")
ok(r6.consumed_scroll == 0.0, "被钳掉的那部分滚动量不记为已消费")
r6b = measure_lazy_list(10, [40] * 10, viewport=100,
                        first_visible_item_index=2, first_visible_item_scroll_offset=40,
                        scroll_to_be_consumed=30.0)
ok(r6b.first_offset == 10, "不在第 0 项时 offset 正常递减（40 - 30）")
ok(r6b.consumed_scroll == 30.0, "不在第 0 项时滚动量被完整消费")

# ---- 17. beyondBoundsItemCount 与 pinned ----
r7 = measure_lazy_list(10, [40] * 10, viewport=100, beyond_bounds_item_count=2)
ok(r7.visible == [0, 1, 2, 3, 4], "beyondBoundsItemCount=2 ⇒ 可见区外多测 2 项")
r8 = measure_lazy_list(10, [40] * 10, viewport=100, pinned_indices=(7,))
ok(7 in r8.visible, "pinned 项即使不可见也会被测量")

# ---- 18. 按 key 修正滚动位置 ----
state = LazyListState(first_visible_item_index=3)
keys = ["a", "b", "c", "d", "e"]
log = []
new_index = without_read_observation(
    lambda: state.update_scroll_position_if_the_first_item_was_moved(keys, 3), log)
ok(new_index == 3, "首次记录下 key")
ok(log == ["withoutReadObservation"], "位置修正在 withoutReadObservation 里执行，不产生读观测")
keys2 = ["x", "a", "b", "c", "d"]     # 头部插入一项，原 key=d 从 3 移到 4
new_index = state.update_scroll_position_if_the_first_item_was_moved(keys2, 3)
ok(new_index == 4, "按 key 找回新的位置（不是停留在旧下标）")
ok(state.first_visible_item_index == 4, "state 的 firstVisibleItemIndex 被同步更新")
new_index = state.update_scroll_position_if_the_first_item_was_moved(["z", "y"], 1)
ok(new_index == 0, "key 找不到时退回 0，offset 归零")

# ---- 19. 复用槽位 ----
prev_keys = ["a", "b", "c"]
prev_ct = ["T", "T", "H"]
next_keys = ["c", "a", "b"]
next_ct = ["H", "T", "T"]   # contentType 跟着 key 走，而不是跟着位置走
ok(reused_slots(prev_keys, prev_ct, next_keys, next_ct) == [(2, 0), (0, 1), (1, 2)],
   "按 key 复用：顺序变了但槽位跟着 key 走")
ok(reused_slots(prev_keys, prev_ct, ["a"], ["H"]) == [],
   "contentType 不同的槽位不能复用")
ok(reused_slots(prev_keys, prev_ct, ["d"], ["T"]) == [], "新 key 没有可复用槽位")

print("PASS %d" % PASS)
