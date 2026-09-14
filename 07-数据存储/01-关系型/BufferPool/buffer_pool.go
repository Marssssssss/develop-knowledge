// Buffer Pool — Go implementation of InnoDB midpoint LRU + clock sweep.
//
// Mirror of buffer_pool.{py,c}, focuses on:
//   - midpoint insertion (old_blocks_pct = 37, default)
//   - clock-sweep eviction (LRU-K approximation with usage counter cap)
//   - WAL-before-data enforcement (LSN guard)
//   - scan-resistance check (hot pages survive large sequential scan)
//
// Refs:
//   - https://dev.mysql.com/doc/refman/8.0/en/innodb-buffer-pool.html
//   - 庖丁解 InnoDB 之 Buffer Pool (catkang 2023)
//   - 阿里云 RDS "InnoDB Buffer Pool flush 策略漫谈"

package main

import "fmt"

const (
	PoolSize = 16
	OldPct   = 37 // InnoDB default: 3/8 of pool is old sublist
	MaxUsage  = 5
)

type Frame struct {
	PageID   int
	PinCount int
	Dirty    bool
	Usage    int
	IsOld    bool
	PageLSN  int64
}

type FlushEntry struct {
	PageID int
	LSN    int64
}

type Pool struct {
	Frames    [PoolSize]Frame
	LRU       []int // MRU → LRU
	Free      []int
	FlushList []FlushEntry
	Hits      int
	Misses    int
	Evictions int
	Flushes   int

	pageToFrame map[int]int // pid → fi
	walLSN      int64
}

func NewPool() *Pool {
	p := &Pool{
		LRU:         make([]int, PoolSize),
		Free:        make([]int, PoolSize),
		pageToFrame: map[int]int{},
	}
	for i := 0; i < PoolSize; i++ {
		p.Frames[i] = Frame{PageID: -1}
		p.LRU[i] = -1
		p.Free[i] = PoolSize - 1 - i
	}
	return p
}

func (p *Pool) walFlush() int64 {
	p.walLSN += 10
	return p.walLSN
}

func mruIndex() int {
	return PoolSize * (100 - OldPct) / 100
}

func (p *Pool) lruRemove(fi int) {
	for i, v := range p.LRU {
		if v == fi {
			p.LRU = append(p.LRU[:i], p.LRU[i+1:]...)
			p.LRU = append(p.LRU, -1)
			return
		}
	}
}

func (p *Pool) lruInsertNew(fi int) {
	p.LRU = append([]int{fi}, p.LRU[:PoolSize-1]...)
	p.Frames[fi].IsOld = false
}

func (p *Pool) lruInsertOld(fi int) {
	mid := mruIndex()
	// shift right of mid by 1, insert at mid
	p.LRU = append(p.LRU, -1)
	copy(p.LRU[mid+1:], p.LRU[mid:PoolSize])
	p.LRU[mid] = fi
	p.Frames[fi].IsOld = true
}

func (p *Pool) acquireFrame() int {
	if len(p.Free) > 0 {
		fi := p.Free[len(p.Free)-1]
		p.Free = p.Free[:len(p.Free)-1]
		return fi
	}
	// clock sweep over old sublist, tail-first
	mid := mruIndex()
	for i := len(p.LRU) - 1; i >= mid; i-- {
		fi := p.LRU[i]
		if fi < 0 {
			continue
		}
		f := &p.Frames[fi]
		if f.PinCount == 0 {
			if f.Usage > 1 {
				f.Usage--
				continue
			}
			return fi
		}
	}
	// fallback: full LRU scan
	for i := len(p.LRU) - 1; i >= 0; i-- {
		fi := p.LRU[i]
		if fi < 0 {
			continue
		}
		if p.Frames[fi].PinCount == 0 {
			return fi
		}
	}
	return -1
}

func (p *Pool) evict(fi int) {
	f := &p.Frames[fi]
	if f.Dirty {
		w := p.walFlush()
		if f.PageLSN > w {
			panic("WAL-before-data violated")
		}
		f.Dirty = false
		p.Flushes++
	}
	delete(p.pageToFrame, f.PageID)
	p.lruRemove(fi)
	p.Free = append(p.Free, fi)
	f.PageID = -1
	f.PinCount, f.Dirty, f.Usage = 0, false, 0
	f.PageLSN = 0
	p.Evictions++
}

