"""Compose 快照系统的可执行模型（SnapshotIdSet + 状态记录链 + apply 冲突合并）。

对应源码（JetBrains/compose-multiplatform-core @master）：
  compose/runtime/runtime/src/commonMain/kotlin/androidx/compose/runtime/snapshots/Snapshot.kt
  .../snapshots/SnapshotIdSet.kt

覆盖：
  - SnapshotIdSet：双 Long 窗口 + 下界有序数组，不可变，无变化时返回同一实例
  - valid() / readable()：快照可见性判据
  - MutableSnapshot.apply()：碰撞检测与 mergeRecords 冲突合并
  - PreexistingSnapshotId = 1，nextSnapshotId 从 2 开始
"""

from snapshot_ids import (
    LONG_BITS, SNAPSHOT_ID_MAX, SNAPSHOT_ID_SIZE, SNAPSHOT_ID_ZERO, SnapshotIdSet,
)

INVALID_SNAPSHOT = 0
PREEXISTING_SNAPSHOT_ID = 1

# ------------------------------------------------------------ 可见性判据


def valid(current, candidate, invalid):
    """Snapshot.kt:2107 —— 三条件：非保留 id、不晚于当前、不在 invalid 集合里。"""
    return (candidate != INVALID_SNAPSHOT and candidate <= current
            and not invalid.get(candidate))


class StateRecord:
    def __init__(self, snapshot_id, value):
        self.snapshot_id = snapshot_id
        self.value = value
        self.next = None

    def create(self):
        return StateRecord(self.snapshot_id, self.value)


class StateObject:
    _seq = 0

    def __init__(self, value, snapshot_id=PREEXISTING_SNAPSHOT_ID):
        StateObject._seq += 1
        self.seq = StateObject._seq
        self.first = StateRecord(snapshot_id, value)
        self.merge_records_impl = None

    def merge_records(self, previous, current, applied):
        """默认实现返回 null —— 任何碰撞都会让 apply 失败。"""
        if self.merge_records_impl is None:
            return None
        return self.merge_records_impl(previous, current, applied)

    def overridden_records(self):
        r, out = self.first, []
        while r:
            out.append(r)
            r = r.next
        return out


def readable(first, id_, invalid):
    """Snapshot.kt:2122 —— 取合法的、snapshotId 最大的那条记录。"""
    cur, candidate = first, None
    while cur is not None:
        if valid(id_, cur.snapshot_id, invalid):
            candidate = cur if candidate is None or candidate.snapshot_id < cur.snapshot_id \
                else candidate
        cur = cur.next
    return candidate


# ------------------------------------------------------------ 快照系统


class SnapshotApplyConflictException(Exception):
    pass


class Snapshot:
    def __init__(self, id_, invalid):
        self.id = id_
        self.invalid = invalid
        self.modified = []          # 本快照内改过的 StateObject
        self.created = []           # 本快照内新建、尚未 notifyObjectsInitialized 的对象
        self.previous_ids = []      # advance() 之前用过的 id（recordPrevious）
        self.disposed = False
        self.applied = False
        self.read_observer = None

    def enter_read(self, obj):
        if self.read_observer is not None:
            self.read_observer(obj)


