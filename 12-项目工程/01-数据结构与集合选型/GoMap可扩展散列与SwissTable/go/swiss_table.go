// Package main —— Go 1.24+ map 的可扩展散列目录 + Swiss table 模型（Go 侧同口径实现）。
//
// 常量与分支逐条照抄 internal/runtime/maps 的 table.go / map.go / group.go。
package main

const (
	mapGroupSlots    = 8   // abi.MapGroupSlots
	maxTableCapacity = 1024
	maxAvgGroupLoad  = 7   // 7/8 负载因子，与 Abseil 一致

	ctrlEmpty   = 0x80
	ctrlDeleted = 0xFE
)

// h1 高 57 位，用来选组。
func h1(h uint64) uint64 { return h >> 7 }

// h2 低 7 位，当控制字节。
func h2(h uint64) uint64 { return h & 0x7f }

// alignUpPow2 向上取到 2 的幂（组数必须是 2 的幂，三角探测才能遍历所有组）。
func alignUpPow2(n int) int {
	if n <= 1 {
		return 1
	}
	v := n - 1
	k := 0
	for v > 0 {
		k++
		v >>= 1
	}
	return 1 << k
}

// newTableCapacity 复刻 newTable 的取整：下限 8、上限 1024、向上取 2 的幂。
func newTableCapacity(requested int) int {
	c := requested
	if c < mapGroupSlots {
		c = mapGroupSlots
	}
	if c > maxTableCapacity {
		panic("initial table capacity too large")
	}
	return alignUpPow2(c)
}

// maxGrowthLeft 复刻 table.maxGrowthLeft：单组表留一个空槽，大表按 7/8。
func maxGrowthLeft(capacity int) int {
	if capacity <= mapGroupSlots {
		return capacity - 1
	}
	return (capacity * maxAvgGroupLoad) / mapGroupSlots
}

// directoryIndex 复刻 Map.directoryIndex：dirLen==1 时恒为 0。
func directoryIndex(hash uint64, dirLen, globalShift int) int {
	if dirLen == 1 {
		return 0
	}
	return int(hash >> (globalShift & 63))
}

// localDepthMask 复刻 localDepthMask：1 << (64 - localDepth)。
func localDepthMask(localDepth int) uint64 { return uint64(1) << (64 - localDepth) }

// probeGroups 复刻 probeSeq 的组下标序列：三角数推进。
func probeGroups(h1Value uint64, groupsMask, limit int) []int {
	offset := int(h1Value) & groupsMask
	index := 0
	out := make([]int, 0, limit)
	for i := 0; i < limit; i++ {
		out = append(out, offset)
		index++
		offset = (offset + index) & groupsMask
	}
	return out
}

// slot 一个槽位。
type slot struct {
	key  string
	hash uint64
}

// table 对应 internal/runtime/maps.table。
type table struct {
	capacity   int
	ctrl       []byte
	slots      []*slot
	used       int
	growthLeft int
	localDepth int
	index      int
}

func newTable(capacity, index, localDepth int) *table {
	c := newTableCapacity(capacity)
	t := &table{
		capacity:   c,
		ctrl:       make([]byte, c),
		slots:      make([]*slot, c),
		growthLeft: maxGrowthLeft(c),
		localDepth: localDepth,
		index:      index,
	}
	for i := range t.ctrl {
		t.ctrl[i] = ctrlEmpty
	}
	return t
}

func (t *table) groupCount() int     { return t.capacity / mapGroupSlots }
func (t *table) groupsMask() int     { return t.groupCount() - 1 }
func (t *table) tombstones() int     { n := 0; for _, c := range t.ctrl { if c == ctrlDeleted { n++ } }; return n }
func (t *table) seq(h uint64) []int  { return probeGroups(h1(h), t.groupsMask(), t.groupCount()) }

func (t *table) groupHasEmpty(base int) bool {
	for j := base; j < base+mapGroupSlots; j++ {
		if t.ctrl[j] == ctrlEmpty {
			return true
		}
	}
	return false
}

