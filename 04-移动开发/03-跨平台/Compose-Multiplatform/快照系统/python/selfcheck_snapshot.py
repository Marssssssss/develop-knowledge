"""Compose 快照系统自检：SnapshotIdSet / valid / readable / apply 冲突合并。

运行： python selfcheck_snapshot.py
"""

from main import (
    INVALID_SNAPSHOT, PREEXISTING_SNAPSHOT_ID, SnapshotIdSet, SnapshotSystem,
    StateObject, StateRecord, readable, valid,
    SnapshotApplyConflictException, LONG_BITS,
)

PASS = 0


def ok(cond, label, actual=None):
    global PASS
    assert cond, "FAIL: {} -> {!r}".format(label, actual)
    PASS += 1


def sys_():
    return SnapshotSystem()


# ================================================== A. SnapshotIdSet
E = SnapshotIdSet()
ok(E.get(0) is False and E.get(5) is False and E.get(1000) is False,
   "A1 EMPTY 对任何 id 都是 false")

s = E.set(5)
ok(s.get(5) is True and s.get(6) is False, "A2 set(5) 后只有 5 为真")
ok(s is not E, "A3 不可变：set 产生新实例")
ok(s.set(5) is s, "A4 无变化时返回同一实例（源码注释明说）")
ok(E.get(5) is False, "A5 原集合未被修改")

ok(s.clear(5).get(5) is False, "A6 clear(5)")
ok(s.clear(6) is s, "A7 clear 一个没设置的位 -> 同一实例")

u = E.set(70)
ok(u.get(70) is True, "A8 offset 在 [64,128) 的进 upperSet", u.upper_set)
ok(u.upper_set != 0 and u.lower_set == 0, "A9 upperSet 被使用、lowerSet 仍为空")

sh = E.set(5).set(200)
ok(sh.lower_bound == 192, "A10 set(200) 把窗口下界抬到 ((200+1)/64)*64 = 192", sh.lower_bound)
ok(sh.get(200) is True, "A11 200 仍可读")
ok(sh.get(5) is True, "A12 原 lowerSet 被搬进 belowBound 数组，5 仍可读")
ok(sh.below_bound == [5], "A13 belowBound 是有序数组", sh.below_bound)

sh2 = sh.set(300)
ok(sh2.get(300) is True and sh2.get(200) is True and sh2.get(5) is True,
   "A14 再次越界仍能读回全部已设 id")

ok(E.set(1).set(3).or_(E.set(3).set(9)).get(9) is True, "A15 or 是并集")
ok(E.set(1).set(3).and_not(E.set(3)).get(3) is False, "A16 andNot 是差集（a & ~b）")
ok(E.set(1).set(3).and_not(E.set(3)).get(1) is True, "A17 andNot 只去掉交集部分")

r = E.add_range(3, 6)
ok(r.get(3) and r.get(4) and r.get(5) and not r.get(6),
   "A18 addRange(from, until)：until 是排他的")
ok(E.add_range(3, 3) is E, "A19 from == until 时不产生变化")
ok(E.set(5).lowest(-1) == 5, "A20 lowest 取集合里最小的 id")
ok(E.set(70).set(5).lowest(-1) == 5, "A21 lowest 会先看 lowerSet 再看 upperSet")
ok(E.set(300).set(5).lowest(-1) == 300,
   "A22 lowest 先看窗口内的两个 Long（300 在窗口里，5 在 belowBound 里）",
   E.set(300).set(5).lowest(-1))
ok(E.set(300).set(5).clear(300).lowest(-1) == 5,
   "A22b 窗口空了才轮到 belowBound 数组")
ok(E.lowest(42) == 42, "A23 空集返回默认值")

# ================================================== B. valid()
IV = SnapshotIdSet()
ok(valid(5, INVALID_SNAPSHOT, IV) is False, "B1 INVALID_SNAPSHOT(0) 是保留 id")
ok(valid(5, 6, IV) is False, "B2 晚于当前快照的候选非法")
ok(valid(5, 5, IV) is True, "B3 等于当前快照合法")
ok(valid(5, 4, IV) is True, "B4 早于当前快照合法")
ok(valid(5, 4, IV.set(4)) is False, "B5 在 invalid 集合里的非法")

# ================================================== C. readable()
def chain(ids):
    head = None
    for i in reversed(ids):
        rec = StateRecord(i, "v%d" % i)
        rec.next = head
        head = rec
    return head


ok(readable(chain([1, 3, 5]), 4, IV).snapshot_id == 3,
   "C1 取合法的最大 snapshotId（当前 4 -> 选 3）")
ok(readable(chain([1, 3, 5]), 5, IV).snapshot_id == 5, "C2 当前 5 -> 选 5")
ok(readable(chain([1, 3, 5]), 4, IV.set(3)).snapshot_id == 1,
   "C3 3 被标 invalid 后退到 1")