class SnapshotSystem:
    def __init__(self):
        self.next_id = PREEXISTING_SNAPSHOT_ID + 1     # Snapshot.kt:1953
        self.global_id = self.alloc()                  # GlobalSnapshot 也从 nextSnapshotId 取
        self.open = SnapshotIdSet().set(self.global_id)
        self.global_invalid = SnapshotIdSet()          # GlobalSnapshot.invalid = 加自己之前的 open
        self.global_modified = []
        self.apply_observers = []
        self.current = None

    def alloc(self):
        i = self.next_id
        self.next_id += 1
        return i

    def take_mutable_snapshot(self):
        """takeNewSnapshot + resetGlobalSnapshotLocked：invalid = 开着的快照去掉全局自己。"""
        invalid = self.open.clear(self.global_id)
        s = Snapshot(self.alloc(), invalid)
        self.open = self.open.set(s.id)
        self.open = self.open.clear(self.global_id)
        self.global_invalid = self.open.copy()
        self.global_id = self.alloc()
        self.open = self.open.set(self.global_id)
        return s

    def new_object(self, value, snapshot=None):
        """StateRecord() 默认构造器用的是 currentSnapshot().snapshotId。"""
        sid = snapshot.id if snapshot else self.global_id
        return StateObject(value, sid)

    def read(self, obj, snapshot=None):
        s = snapshot or self.current
        if s is not None and s.read_observer is not None:
            s.enter_read(obj)
        if s is None:
            r = readable(obj.first, self.global_id, self.global_invalid)
            return r.value if r else None
        r = readable(obj.first, s.id, s.invalid)
        return r.value if r else None

    def write(self, obj, value, snapshot=None):
        s = snapshot or self.current
        r = readable(obj.first, s.id, s.invalid) if s else \
            readable(obj.first, self.global_id, SnapshotIdSet())
        if s is not None and r is not None and r.snapshot_id == s.id:
            r.value = value                  # 本快照内的记录，可覆写
        else:
            new = StateRecord(s.id if s else self.global_id, value)
            new.next = obj.first
            obj.first = new                  # prependStateRecord
        if s is not None and obj not in s.modified and obj not in s.created:
            s.modified.append(obj)
        else:
            if obj not in self.global_modified:
                self.global_modified.append(obj)

    def notify_objects_initialized(self, snapshot):
        """之前在快照里新建的对象从这一刻起才参与 modified 记账。"""
        for obj in snapshot.created:
            snapshot.modified.append(obj)
        snapshot.created.clear()

    def create_state_object(self, value, snapshot=None):
        s = snapshot
        obj = StateObject(value, s.id if s else self.global_id)
        if s is not None:
            s.created.append(obj)
        return obj

    def advance(self, s):
        """MutableSnapshot.advance()：换一个新 id，并把 (旧, 新) 之间的都标 invalid。"""
        previous = s.id
        s.previous_ids.append(previous)
        s.id = self.alloc()
        self.open = self.open.set(s.id)
        s.invalid = s.invalid.add_range(previous + 1, s.id)
        return s

    def apply(self, s):
        """MutableSnapshot.apply() 的核心（innerApplyLocked 的判定顺序）。

        current  = readable(first, nextId, 所有仍开着的快照)
        previous = readable(first, s.id, s.invalid ∪ {s.id} ∪ s.previousIds)
        current != previous 才算碰撞，此时才去取 applied 并问 mergeRecords。
        """
        next_id = self.alloc()
        invalid_snapshots = self.open.clear(self.global_id)  # openSnapshots.clear(globalId)
        start = s.invalid.set(s.id)
        for pid in s.previous_ids:
            start = start.set(pid)
        for obj in s.modified:
            first = obj.first
            current = readable(first, next_id, invalid_snapshots)
            previous = readable(first, s.id, start)
            if current is None or previous is None:
                continue        # 在嵌套快照里创建并提交过的对象
            if previous.snapshot_id == PREEXISTING_SNAPSHOT_ID:
                continue        # 官方注释：嵌套快照里新建的对象，直接采用 applied
            if current is previous:
                continue        # 无碰撞，最常见
            applied = readable(first, s.id, s.invalid)
            merged = obj.merge_records(previous, current, applied)
            if merged is None:
                raise SnapshotApplyConflictException(obj)
            merged.snapshot_id = next_id
            merged.next = obj.first
            obj.first = merged
        old_global = self.global_id
        self.open = self.open.clear(s.id).clear(old_global)
        self.global_invalid = self.open.copy()
        self.global_id = self.alloc()          # 全局快照再往前走一格（> next_id）
        self.open = self.open.set(self.global_id)
        s.applied = True
        s.modified = []
        return True


def main():
    sys_ = SnapshotSystem()
    counter = StateObject(0)
    print("初始：global id =", sys_.global_id, " 预置 id =", PREEXISTING_SNAPSHOT_ID)

    s = sys_.take_mutable_snapshot()
    print("取快照：id =", s.id, " invalid =", list(s.invalid), " global 前进到", sys_.global_id)
    sys_.write(counter, 41, s)
    print("快照内读到：", sys_.read(counter, s), " 全局仍读到：", sys_.read(counter))

    ids = SnapshotIdSet()
    ids = ids.set(5).set(70).set(200)
    print("SnapshotIdSet:", [i for i in ids], " lowerBound =", ids.lower_bound)

    try:
        sys_.apply(s)
        print("apply 成功，全局读到：", sys_.read(counter))
    except SnapshotApplyConflictException:
        print("apply 冲突")


if __name__ == "__main__":
    main()
