// brk/sbrk 与 mmap —— 进程地址空间接口层的语义模型(Go 版)。
//
// 权威来源(Linux man-pages 6.19,实际联网阅读):
//   - man7.org/linux/man-pages/man2/brk.2.html
//   - man7.org/linux/man-pages/man2/mmap.2.html
//   - man7.org/linux/man-pages/man3/malloc.3.html
//   - man7.org/linux/man-pages/man3/mallopt.3.html
//
// 只复现手册明文规定的语义;手册未规定者不断言。
package main

import "fmt"

const (
	page        = 4096
	hugePage    = 2 * 1024 * 1024
	mapFailed   = -1
	eInval      = 22
	eNomen      = 12
	eExist      = 17
	protRead    = 0x1
	protWrite   = 0x2
	mapShared   = 0x01
	mapPrivate  = 0x02
	mapFixed    = 0x10
	mapNoRepl   = 0x100000
	mapAnon     = 0x20
	heapStart   = 0x0000555500000000
	mmapBase    = 0x00007F0000000000
	mmapThresh  = 128 * 1024
	mmapThreshM = 4 * 1024 * 1024 * 8 // 64 位:4*1024*1024*sizeof(long)
	trimThresh  = 128 * 1024
	mmapMax     = 65536
)

type osErr struct {
	errno int
	what  string
}

func (e *osErr) Error() string { return fmt.Sprintf("%s: errno=%d", e.what, e.errno) }

func alignUp(x int) int   { return (x + page - 1) &^ (page - 1) }
func alignDown(x int) int { return x &^ (page - 1) }

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

type mapping struct {
	start  int
	length int
	flags  int
	offset int
	data   []byte
}

type addrSpace struct {
	heapMin       int
	brk           int
	nextHint      int
	maps          []*mapping
	unmappedSpans [][2]int
}

func newSpace() *addrSpace {
	return &addrSpace{heapMin: heapStart, brk: heapStart, nextHint: mmapBase}
}

// sysBrk 复现 Linux brk(2) **系统调用**语义:成功返回新 break,失败返回**当前** break。
func (a *addrSpace) sysBrk(addr int) int {
	if addr < a.heapMin || addr-a.heapMin > 1<<40 {
		return a.brk
	}
	a.brk = addr
	return addr
}

// glibcBrk 复现 glibc 包装:成功返回 0,失败返回 -1(errno=ENOMEM)。
func (a *addrSpace) glibcBrk(addr int) (int, error) {
	if a.sysBrk(addr) != addr {
		return -1, &osErr{eNomen, "brk"}
	}
	return 0, nil
}

// sbrk 复现 glibc 库函数:成功返回**旧的** program break,失败返回 (void*)-1。
func (a *addrSpace) sbrk(inc int) int {
	old := a.brk
	if inc == 0 {
		return old
	}
	if a.sysBrk(old+inc) != old+inc {
		return mapFailed
	}
	return old
}

func (a *addrSpace) overlaps(start, length int) bool {
	end := start + length
	for _, m := range a.maps {
		if m.start < end && start < m.start+m.length {
			return true
		}
	}
	return false
}

func (a *addrSpace) mmap(addr, length, flags, offset int, huge bool) (int, error) {
	if length <= 0 {
		return mapFailed, &osErr{eInval, "mmap: length must be > 0"}
	}
	if flags&(mapPrivate|mapShared) != mapPrivate && flags&(mapPrivate|mapShared) != mapShared {
		return mapFailed, &osErr{eInval, "mmap: exactly one of MAP_PRIVATE/MAP_SHARED"}
	}
	unit := page
	if huge {
		unit = hugePage
	}
	if offset%unit != 0 {
		return mapFailed, &osErr{eInval, fmt.Sprintf("mmap: offset must be a multiple of %d", unit)}
	}

	var start int
	if flags&(mapFixed|mapNoRepl) != 0 {
		if addr%unit != 0 {
			return mapFailed, &osErr{eInval, "mmap: fixed addr must be suitably aligned"}
		}
		start = addr
		if a.overlaps(start, length) {
			if flags&mapNoRepl != 0 {
				return mapFailed, &osErr{eExist, "mmap: MAP_FIXED_NOREPLACE hit existing mapping"}
			}
			a.discard(start, length)
		}
	} else {
		start = a.pick(addr, length, unit)
	}

	span := alignUp(length)
	if huge {
		span = length
	}
	a.maps = append(a.maps, &mapping{start: start, length: span, flags: flags, offset: offset,
		data: make([]byte, span)}) // MAP_ANONYMOUS:内容初始化为 0
	return start, nil
}

