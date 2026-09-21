// Package snapshot 复刻 androidx.compose.runtime 的快照系统三段：
//   SnapshotIdSet.kt  —— 双 Long 窗口位集合
//   Snapshot.kt       —— valid() / readable() / innerApplyLocked 的碰撞判据
// 无本机 Go 工具链，仅人工审查 + 括号配平校验。
package snapshot

import "sort"

// 保留 id 与预置 id。
const (
	InvalidSnapshot     = 0
	PreexistingSnapshot = 1
)

// LongBits 是窗口的位宽；SnapshotIdSize 是窗口对齐粒度。
const (
	LongBits       = 64
	SnapshotIdSize = 64
	SnapshotIdMax  = 0x7FFFFFFF
)

// SnapshotIdSet 对应 SnapshotIdSet.kt：upperSet/lowerSet 覆盖
// [lowerBound, lowerBound+2*LongBits)，窗口之上恒为 0，窗口之下放有序数组。
type SnapshotIdSet struct {
	UpperSet   uint64
	LowerSet   uint64
	LowerBound int
	BelowBound []int
}

// Empty 返回空集合。
func Empty() SnapshotIdSet { return SnapshotIdSet{} }

func (s SnapshotIdSet) copy() SnapshotIdSet {
	return SnapshotIdSet{s.UpperSet, s.LowerSet, s.LowerBound, s.BelowBound}
}

// Get 取某一位。
func (s SnapshotIdSet) Get(id int) bool {
	offset := id - s.LowerBound
	switch {
	case offset >= 0 && offset < LongBits:
		return s.LowerSet&(1<<uint(offset)) != 0
	case offset >= LongBits && offset < LongBits*2:
		return s.UpperSet&(1<<uint(offset-LongBits)) != 0
	case offset > 0:
		return false // 窗口之上恒为 0
	}
	if s.BelowBound == nil {
		return false
	}
	i := sort.SearchInts(s.BelowBound, id)
	return i < len(s.BelowBound) && s.BelowBound[i] == id
}

// Set 置位；无变化时返回同一份值（值类型，语义等价于源码的同一实例）。
func (s SnapshotIdSet) Set(id int) SnapshotIdSet {
	offset := id - s.LowerBound
	switch {
	case offset >= 0 && offset < LongBits:
		if s.LowerSet&(1<<uint(offset)) == 0 {
			r := s.copy()
			r.LowerSet |= 1 << uint(offset)
			return r
		}
	case offset >= LongBits && offset < LongBits*2:
		if s.UpperSet&(1<<uint(offset-LongBits)) == 0 {
			r := s.copy()
			r.UpperSet |= 1 << uint(offset-LongBits)
			return r
		}
	case offset >= LongBits * 2:
		if s.Get(id) {
			return s
		}
		target := ((id + 1) / SnapshotIdSize) * SnapshotIdSize
		if target < 0 {
			target = SnapshotIdMax - SnapshotIdSize*2 + 1
		}
		r := s.copy()
		below := append([]int(nil), r.BelowBound...)
		for r.LowerBound < target {
			if r.LowerSet != 0 {
				for b := 0; b < LongBits; b++ {
					if r.LowerSet&(1<<uint(b)) != 0 {
						below = append(below, r.LowerBound+b)
					}
				}
			}
			if r.UpperSet == 0 {
				r.LowerBound = target
				r.LowerSet = 0
				break
			}
			r.LowerSet = r.UpperSet
			r.UpperSet = 0
			r.LowerBound += LongBits
		}
		sort.Ints(below)
		if len(below) > 0 {
			r.BelowBound = below
		}
		return r.Set(id)
	default: // offset < 0
		if s.BelowBound == nil {
			r := s.copy()
			r.BelowBound = []int{id}
			return r
		}
		i := sort.SearchInts(s.BelowBound, id)
		if i < len(s.BelowBound) && s.BelowBound[i] == id {
			return s
		}
		r := s.copy()
		nb := append([]int(nil), r.BelowBound...)
		nb = append(nb, 0)
		copy(nb[i+1:], nb[i:])
		nb[i] = id
		r.BelowBound = nb
		return r
	}
	return s
}