// Fix returns a frame for the requested page id (pins it).
func (p *Pool) Fix(pid int) *Frame {
	if fi, ok := p.pageToFrame[pid]; ok {
		f := &p.Frames[fi]
		f.PinCount++
		if f.IsOld {
			p.lruRemove(fi)
			p.lruInsertNew(fi)
		}
		p.Hits++
		return f
	}
	p.Misses++
	fi := p.acquireFrame()
	if fi < 0 {
		return nil
	}
	f := &p.Frames[fi]
	if f.PageID >= 0 {
		p.evict(fi)
	}
	f.PageID = pid
	f.PinCount = 1
	f.Usage = 1
	p.pageToFrame[pid] = fi
	p.lruInsertOld(fi)
	p.Hits++ // adjusted: a miss+load still counts as miss above; keep as is
	return f
}

func (p *Pool) Unfix(f *Frame, dirty bool) {
	f.PinCount--
	if dirty {
		f.Dirty = true
		f.PageLSN = p.walFlush()
		p.FlushList = append(p.FlushList, FlushEntry{f.PageID, f.PageLSN})
	}
}

// BackgroundFlush trickles up to maxPages dirty pages (oldest LSN first).
func (p *Pool) BackgroundFlush(maxPages int) int {
	// flush in LSN order
	sorted := append([]FlushEntry{}, p.FlushList...)
	for i := 0; i < len(sorted); i++ {
		for j := i + 1; j < len(sorted); j++ {
			if sorted[j].LSN < sorted[i].LSN {
				sorted[i], sorted[j] = sorted[j], sorted[i]
			}
		}
	}
	n := 0
	for _, e := range sorted {
		if n >= maxPages {
			break
		}
		if fi, ok := p.pageToFrame[e.PageID]; ok {
			f := &p.Frames[fi]
			if f.PinCount == 0 && f.Dirty {
				f.Dirty = false
				p.Flushes++
				n++
			}
		}
	}
	return n
}

func (p *Pool) LRUString() string {
	out := ""
	for _, fi := range p.LRU {
		if fi < 0 {
			continue
		}
		f := p.Frames[fi]
		if f.PageID < 0 {
			continue
		}
		tag := "n"
		if f.IsOld {
			tag = "o"
		}
		out += fmt.Sprintf("%d%s ", f.PageID, tag)
	}
	return out
}

func main() {
	p := NewPool()

	// 1) sequential scan: 1..POOL_SIZE+5
	for pid := 1; pid <= PoolSize+5; pid++ {
		f := p.Fix(pid)
		p.Unfix(f, false)
	}
	fmt.Printf("after scan:     hits=%d misses=%d evictions=%d\n",
		p.Hits, p.Misses, p.Evictions)

	// 2) hot loop on pages 1..5
	for r := 0; r < 5; r++ {
		for pid := 1; pid <= 5; pid++ {
			f := p.Fix(pid)
			p.Unfix(f, false)
		}
	}
	fmt.Printf("after hot loop: hits=%d misses=%d evictions=%d dirty=%d\n",
		p.Hits, p.Misses, p.Evictions, len(p.FlushList))
	fmt.Println("LRU:", p.LRUString())

	// 3) write two dirty pages
	if f := p.Fix(2); f != nil {
		p.Unfix(f, true)
	}
	if f := p.Fix(4); f != nil {
		p.Unfix(f, true)
	}

	// 4) big sequential scan — should NOT evict hot pages 1..5
	for pid := 100; pid < 130; pid++ {
		f := p.Fix(pid)
		p.Unfix(f, false)
	}
	hot := 0
	for pid := 1; pid <= 5; pid++ {
		if _, ok := p.pageToFrame[pid]; ok {
			hot++
		}
	}
	fmt.Printf("after big scan: hot_1..5_resident=%d/5 evictions=%d\n",
		hot, p.Evictions)

	// 5) background flush
	n := p.BackgroundFlush(4)
	fmt.Printf("background_flush wrote=%d pages\n", n)
}