func (a *addrSpace) pick(addr, length, unit int) int {
	span := alignUp(length)
	cand := a.nextHint
	if addr != 0 {
		cand = addr
	}
	cand = (cand + unit - 1) &^ (unit - 1)
	for a.overlaps(cand, span) {
		cand = alignUp(cand + span)
	}
	a.nextHint = cand + span
	return cand
}

// discard 只丢弃 [start,start+length) 覆盖到的部分,其余部分必须保留。
//
// 手册原文:mmap(2) MAP_FIXED "the overlapped part of the existing mapping(s)
// will be discarded";munmap(2) 同样只卸载范围内的页。
func (a *addrSpace) discard(start, length int) {
	lo, hi := start, start+length
	kept := make([]*mapping, 0, len(a.maps))
	for _, m := range a.maps {
		mLo, mHi := m.start, m.start+m.length
		if mLo >= hi || lo >= mHi {
			kept = append(kept, m)
			continue
		}
		a.unmappedSpans = append(a.unmappedSpans, [2]int{maxInt(lo, mLo), minInt(hi, mHi)})
		if mLo < lo { // 保留左段
			keep := lo - mLo
			kept = append(kept, &mapping{start: mLo, length: keep, flags: m.flags,
				offset: m.offset, data: m.data[:keep]})
		}
		if hi < mHi { // 保留右段
			off := hi - mLo
			kept = append(kept, &mapping{start: hi, length: mHi - hi, flags: m.flags,
				offset: m.offset, data: m.data[off:]})
		}
	}
	a.maps = kept
}

func (a *addrSpace) munmap(addr, length int) (int, error) {
	if addr%page != 0 {
		return -1, &osErr{eInval, "munmap: addr must be a multiple of the page size"}
	}
	// length 不必是页的整数倍;覆盖到的**整页**都会被卸载
	lo, hi := addr, alignUp(addr+length)
	a.discard(lo, hi-lo)
	return 0, nil
}

func (a *addrSpace) find(addr int) *mapping {
	for _, m := range a.maps {
		if addr >= m.start && addr < m.start+m.length {
			return m
		}
	}
	return nil
}

func (a *addrSpace) store(addr, n int, v byte) error {
	m := a.find(alignDown(addr))
	if m == nil {
		return &osErr{eInval, "store: address not mapped"}
	}
	off := addr - m.start
	for i := 0; i < n; i++ {
		m.data[off+i] = v
	}
	return nil
}

func (a *addrSpace) load(addr, n int) ([]byte, error) {
	m := a.find(alignDown(addr))
	if m == nil {
		return nil, &osErr{eInval, "load: address not mapped"}
	}
	off := addr - m.start
	return m.data[off : off+n], nil
}

func (a *addrSpace) brkManaged() int { return a.brk - a.heapMin }

// ---- malloc 的 mmap 阈值策略(mallopt(3)) ----

type thresholdPolicy struct {
	mmapThreshold int
	trimThreshold int
	mmapMax       int
	dynamic       bool
}

func newPolicy() *thresholdPolicy {
	return &thresholdPolicy{mmapThreshold: mmapThresh, trimThreshold: trimThresh,
		mmapMax: mmapMax, dynamic: true}
}

func (p *thresholdPolicy) classify(n int) string {
	if n >= p.mmapThreshold {
		return "mmap"
	}
	return "heap"
}

func (p *thresholdPolicy) onFree(blockSize int) string {
	if !p.dynamic {
		return "frozen"
	}
	if blockSize > p.mmapThreshold && blockSize <= mmapThreshM {
		p.mmapThreshold = blockSize
		p.trimThreshold = 2 * p.mmapThreshold
		return "raise"
	}
	return "keep"
}

func (p *thresholdPolicy) mallopt(param string) {
	switch param {
	case "M_TRIM_THRESHOLD", "M_TOP_PAD", "M_MMAP_THRESHOLD", "M_MMAP_MAX":
		p.dynamic = false
	}
}