// Clear 清位。
func (s SnapshotIdSet) Clear(id int) SnapshotIdSet {
	offset := id - s.LowerBound
	switch {
	case offset >= 0 && offset < LongBits:
		if s.LowerSet&(1<<uint(offset)) != 0 {
			r := s.copy()
			r.LowerSet &^= 1 << uint(offset)
			return r
		}
	case offset >= LongBits && offset < LongBits*2:
		if s.UpperSet&(1<<uint(offset-LongBits)) != 0 {
			r := s.copy()
			r.UpperSet &^= 1 << uint(offset - LongBits)
			return r
		}
	case offset < 0 && s.BelowBound != nil:
		i := sort.SearchInts(s.BelowBound, id)
		if i < len(s.BelowBound) && s.BelowBound[i] == id {
			r := s.copy()
			nb := append([]int(nil), r.BelowBound...)
			r.BelowBound = append(nb[:i], nb[i+1:]...)
			return r
		}
	}
	return s
}

// AddRange 是 Snapshot.kt:2578 的 helper：until 排他，一个个 set。
func (s SnapshotIdSet) AddRange(from, until int) SnapshotIdSet {
	r := s
	for i := from; i < until; i++ {
		r = r.Set(i)
	}
	return r
}

// StateRecord 对应 StateRecord：多条记录按 snapshotId 串成链。
type StateRecord struct {
	SnapshotID int
	Value      int
	Next       *StateRecord
}

// Valid 对应 Snapshot.kt:2107 的三条件。
func Valid(current, candidate int, invalid SnapshotIdSet) bool {
	return candidate != InvalidSnapshot && candidate <= current && !invalid.Get(candidate)
}

// Readable 取合法记录中 snapshotId 最大的那条。
func Readable(first *StateRecord, id int, invalid SnapshotIdSet) *StateRecord {
	var candidate *StateRecord
	for cur := first; cur != nil; cur = cur.Next {
		if Valid(id, cur.SnapshotID, invalid) {
			if candidate == nil || candidate.SnapshotID < cur.SnapshotID {
				candidate = cur
			}
		}
	}
	return candidate
}

// ApplyCheck 复刻 innerApplyLocked 的碰撞判据：
//   current  = readable(first, nextId, 开着的快照去掉全局)
//   previous = readable(first, snapshotId, invalid ∪ {snapshotId} ∪ previousIds)
// 两者不同才需要问 mergeRecords；默认策略返回 nil 即 apply 失败。
type ApplyResult int

// 三种结果。
const (
	ApplySuccess ApplyResult = iota
	ApplyNoConflict
	ApplyConflict
)

// ApplyCheck 返回该状态对象的判定结果。
func ApplyCheck(first *StateRecord, snapshotID, nextID int, invalid, invalidSnapshots SnapshotIdSet,
	previousIDs []int, merge func(previous, current, applied *StateRecord) *StateRecord) ApplyResult {
	current := Readable(first, nextID, invalidSnapshots)
	previous := Readable(first, snapshotID, invalid.Set(snapshotID))
	for _, p := range previousIDs {
		previous = Readable(first, snapshotID, invalid.Set(snapshotID).Set(p))
	}
	if current == nil || previous == nil {
		return ApplySuccess
	}
	if previous.SnapshotID == PreexistingSnapshot {
		return ApplySuccess // 嵌套快照里新建的对象，直接采用 applied
	}
	if current == previous {
		return ApplyNoConflict
	}
	applied := Readable(first, snapshotID, invalid)
	if merge == nil || merge(previous, current, applied) == nil {
		return ApplyConflict
	}
	return ApplySuccess
}
