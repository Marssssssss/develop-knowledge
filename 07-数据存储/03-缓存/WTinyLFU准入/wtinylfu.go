// Window TinyLFU 的 Go 实现（与 wtinylfu.py 同构，断言逐条对齐）。
//
// 构建/运行： go run wtinylfu.go
package main

import (
	"container/list"
	"fmt"
)

// ------------------------------------------------------------ 有序集合

type lruSet struct {
	l *list.List
	m map[string]*list.Element
}

func newLRUSet() *lruSet { return &lruSet{l: list.New(), m: map[string]*list.Element{}} }

func (s *lruSet) len() int      { return s.l.Len() }
func (s *lruSet) peek() string  { return s.l.Front().Value.(string) }
func (s *lruSet) contains(k string) bool { _, ok := s.m[k]; return ok }

func (s *lruSet) add(k string) {
	if e, ok := s.m[k]; ok {
		s.l.MoveToBack(e)
		return
	}
	s.m[k] = s.l.PushBack(k)
}

func (s *lruSet) touch(k string) { s.l.MoveToBack(s.m[k]) }

func (s *lruSet) remove(k string) {
	if e, ok := s.m[k]; ok {
		s.l.Remove(e)
		delete(s.m, k)
	}
}

func (s *lruSet) pop() string {
	e := s.l.Front()
	s.l.Remove(e)
	delete(s.m, e.Value.(string))
	return e.Value.(string)
}

// ------------------------------------------------------------ 频率草图

const (
	counterBits     = 4
	counterMax      = 15
	countersPerLong = 16
	numHashes       = 4
)

func hash64(s string) uint64 {
	h := uint64(0xC4CEB9FE1A85EC53)
	for i := 0; i < len(s); i++ {
		h = (h ^ uint64(s[i])) * 0x9E3779B97F4A7C15
	}
	h ^= h >> 32
	h *= 0xBF58476D1CE4E5B9
	h ^= h >> 32
	return h
}

func rehash(h uint64) uint64 {
	h *= 0xFF51AFD7ED558CCD
	h ^= h >> 33
	return h
}

// FrequencySketch：4-bit Count-Min Sketch。
// 布局对齐 Caffeine：ceiling_pow2(capacity) 个 long × 16 个 4-bit 计数器
// → 合计 8 字节/条目。
type FrequencySketch struct {
	nLongs    int
	nCounters int
	table     []byte
	resetThr  int
	size      int
}

func NewFrequencySketch(capacity int) *FrequencySketch {
	n := 1
	for n < capacity {
		n <<= 1
	}
	return &FrequencySketch{
		nLongs: n, nCounters: n * countersPerLong,
		table: make([]byte, n*countersPerLong), resetThr: 10 * capacity,
	}
}

func (f *FrequencySketch) memoryBytes() int { return f.nCounters * counterBits / 8 }

func (f *FrequencySketch) indices(k string) [numHashes]int {
	h := hash64(k)
	block := (h & uint64(f.nLongs-1)) * countersPerLong
	h2 := rehash(h)
	var out [numHashes]int
	for i := 0; i < numHashes; i++ {
		out[i] = int(block) + int((h2>>(i*4))&0xF)
	}
	return out
}

func (f *FrequencySketch) Increment(k string) {
	f.size++
	if f.size >= f.resetThr {
		f.Reset()
	}
	for i, idx := range f.indices(k) {
		_ = i
		if f.table[idx] < counterMax {
			f.table[idx]++
		}
	}
}

// Frequency：Count-Min 的估计量 = 4 个计数器的**最小值**。
func (f *FrequencySketch) Frequency(k string) int {
	idx := f.indices(k)
	m := int(f.table[idx[0]])
	for i := 1; i < numHashes; i++ {
		if v := int(f.table[idx[i]]); v < m {
			m = v
		}
	}
	return m
}

// Reset：老化 —— 全体减半，使「历史频率」随时间衰减。
func (f *FrequencySketch) Reset() {
	for i := range f.table {
		f.table[i] >>= 1
	}
	f.size >>= 1
}

