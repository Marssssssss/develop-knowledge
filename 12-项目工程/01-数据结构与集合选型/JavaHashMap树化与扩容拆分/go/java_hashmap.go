// Package main —— OpenJDK java.util.HashMap 的树化 / 退化 / 扩容拆分模型（Go 侧同口径实现）。
//
// 常量与分支条件逐条照抄
// src/java.base/share/classes/java/util/HashMap.java。
package main

const (
	defaultInitialCapacity = 1 << 4  // 16
	maximumCapacity        = 1 << 30
	defaultLoadFactor      = 0.75
	treeifyThreshold       = 8
	untreeifyThreshold     = 6
	minTreeifyCapacity     = 64

	intMax = 1<<31 - 1
	intMin = -1 << 31
)

const u32 = 1<<32 - 1

// asInt32 把任意整数折成 Java int（有符号 32 位）。
func asInt32(x int64) int32 {
	x &= u32
	if x >= 1<<31 {
		return int32(x - 1<<32)
	}
	return int32(x)
}

// numberOfLeadingZeros 复刻 Integer.numberOfLeadingZeros：0 返回 32。
func numberOfLeadingZeros(x int32) int {
	u := uint32(x)
	if u == 0 {
		return 32
	}
	n := 0
	for u&(1<<31) == 0 {
		n++
		u <<= 1
	}
	return n
}

// tableSizeFor 复刻 tableSizeFor。
//
// 关键：Java 的移位量会 & 31，所以 numberOfLeadingZeros(0)==32 时
// `-1 >>> 32` 实际是 `-1 >>> 0 == -1`，落在 `n < 0` 分支返回 1。
func tableSizeFor(cap int32) int32 {
	if cap <= 0 {
		cap = 1
	}
	nMinus := asInt32(int64(cap) - 1)
	nlz := numberOfLeadingZeros(nMinus)
	n := asInt32(int64(uint32(u32) >> (nlz & 31)))
	if n < 0 {
		return 1
	}
	if n >= maximumCapacity {
		return maximumCapacity
	}
	return asInt32(int64(n) + 1)
}

// spread 复刻 hash(key)：h ^ (h >>> 16)。
func spread(h int32) int32 {
	u := uint32(h)
	return asInt32(int64(u ^ (u >> 16)))
}

// indexFor 复刻 tab[i = (n-1) & hash]。
func indexFor(h int32, n int) int { return int(uint32(spread(h)) & uint32(n-1)) }

// nextCapacityAfterResize 复刻 resize() 里 newCap / newThr 的推导（不含搬移）。
func nextCapacityAfterResize(oldCap, oldThr int32, loadFactor float64) (int32, int32) {
	var newThr int32
	var newCap int32
	switch {
	case oldCap > 0:
		if oldCap >= maximumCapacity {
			return oldCap, intMax
		}
		newCap = asInt32(int64(oldCap) << 1)
		if newCap < maximumCapacity && oldCap >= defaultInitialCapacity {
			newThr = asInt32(int64(oldThr) << 1)
		}
	case oldThr > 0:
		newCap = oldThr
	default:
		newCap = defaultInitialCapacity
		newThr = int32(defaultLoadFactor * defaultInitialCapacity)
	}
	if newThr == 0 {
		ft := float64(newCap) * loadFactor
		if newCap < maximumCapacity && ft < float64(maximumCapacity) {
			newThr = int32(ft) // 截断，不是四舍五入
		} else {
			newThr = intMax
		}
	}
	return newCap, newThr
}

// node 对应 HashMap.Node。
type node struct {
	hash  int32
	key   string
	value any
}

// bin 一个桶：链或树。isTree 只记"是不是树"，next 链顺序始终保留
// （TreeNode 同时维护 prev/next 与红黑链接）。
type bin struct {
	nodes  []*node
	isTree bool
}

// hashMap 只建模影响扩容 / 树化 / 拆分的那部分状态。
type hashMap struct {
	table      []*bin
	threshold  int32
	loadFactor float64
	size       int
	resizes    int
	treeifies  int
	untreeify  int
}