// uncheckedPut 复刻 uncheckedPutSlot：grow/split 时用，已知 key 不在表里。
func (t *table) uncheckedPut(key string, h uint64) {
	tag := byte(h2(h))
	for _, gi := range t.seq(h) {
		base := gi * mapGroupSlots
		for j := base; j < base+mapGroupSlots; j++ {
			if t.ctrl[j] == ctrlEmpty {
				t.ctrl[j] = tag
				t.slots[j] = &slot{key: key, hash: h}
				t.used++
				t.growthLeft--
				return
			}
		}
	}
	panic("no empty slot")
}

// put 返回 true 表示落位成功，false 表示需要 rehash 后重试。
func (t *table) put(key string, h uint64) bool {
	tag := byte(h2(h))
	firstDeleted := -1
	for _, gi := range t.seq(h) {
		base := gi * mapGroupSlots
		for j := base; j < base+mapGroupSlots; j++ {
			if t.ctrl[j] == tag && t.slots[j] != nil && t.slots[j].key == key {
				return true
			}
		}
		if firstDeleted < 0 {
			for j := base; j < base+mapGroupSlots; j++ {
				if t.ctrl[j] == ctrlDeleted {
					firstDeleted = j
					break
				}
			}
		}
		if t.groupHasEmpty(base) {
			target := -1
			for j := base; j < base+mapGroupSlots; j++ {
				if t.ctrl[j] == ctrlEmpty {
					target = j
					break
				}
			}
			if firstDeleted >= 0 {
				target = firstDeleted
				t.growthLeft++ // 紧接着的 -- 变成空操作
			}
			if t.growthLeft == 0 {
				t.pruneTombstones()
			}
			if t.growthLeft > 0 {
				t.ctrl[target] = tag
				t.slots[target] = &slot{key: key, hash: h}
				t.growthLeft--
				t.used++
				return true
			}
			return false
		}
	}
	return false
}

// delete 返回是否产生了墓碑：组里还有空槽就置 EMPTY 并归还配额，否则留墓碑。
func (t *table) delete(key string, h uint64) bool {
	tag := byte(h2(h))
	for _, gi := range t.seq(h) {
		base := gi * mapGroupSlots
		for j := base; j < base+mapGroupSlots; j++ {
			if t.ctrl[j] == tag && t.slots[j] != nil && t.slots[j].key == key {
				t.used--
				t.slots[j] = nil
				if t.groupHasEmpty(base) {
					t.ctrl[j] = ctrlEmpty
					t.growthLeft++
					return false
				}
				t.ctrl[j] = ctrlDeleted
				return true
			}
		}
		if t.groupHasEmpty(base) {
			return false
		}
	}
	return false
}

// pruneTombstones 复刻 pruneTombstones：墓碑不足容量 10% 时直接放弃。
func (t *table) pruneTombstones() int {
	if t.tombstones()*10 < t.capacity {
		return 0
	}
	removed := 0
	for gi := 0; gi < t.groupCount(); gi++ {
		base := gi * mapGroupSlots
		if !t.groupHasEmpty(base) {
			continue
		}
		for j := base; j < base+mapGroupSlots; j++ {
			if t.ctrl[j] == ctrlDeleted {
				t.ctrl[j] = ctrlEmpty
				t.growthLeft++
				removed++
			}
		}
	}
	return removed
}

// rehash 复刻 table.rehash：能 grow 就 grow，否则 split。
func (t *table) rehash(m *goMap) {
	newCapacity := 2 * t.capacity
	if newCapacity <= maxTableCapacity {
		t.grow(m, newCapacity)
		return
	}
	t.split(m)
}

func (t *table) grow(m *goMap, newCapacity int) {
	nt := newTable(newCapacity, t.index, t.localDepth)
	for _, s := range t.slots {
		if s != nil {
			nt.uncheckedPut(s.key, s.hash)
		}
	}
	m.replaceTable(nt)
	t.index = -1
}

func (t *table) split(m *goMap) {
	localDepth := t.localDepth + 1
	left := newTable(maxTableCapacity, -1, localDepth)
	right := newTable(maxTableCapacity, -1, localDepth)
	mask := localDepthMask(localDepth)
	for _, s := range t.slots {
		if s == nil {
			continue
		}
		if s.hash&mask == 0 {
			left.uncheckedPut(s.key, s.hash)
		} else {
			right.uncheckedPut(s.key, s.hash)
		}
	}
	m.installTableSplit(t, left, right)
	t.index = -1
}
