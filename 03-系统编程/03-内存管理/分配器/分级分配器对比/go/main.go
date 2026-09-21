// Package main —— TCMalloc per-CPU 缓存的 Go 镜像（与 python/main.py 同构）。
//
// 静态最大容量 = 本档数组起点与下一档数组起点之差 / 指针大小（TCMalloc Design Doc）。
package main

import "fmt"

// PerCpuCache 是一个逻辑 CPU 的 slab 缓存。
type PerCpuCache struct {
	Classes    []int
	Slab       int
	Starts     map[int]int
	SlabEnd    int
	StaticCap  map[int]int
	Cap        map[int]int
	Cached     map[int]int
	Refills    int
	Spills     int
}

// NewPerCpuCache 按等分字节预算布局 slab，并由此算出各档静态容量。
func NewPerCpuCache(classes []int, slabBytes int) *PerCpuCache {
	c := &PerCpuCache{
		Classes: classes, Slab: slabBytes,
		Starts: map[int]int{}, StaticCap: map[int]int{},
		Cap: map[int]int{}, Cached: map[int]int{},
	}
	usable := slabBytes - HdrBytes*len(classes)
	if usable <= 0 {
		panic("slab too small for headers")
	}
	share := usable / len(classes)
	off := HdrBytes * len(classes)
	for _, s := range classes {
		c.Starts[s] = off
		off += share
	}
	c.SlabEnd = off
	for i, s := range classes {
		nxt := c.SlabEnd
		if i+1 < len(classes) {
			nxt = c.Starts[classes[i+1]]
		}
		c.StaticCap[s] = (nxt - c.Starts[s]) / PtrSize
		c.Cap[s] = c.StaticCap[s]
		c.Cached[s] = 0
	}
	return c
}

// SetCap 设置运行期容量，恒被静态容量夹住。
func (c *PerCpuCache) SetCap(class, v int) {
	if v < 0 {
		v = 0
	}
	if v > c.StaticCap[class] {
		v = c.StaticCap[class]
	}
	c.Cap[class] = v
}

// Get 从本档数组取一个对象；数组空则从 middle-end 批量补充。
func (c *PerCpuCache) Get(class int) bool {
	if c.Cached[class] > 0 {
		c.Cached[class]--
		return true
	}
	batch := c.Cap[class] / 2
	if batch < 1 {
		batch = 1
	}
	c.Refills++
	c.Cached[class] += batch - 1
	return true
}

// Put 归还一个对象；溢出则批量退回 middle-end。
func (c *PerCpuCache) Put(class int) {
	if c.Cached[class] >= c.Cap[class] {
		c.Spills++
		drop := c.Cap[class] / 2
		if drop < 1 {
			drop = 1
		}
		c.Cached[class] -= drop
		return
	}
	c.Cached[class]++
}

// Steal 从同 CPU 的 src 档偷槽位给 dst 档；受 src 余量与 dst 静态余量双重约束。
func (c *PerCpuCache) Steal(src, dst, slots int) int {
	room := c.StaticCap[dst] - c.Cap[dst]
	give := slots
	if c.Cap[src] < give {
		give = c.Cap[src]
	}
	if room < give {
		give = room
	}
	if give <= 0 {
		return 0
	}
	c.SetCap(src, c.Cap[src]-give)
	c.SetCap(dst, c.Cap[dst]+give)
	return give
}

// TotalBytes 返回当前缓存的字节数。
func (c *PerCpuCache) TotalBytes() int {
	n := 0
	for _, s := range c.Classes {
		n += c.Cached[s] * s
	}
	return n
}

// BudgetBytes 返回按容量上限能缓存的字节数。
func (c *PerCpuCache) BudgetBytes() int {
	n := 0
	for _, s := range c.Classes {
		n += c.Cap[s] * s
	}
	return n
}

// Release 对应 MallocExtension::ReleaseCpuMemory。
func (c *PerCpuCache) Release() int {
	n := c.TotalBytes()
	for _, s := range c.Classes {
		c.Cached[s] = 0
	}
	return n
}

// Machine 是一台多 CPU 机器：总缓存量随 CPU 数增长。
type Machine struct {
	NCPU   int
	Caches []*PerCpuCache
}

// NewMachine 构造 ncpu 个 per-CPU 缓存。
func NewMachine(ncpu int, classes []int, perCPULimit int) *Machine {
	m := &Machine{NCPU: ncpu}
	for i := 0; i < ncpu; i++ {
		m.Caches = append(m.Caches, NewPerCpuCache(classes, perCPULimit))
	}
	return m
}

// MaxCachedBytes 返回整机可缓存的字节上限。
func (m *Machine) MaxCachedBytes() int {
	n := 0
	for _, c := range m.Caches {
		n += c.BudgetBytes()
	}
	return n
}

func main() {
	cls := JemSizeClasses(64 * MiB)
	fmt.Println("jemalloc size classes (<=64MiB):", len(cls))
	fmt.Println("  first 12:", cls[:12])

	fmt.Println("mimalloc small/medium/large max obj:",
		MISmallMaxObjSize, MIMediumMaxObjSize, MILargeMaxObjSize)

	small := []int{16, 24, 32, 40}
	a8 := make([]int, 0, len(small))
	a16 := make([]int, 0, len(small))
	for _, s := range small {
		a8 = append(a8, TcmRoundUpSmall(s, 8))
		a16 = append(a16, TcmRoundUpSmall(s, 16))
	}
	fmt.Println("tcmalloc 8B align :", a8)
	fmt.Println("tcmalloc 16B align:", a16)

	c := NewPerCpuCache([]int{16, 32, 64}, 1*MiB)
	for _, s := range []int{16, 32, 64} {
		fmt.Printf("  class %4d: start=%d staticCap=%d\n", s, c.Starts[s], c.StaticCap[s])
	}
	m1 := NewMachine(1, []int{16, 32, 64}, 1*MiB)
	m4 := NewMachine(4, []int{16, 32, 64}, 1*MiB)
	fmt.Println("1 CPU budget:", m1.MaxCachedBytes(), " 4 CPU budget:", m4.MaxCachedBytes())

	f, n := JemPurgeFraction(5000, JemDirtyDecayMs, 1000)
	fmt.Println("dirty decay @5s:", f, n)
}