// ------------------------------------------------------------ 基线 LRU

type LRUCache struct {
	cap       int
	s         *lruSet
	hits, mis int
}

func NewLRU(cap int) *LRUCache { return &LRUCache{cap: cap, s: newLRUSet()} }

func (c *LRUCache) Access(k string) bool {
	if c.s.contains(k) {
		c.hits++
		c.s.touch(k)
		return true
	}
	c.mis++
	c.s.add(k)
	if c.s.len() > c.cap {
		c.s.pop()
	}
	return false
}

// ------------------------------------------------------------ W-TinyLFU

type WTinyLFU struct {
	cap                      int
	windowPct, protectedPct  float64
	windowMax, protectedMax  int
	win, prob, prot          *lruSet
	sketch                   *FrequencySketch
	hits, misses             int
	admitted, rejected       int
	adaptive                 bool
}

func NewWTinyLFU(cap int, windowPct float64, adaptive bool) *WTinyLFU {
	c := &WTinyLFU{
		cap: cap, windowPct: windowPct, protectedPct: 0.80,
		win: newLRUSet(), prob: newLRUSet(), prot: newLRUSet(),
		sketch: NewFrequencySketch(cap), adaptive: adaptive,
	}
	c.applySizes()
	return c
}

func (c *WTinyLFU) applySizes() {
	c.windowMax = int(float64(c.cap) * c.windowPct)
	c.protectedMax = int(float64(c.cap-c.windowMax) * c.protectedPct)
}

func (c *WTinyLFU) size() int { return c.win.len() + c.prob.len() + c.prot.len() }

func (c *WTinyLFU) Access(k string) bool {
	c.sketch.Increment(k)
	switch {
	case c.win.contains(k):
		c.hits++
		c.win.touch(k)
		return true
	case c.prot.contains(k):
		c.hits++
		c.prot.touch(k)
		return true
	case c.prob.contains(k):
		c.hits++
		c.promote(k)
		return true
	}
	c.misses++
	c.win.add(k)
	c.evict()
	return false
}

func (c *WTinyLFU) promote(k string) {
	c.prob.remove(k)
	c.prot.add(k)
	for c.prot.len() > c.protectedMax {
		c.prob.add(c.prot.pop()) // 降级回 probation
	}
}

func (c *WTinyLFU) mainVictim() (*lruSet, string) {
	if c.prob.len() > 0 {
		return c.prob, c.prob.peek()
	}
	if c.prot.len() > 0 {
		return c.prot, c.prot.peek()
	}
	return nil, ""
}

func (c *WTinyLFU) evict() {
	// 阶段一：窗口超出配额时，把窗口 LRU 交给主区裁决
	for c.win.len() > c.windowMax {
		cand := c.win.pop()
		if c.size() < c.cap {
			// 还没装满 —— 直接降级进 probation，不做准入测试。
			// 否则冷启动时所有条目频率都是 1，判据 `>` 会拒掉一切。
			c.prob.add(cand)
			continue
		}
		set, victim := c.mainVictim()
		if set != nil && c.sketch.Frequency(cand) > c.sketch.Frequency(victim) {
			c.admitted++
			set.remove(victim)
			c.prob.add(cand)
		} else {
			c.rejected++ // 候选被直接丢弃
		}
	}
	// 阶段二：总量超限，从 probation 的 LRU 端淘汰
	for c.size() > c.cap {
		if c.prob.len() > 0 {
			c.prob.pop()
		} else if c.prot.len() > 0 {
			c.prob.add(c.prot.pop())
		} else {
			break
		}
	}
}

// HillClimb：简化版爬山 —— 命中率高的一方扩张。
func (c *WTinyLFU) HillClimb(winHR, mainHR float64) {
	if !c.adaptive {
		return
	}
	if winHR > mainHR {
		c.windowPct = min(0.50, c.windowPct+0.01)
	} else {
		c.windowPct = max(0.01, c.windowPct-0.01)
	}
	c.applySizes()
}

func min(a, b float64) float64 { if a < b { return a }; return b }
func max(a, b float64) float64 { if a > b { return a }; return b }
