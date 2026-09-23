package main

// mapLayout 是 NewMap(hint) 推导出来的目录布局。
type mapLayout struct {
	small          bool
	dirLen         int
	globalDepth    int
	globalShift    int
	tableCapacity  int
	targetCapacity int
}

// newMapLayout 复刻 NewMap 的容量推导（不含分配）。
func newMapLayout(hint int) mapLayout {
	if hint <= mapGroupSlots {
		return mapLayout{small: true, globalShift: 64}
	}
	target := (hint * mapGroupSlots) / maxAvgGroupLoad
	dirSize := (target + maxTableCapacity - 1) / maxTableCapacity
	dirSize = alignUpPow2(dirSize)
	globalDepth := 0
	for (1 << globalDepth) < dirSize {
		globalDepth++
	}
	return mapLayout{
		small:          false,
		dirLen:         dirSize,
		globalDepth:    globalDepth,
		globalShift:    64 - globalDepth,
		tableCapacity:  newTableCapacity(target / dirSize),
		targetCapacity: target,
	}
}

// goMap 对应 internal/runtime/maps.Map。
type goMap struct {
	small         bool
	smallSlots    []*slot
	dir           []*table
	globalDepth   int
	used          int
	grows         int
	splits        int
	dirDoublings  int
}

func newGoMap(hint int) *goMap {
	lay := newMapLayout(hint)
	m := &goMap{globalDepth: lay.globalDepth}
	if lay.small {
		m.small = true
		m.smallSlots = make([]*slot, mapGroupSlots)
		return m
	}
	for i := 0; i < lay.dirLen; i++ {
		m.dir = append(m.dir, newTable(lay.tableCapacity, i, lay.globalDepth))
	}
	return m
}

func (m *goMap) globalShift() int { return 64 - m.globalDepth }

func (m *goMap) directoryIndex(h uint64) int {
	if len(m.dir) == 1 {
		return 0
	}
	return int(h >> (m.globalShift() & 63))
}

// replaceTable 一个 table 可能占据连续 2^(globalDepth-localDepth) 个目录项。
func (m *goMap) replaceTable(nt *table) {
	entries := 1 << (m.globalDepth - nt.localDepth)
	for k := 0; k < entries; k++ {
		m.dir[nt.index+k] = nt
	}
}

func (m *goMap) installTableSplit(old, left, right *table) {
	if old.localDepth == m.globalDepth {
		newDir := make([]*table, 0, len(m.dir)*2)
		for i := range m.dir {
			t := m.dir[i]
			newDir = append(newDir, t, t)
			if t.index == i {
				t.index = 2 * i
			}
		}
		m.dir = newDir
		m.globalDepth++
		m.dirDoublings++
	}
	left.index = old.index
	m.replaceTable(left)
	right.index = left.index + (1 << (m.globalDepth - left.localDepth))
	m.replaceTable(right)
	m.splits++
}

func (m *goMap) put(key string, h uint64) {
	if m.small {
		for _, s := range m.smallSlots {
			if s != nil && s.key == key {
				return
			}
		}
		for i, s := range m.smallSlots {
			if s == nil {
				m.smallSlots[i] = &slot{key: key, hash: h}
				m.used++
				return
			}
		}
		m.growToTable()
		return
	}
	for i := 0; i < 64; i++ {
		t := m.dir[m.directoryIndex(h)]
		if t.put(key, h) {
			m.used++
			return
		}
		before := len(m.dir)
		t.rehash(m)
		if len(m.dir) == before {
			m.grows++
		}
	}
	panic("put did not settle")
}

// growToTable 小图满了之后分配到 **2 * MapGroupSlots = 16** 槽的表。
func (m *goMap) growToTable() {
	m.small = false
	t := newTable(2*mapGroupSlots, 0, 0)
	for _, s := range m.smallSlots {
		if s != nil {
			t.uncheckedPut(s.key, s.hash)
		}
	}
	m.dir = []*table{t}
	m.globalDepth = 0
}

func (m *goMap) tables() []*table {
	var seen []*table
	for _, t := range m.dir {
		dup := false
		for _, s := range seen {
			if s == t {
				dup = true
				break
			}
		}
		if !dup {
			seen = append(seen, t)
		}
	}
	return seen
}

func (m *goMap) totalCapacity() int {
	n := 0
	for _, t := range m.tables() {
		n += t.capacity
	}
	return n
}