// newHashMap：initialCapacity < 0 表示无参构造器 new HashMap()（threshold 保持 0）。
func newHashMap(initialCapacity int32, loadFactor float64) *hashMap {
	thr := int32(0)
	if initialCapacity >= 0 {
		thr = tableSizeFor(initialCapacity)
	}
	return &hashMap{threshold: thr, loadFactor: loadFactor}
}

func (m *hashMap) capacity() int {
	if m.table == nil {
		return 0
	}
	return len(m.table)
}

func (m *hashMap) treeBuckets() int {
	n := 0
	for _, b := range m.table {
		if b != nil && b.isTree {
			n++
		}
	}
	return n
}

// splitByBit 复刻 resize() 的 lo/hi 拆分：(e.hash & oldCap) == 0 留在 j，否则去 j+oldCap。
// 判据用 spread 之后的 hash，并保持原链表相对顺序。
func splitByBit(nodes []*node, bit int32) ([]*node, []*node) {
	var lo, hi []*node
	for _, e := range nodes {
		if uint32(spread(e.hash))&uint32(bit) == 0 {
			lo = append(lo, e)
		} else {
			hi = append(hi, e)
		}
	}
	return lo, hi
}

// splitTree 复刻 TreeNode.split：只有拆成两半时才需要重新 treeify
// （源码里是 if (hiHead != null) loHead.treeify(tab)，hi 侧对称看 loHead）。
func (m *hashMap) splitTree(newTab []*bin, b *bin, index int, bit int32) {
	lo, hi := splitByBit(b.nodes, bit)
	if len(lo) > 0 {
		if len(lo) <= untreeifyThreshold {
			newTab[index] = &bin{nodes: lo}
			m.untreeify++
		} else {
			newTab[index] = &bin{nodes: lo, isTree: true}
			if len(hi) > 0 {
				m.treeifies++
			}
		}
	}
	if len(hi) > 0 {
		if len(hi) <= untreeifyThreshold {
			newTab[index+int(bit)] = &bin{nodes: hi}
			m.untreeify++
		} else {
			newTab[index+int(bit)] = &bin{nodes: hi, isTree: true}
			if len(lo) > 0 {
				m.treeifies++
			}
		}
	}
}

func (m *hashMap) resize() {
	oldCap := int32(m.capacity())
	oldTab := m.table
	if oldCap >= maximumCapacity {
		m.threshold = intMax
		return
	}
	newCap, newThr := nextCapacityAfterResize(oldCap, m.threshold, m.loadFactor)
	m.threshold = newThr
	newTab := make([]*bin, newCap)
	for j, b := range oldTab {
		if b == nil {
			continue
		}
		if len(b.nodes) == 1 {
			newTab[indexFor(b.nodes[0].hash, int(newCap))] = b
			continue
		}
		if b.isTree {
			m.splitTree(newTab, b, j, oldCap)
			continue
		}
		lo, hi := splitByBit(b.nodes, oldCap)
		if len(lo) > 0 {
			newTab[j] = &bin{nodes: lo}
		}
		if len(hi) > 0 {
			newTab[j+int(oldCap)] = &bin{nodes: hi}
		}
	}
	m.table = newTab
	m.resizes++
}

// treeifyBin 复刻 treeifyBin：表长 < MIN_TREEIFY_CAPACITY 时先扩容而不是树化。
func (m *hashMap) treeifyBin(tab []*bin) bool {
	if len(tab) < minTreeifyCapacity {
		return false // 走 resize
	}
	return true // 走 treeify
}

func (m *hashMap) put(key string, h int32, value any) {
	if m.table == nil {
		m.resize()
	}
	tab := m.table
	i := indexFor(h, len(tab))
	b := tab[i]
	if b == nil {
		tab[i] = &bin{nodes: []*node{{hash: h, key: key, value: value}}}
	} else {
		for _, e := range b.nodes {
			if e.hash == h && e.key == key {
				e.value = value
				return
			}
		}
		b.nodes = append(b.nodes, &node{hash: h, key: key, value: value})
		binCount := len(b.nodes) - 2 // putVal 里 binCount 从 0 起算
		if binCount >= treeifyThreshold-1 {
			if m.treeifyBin(tab) {
				b.isTree = true
				m.treeifies++
			} else {
				m.resize()
			}
		}
	}
	m.size++
	if int32(m.size) > m.threshold {
		m.resize()
	}
}