ok(readable(chain([1, 3]), 4, IV.set(1).set(3)) is None,
   "C4 全部非法时返回 null（源码注释：全局被别的线程推进过时会发生）")
ok(readable(chain([3, 3]), 5, IV).snapshot_id == 3,
   "C5 两条 id 相同时取先遇到的那条")

# ================================================== D. 隔离与提交
sy = sys_()
counter = sy.new_object(0)
ok(sy.global_id == 2 and PREEXISTING_SNAPSHOT_ID == 1,
   "D1 全局快照 id 也来自 nextSnapshotId（首个 = 2），预置 id 是 1", sy.global_id)
ok(sy.next_id == 3, "D2 nextSnapshotId = PreexistingSnapshotId + 1 = 2，已发给全局快照后为 3",
   sy.next_id)

snap = sy.take_mutable_snapshot()
sy.write(counter, 41, snap)
ok(sy.read(counter, snap) == 41, "D3 快照内能读到自己的写")
ok(sy.read(counter) == 0, "D4 提交前全局看不到（隔离性）")
sy.apply(snap)
ok(sy.read(counter) == 41, "D5 apply 之后全局可见")

sy = sys_()
counter = sy.new_object(0)
s1 = sy.take_mutable_snapshot()
s2 = sy.take_mutable_snapshot()
ok(s2.invalid.get(s1.id) is True,
   "D6 取快照时把当时仍开着的快照都记进 invalid", list(s2.invalid))
sy.write(counter, 1, s1)
ok(sy.read(counter, s2) == 0, "D7 兄弟快照未提交的写互相不可见")
sy.apply(s1)
try:
    sy.write(counter, 2, s2)
    sy.apply(s2)
    ok(False, "D8 后提交的应该冲突")
except SnapshotApplyConflictException:
    ok(True, "D8 默认 mergeRecords 返回 null -> apply 失败（SnapshotApplyConflictException）")
ok(sy.read(counter) == 1, "D9 冲突后全局保持先提交的值")

sy = sys_()
counter = sy.new_object(0)


def summing(previous, current, applied):
    merged = StateRecord(current.snapshot_id, current.value + applied.value)
    return merged


counter.merge_records_impl = summing
a = sy.take_mutable_snapshot()
b = sy.take_mutable_snapshot()
sy.write(counter, 1, a)
sy.write(counter, 2, b)
sy.apply(a)
sy.apply(b)
ok(sy.read(counter) == 3, "D10 自定义 mutation policy 能把 1 与 2 合并成 3", sy.read(counter))

sy = sys_()
counter = sy.new_object(0)
s = sy.take_mutable_snapshot()
sy.write(counter, 7, s)
old = s.id
sy.advance(s)
ok(s.id > old, "D11 advance() 换到更大的 id", (old, s.id))
ok(s.invalid.get(old + 1) is True and s.invalid.get(s.id - 1) is True,
   "D12 invalid 增加 (旧, 新) 之间的全部 id")
ok(s.invalid.get(old) is False, "D13 旧 id 本身不在 invalid 里")
ok(sy.read(counter, s) == 7, "D14 advance 后仍读得到自己在旧 id 下的写")

# ================================================== E. 对象初始化记账
sy = sys_()
s = sy.take_mutable_snapshot()
fresh = sy.create_state_object(0, s)
sy.write(fresh, 5, s)
ok(fresh not in s.modified, "E1 notifyObjectsInitialized 之前，快照内新建对象的改动不记账")
sy.notify_objects_initialized(s)
sy.write(fresh, 6, s)
ok(fresh in s.modified, "E2 notify 之后的改动才进 modified 集合")
ok(sy.read(fresh, s) == 6, "E3 值仍然被改写")

sy = sys_()
nested = StateObject(0)          # 初始记录的 id 恒为 PreexistingSnapshotId
ok(nested.first.snapshot_id == PREEXISTING_SNAPSHOT_ID,
   "E4 新对象的初始记录用 PreexistingSnapshotId，因此任何快照都能看到它")
s = sy.take_mutable_snapshot()
sy.write(nested, 9, s)
sy.apply(s)
ok(sy.read(nested) == 9, "E5 官方注释：previous 是预置记录时直接采用 applied，不判冲突")

# ================================================== F. 读观察者
sy = sys_()
seen = []
s = sy.take_mutable_snapshot()
s.read_observer = lambda obj: seen.append(obj.seq)
obj = StateObject(0)
sy.read(obj, s)
ok(seen == [obj.seq], "F1 每次读都会回调 readObserver（composition 靠它订阅）")

# ================================================== G. 无变化的 apply
sy = sys_()
s = sy.take_mutable_snapshot()
before = sy.global_id
sy.apply(s)
ok(sy.read(sy.new_object(0)) == 0, "G1 空 apply 不报错")
ok(sy.global_id > before, "G2 但全局快照 id 仍然前进")

print("Compose 快照系统自检：{} 项断言全部通过".format(PASS))
