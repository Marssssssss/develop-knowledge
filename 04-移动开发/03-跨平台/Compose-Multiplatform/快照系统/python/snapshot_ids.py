"""SnapshotIdSet：Compose 快照 id 的双窗口位集合。

对应源码：JetBrains/compose-multiplatform-core @master
  compose/runtime/runtime/src/commonMain/kotlin/androidx/compose/runtime/snapshots/SnapshotIdSet.kt

结构：upperSet(64) + lowerSet(64) 覆盖 [lowerBound, lowerBound+127]，
      lowerBound 以下的 id 放在有序数组 belowBound 里；窗口之上恒为 0。
所有修改操作返回新实例；无变化时返回同一实例。
"""

LONG_BITS = 64
# SnapshotIdSize（平台 actual）：窗口按 64 对齐
SNAPSHOT_ID_SIZE = 64
SNAPSHOT_ID_MAX = 0x7FFFFFFF
SNAPSHOT_ID_ZERO = 0


def _shift_target(id_):
    """set() 超出窗口时把下界抬到 (id+1) 按 64 对齐的位置。"""
    t = ((id_ + 1) // SNAPSHOT_ID_SIZE) * SNAPSHOT_ID_SIZE
    return SNAPSHOT_ID_MAX - (SNAPSHOT_ID_SIZE * 2) + 1 if t < 0 else t


class SnapshotIdSet:
    """双 Long 窗口 [lowerBound, lowerBound+127] + 下界以下的有序数组。"""

    __slots__ = ("upper_set", "lower_set", "lower_bound", "below_bound")

    def __init__(self, upper_set=0, lower_set=0, lower_bound=SNAPSHOT_ID_ZERO,
                 below_bound=None):
        self.upper_set = upper_set
        self.lower_set = lower_set
        self.lower_bound = lower_bound
        self.below_bound = list(below_bound) if below_bound else None

    @property
    def EMPTY():
        return SnapshotIdSet(0, 0, SNAPSHOT_ID_ZERO, None)

    def copy(self):
        return SnapshotIdSet(self.upper_set, self.lower_set, self.lower_bound,
                             self.below_bound)

    def get(self, id_):
        offset = id_ - self.lower_bound
        if 0 <= offset < LONG_BITS:
            return (self.lower_set >> offset) & 1 == 1
        if LONG_BITS <= offset < LONG_BITS * 2:
            return (self.upper_set >> (offset - LONG_BITS)) & 1 == 1
        if offset > 0:
            return False            # 窗口之上恒为 0
        if self.below_bound is None:
            return False
        return self._bsearch(self.below_bound, id_) >= 0

    @staticmethod
    def _bsearch(arr, x):
        lo, hi = 0, len(arr) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if arr[mid] == x:
                return mid
            if arr[mid] < x:
                lo = mid + 1
            else:
                hi = mid - 1
        return -(lo + 1)

    def set(self, id_):
        offset = id_ - self.lower_bound
        if 0 <= offset < LONG_BITS:
            mask = 1 << offset
            if self.lower_set & mask == 0:
                r = self.copy()
                r.lower_set |= mask
                return r
            return self
        if LONG_BITS <= offset < LONG_BITS * 2:
            mask = 1 << (offset - LONG_BITS)
            if self.upper_set & mask == 0:
                r = self.copy()
                r.upper_set |= mask
                return r
            return self
        if offset >= LONG_BITS * 2:
            # 下界抬到目标位置，原 lowerSet 落进数组，upperSet 变 lowerSet
            if self.get(id_):
                return self
            target = _shift_target(id_)
            new = self.copy()
            below = list(new.below_bound) if new.below_bound else []
            while new.lower_bound < target:
                if new.lower_set != 0:
                    for b in range(LONG_BITS):
                        if (new.lower_set >> b) & 1:
                            below.append(new.lower_bound + b)
                if new.upper_set == 0:
                    new.lower_bound = target
                    new.lower_set = 0
                    break
                new.lower_set = new.upper_set
                new.upper_set = 0
                new.lower_bound += LONG_BITS
            new.below_bound = sorted(below) or None
            return new.set(id_)
        # offset < 0：下界以下，插进有序数组
        if self.below_bound is None:
            r = self.copy()
            r.below_bound = [id_]
            return r
        loc = self._bsearch(self.below_bound, id_)
        if loc < 0:
            r = self.copy()
            r.below_bound = list(self.below_bound)
            r.below_bound.insert(-(loc + 1), id_)
            return r
        return self

    def clear(self, id_):
        offset = id_ - self.lower_bound
        if 0 <= offset < LONG_BITS:
            mask = 1 << offset
            if self.lower_set & mask:
                r = self.copy()
                r.lower_set &= ~mask
                return r
            return self
        if LONG_BITS <= offset < LONG_BITS * 2:
            mask = 1 << (offset - LONG_BITS)
            if self.upper_set & mask:
                r = self.copy()
                r.upper_set &= ~mask
                return r
            return self
        if offset < 0 and self.below_bound is not None:
            loc = self._bsearch(self.below_bound, id_)
            if loc >= 0:
                r = self.copy()
                r.below_bound = list(self.below_bound)
                r.below_bound.pop(loc)
                return r
        return self

    def or_(self, other):
        s = self
        for i in other:
            s = s.set(i)
        return s

    def and_not(self, other):
        s = self
        for i in other:
            s = s.clear(i)
        return s

    def add_range(self, start, until):
        """Snapshot.kt:2578 —— 就是一个个 set（until 是排他的）。"""
        result = self
        i = start
        while i < until:
            result = result.set(i)
            i += 1
        return result

    def lowest(self, default):
        lo = self.lower_bound
        for b in range(LONG_BITS):
            if (self.lower_set >> b) & 1:
                return lo + b
        for b in range(LONG_BITS):
            if (self.upper_set >> b) & 1:
                return lo + LONG_BITS + b
        if self.below_bound:
            return self.below_bound[0]
        return default

    def __iter__(self):
        if self.below_bound:
            for i in self.below_bound:
                yield i
        lo = self.lower_bound
        for b in range(LONG_BITS):
            if (self.lower_set >> b) & 1:
                yield lo + b
        for b in range(LONG_BITS):
            if (self.upper_set >> b) & 1:
                yield lo + LONG_BITS + b
