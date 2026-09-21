"""Compose 快照系统的最小可执行模型。

对应 androidx `compose/runtime/runtime/.../snapshots/Snapshot.kt`：
  - `PreexistingSnapshotId = 1`
  - `apply()` 的源码注释明确：*「does not guarantee serializable snapshots as it doesn't
    prevent crossing writes (https://arxiv.org/pdf/1412.2324.pdf)」*
  - `innerApplyLocked` 的三分支合并： merged == null → Failure；== applied → 忽略；== current → 回退
"""

INVALID_SNAPSHOT = 0
PREEXISTING_SNAPSHOT_ID = 1


class SnapshotApplyResult:
    def __init__(self, status, snapshot=None):
        self.status = status
        self.snapshot = snapshot

    @property
    def is_success(self):
        return self.status == "Success"

    def __repr__(self):
        return "SnapshotApplyResult.%s" % self.status


SUCCESS = SnapshotApplyResult("Success")


def failure(snapshot):
    return SnapshotApplyResult("Failure", snapshot)


class StateRecord:
    __slots__ = ("snapshot_id", "value")

    def __init__(self, snapshot_id, value):
        self.snapshot_id = snapshot_id
        self.value = value

    def create(self, snapshot_id):
        return StateRecord(snapshot_id, self.value)


class StateObject:
    """对应 StateObject：一条按 snapshotId 升序排列的 StateRecord 链。"""

    def __init__(self, value, merge=None):
        self.records = [StateRecord(PREEXISTING_SNAPSHOT_ID, value)]
        self.merge = merge  # (previous, current, applied) -> StateRecord | None
        self.written_by = PREEXISTING_SNAPSHOT_ID

    def readable(self, snapshot_id, invalid=frozenset()):
        """找 snapshotId <= 给定值、且不在 invalid 集合里的最新记录。"""
        found = None
        for r in self.records:
            if r.snapshot_id <= snapshot_id and r.snapshot_id not in invalid:
                if found is None or r.snapshot_id > found.snapshot_id:
                    found = r
        return found

    def write(self, snapshot_id, value):
        rec = StateRecord(snapshot_id, value)
        self.records.append(rec)
        self.records.sort(key=lambda r: r.snapshot_id)
        self.written_by = snapshot_id
        return rec

    @property
    def current(self):
        return self.records[-1].value


class GlobalSnapshot:
    def __init__(self, snapshot_id):
        self.snapshot_id = snapshot_id
        self.modified = {}


class MutableSnapshot:
    def __init__(self, snapshot_id, taken_from, global_snapshot, system=None, invalid=frozenset()):
        self.snapshot_id = snapshot_id
        self.taken_from = taken_from        # 本快照被 take 时全局的 id
        self.global_snapshot = global_snapshot
        self.system = system                # 全局快照会被替换，必须持有系统而不是旧对象
        self.invalid = invalid
        self.modified = {}                  # StateObject -> StateRecord（本快照的写入）
        self.applied = False
        self.disposed = False
        self.pinned = True

    # --- enter / leave（Snapshot.enter + unsafeLeave 的 checkPrecondition）---
    def leave(self, current):
        if current is not self:
            raise AssertionError("Cannot leave snapshot; %r is not the current snapshot" % (self,))
        self.pinned = False

    def set(self, state, value):
        if self.disposed:
            raise AssertionError("Cannot use a disposed snapshot")
        rec = StateRecord(self.snapshot_id, value)
        self.modified[state] = rec
        return rec

    # --- apply ---
    def apply(self, next_snapshot_id, open_snapshots=frozenset()):
        """对应 Snapshot.apply() + innerApplyLocked()。"""
        if self.disposed:
            raise AssertionError("Cannot use a disposed snapshot")
        if not self.modified:
            # 空修改：直接关闭并重置全局快照，永不失败
            self.applied = True
            return SUCCESS

        # 符号对齐官方 when(merged)：
        #   previous       = 本快照 take 时刻可见的记录
        #   global_current = 全局最新记录（源码里的 current）
        #   local          = 本快照自己写入的记录（源码里的 applied）
        plan = {}
        dropped = set()
        for state, local in self.modified.items():
            previous = state.readable(self.taken_from, self.invalid)
            if previous is None or previous.snapshot_id == PREEXISTING_SNAPSHOT_ID:
                # 状态对象是在嵌套快照里新建的：源码 return@forEach，视为无冲突
                plan[state] = local.value
                continue
            gid = self.system.global_snapshot.snapshot_id if self.system else self.global_snapshot.snapshot_id
            global_current = state.readable(gid, open_snapshots)
            if global_current is previous:
                plan[state] = local.value  # take 之后无人写过 ⇒ 无碰撞
                continue
            merged = state.merge(previous, global_current, local) if state.merge else None
            if merged is None:
                return failure(self)
            if merged is local:
                plan[state] = local.value                       # 本地变更掩盖冲突
            elif merged is global_current:
                plan[state] = global_current.value              # 回退到全局值
                dropped.add(state)                              # 该 state 不再是 modified
            else:
                plan[state] = merged.value                      # 采用协商值
        for state, value in plan.items():
            state.write(next_snapshot_id, value)
        self.applied = True
        self.disposed = True
        return SUCCESS

    def dispose(self):
        self.disposed = True


class SnapshotSystem:
    """持有全局快照 id 与线程局部的当前快照。"""

    def __init__(self, start_id=PREEXISTING_SNAPSHOT_ID):
        self.next_id = start_id + 1
        self.global_snapshot = GlobalSnapshot(start_id)
        self.current = None
        self.applied_observers = []

    def take_mutable_snapshot(self):
        sid = self.next_id
        snap = MutableSnapshot(sid, self.global_snapshot.snapshot_id, self.global_snapshot, system=self)
        self.next_id += 1
        return snap

    def enter(self, snap):
        previous = self.current
        self.current = snap
        return previous

    def leave(self, previous):
        self.current = previous

    def advance_global(self):
        self.global_snapshot = GlobalSnapshot(self.next_id)
        self.next_id += 1
