// Package main 用 Go 复刻六家标准库的动态数组增长策略，与 python/growth_factor.py 逐函数对齐。
//
// 逐行对应的原文：
//
//   - libstdc++ bits/stl_vector.h  _M_check_len      → size() + max(n, size())
//   - libc++    include/__vector/vector.h __recommend → max(2*cap, new_size)
//   - MSVC STL  stl/inc/vector     _Calculate_growth  → cap + cap/2（1.5×）
//   - Go        runtime/slice.go   nextslicecap       → <256 翻倍，之后 +1/4 平滑
//   - CPython   Objects/listobject.c list_resize      → ((n + n>>3 + 6) &^ 3)
//   - Rust      library/alloc/src/raw_vec/mod.rs      → max(cap*2, required) 再过 min_non_zero_cap
package main

import "math"

const maxSize = 1 << 62
const goThreshold = 256

// libstdcxxCheckLen 复刻 _M_check_len：push_back 单个元素时恰好 2×。
func libstdcxxCheckLen(size, n int) int {
	room := maxSize - size
	if room < n {
		panic("vector::_M_range_insert would exceed max_size")
	}
	if n < size {
		n = size // Grow by (at least) doubling
	}
	if n > room {
		n = room
	}
	return size + n
}

// libcppRecommend 复刻 __recommend：max(2*cap, new_size)。
func libcppRecommend(newSize, cap int) int {
	if newSize > maxSize {
		panic("length_error")
	}
	if cap >= maxSize/2 {
		return maxSize
	}
	if 2*cap > newSize {
		return 2 * cap
	}
	return newSize
}

// msvcCalculateGrowth 复刻 _Calculate_growth：1.5× 几何增长。
func msvcCalculateGrowth(newSize, cap int) int {
	if cap > maxSize-cap/2 {
		return maxSize // geometric growth would overflow
	}
	geometric := cap + cap/2
	if geometric < newSize {
		return newSize // geometric growth would be insufficient
	}
	return geometric
}

// goNextslicecap 复刻 runtime/slice.go 的 nextslicecap。
func goNextslicecap(newLen, oldCap int) int {
	doublecap := oldCap + oldCap
	if newLen > doublecap {
		return newLen
	}
	if oldCap < goThreshold {
		return doublecap
	}
	newCap := oldCap
	for i := 0; i < 64; i++ {
		newCap += (newCap + 3*goThreshold) >> 2
		if newCap >= newLen {
			break
		}
	}
	if newCap <= 0 { // 溢出回退
		return newLen
	}
	return newCap
}

// cpythonListResize 复刻 list_resize，返回新的 allocated。
func cpythonListResize(newsize, allocated, oldSize int) int {
	if allocated >= newsize && newsize >= allocated>>1 {
		return allocated // 含「缩到一半以下才真缩」的旁路
	}
	newAllocated := (newsize + (newsize >> 3) + 6) &^ 3
	if newsize-oldSize > newAllocated-newsize {
		newAllocated = (newsize + 3) &^ 3
	}
	if newsize == 0 {
		newAllocated = 0
	}
	return newAllocated
}

// rustMinNonZeroCap 复刻 min_non_zero_cap：1 字节→8，≤1024 字节→4，否则 1。
func rustMinNonZeroCap(elemSize int) int {
	if elemSize == 1 {
		return 8
	}
	if elemSize <= 1024 {
		return 4
	}
	return 1
}

// rustGrowAmortized 复刻 grow_amortized：先 max(cap*2, required)，再 max(min_non_zero_cap, cap)。
func rustGrowAmortized(cap, length, additional, elemSize int) int {
	required := length + additional
	c := cap * 2
	if required > c {
		c = required
	}
	m := rustMinNonZeroCap(elemSize)
	if m > c {
		return m
	}
	return c
}

// growers 是统一驱动表；签名 (cap, size, n) → 新容量。
var growers = map[string]func(cap, size, n int) int{
	"libstdc++": func(cap, size, n int) int { return libstdcxxCheckLen(size, n) },
	"libc++":    func(cap, size, n int) int { return libcppRecommend(size+n, cap) },
	"MSVC":      func(cap, size, n int) int { return msvcCalculateGrowth(size+n, cap) },
	"Go":        func(cap, size, n int) int { return goNextslicecap(size+n, cap) },
	"CPython":   func(cap, size, n int) int { return cpythonListResize(size+n, cap, size) },
	"Rust<u64>": func(cap, size, n int) int { return rustGrowAmortized(cap, size, n, 8) },
}

// alwaysCall 里的实现在每次 append 时都会被调用（内部自带旁路判据）。
var alwaysCall = map[string]bool{"CPython": true}

// orderedNames 保证打印顺序稳定（Go map 迭代是随机的）。
var orderedNames = []string{"libstdc++", "libc++", "MSVC", "Go", "CPython", "Rust<u64>"}

// appendSequence 模拟从空容器开始连续 append count 次，返回每次操作后的容量。
func appendSequence(name string, count int) []int {
	grow := growers[name]
	always := alwaysCall[name]
	cap, size := 0, 0
	out := make([]int, 0, count)
	for i := 0; i < count; i++ {
		if always || size+1 > cap {
			if newCap := grow(cap, size, 1); newCap != cap {
				cap = newCap
			}
		}
		size++
		out = append(out, cap)
	}
	return out
}

// growthPoint 是一次扩容：(第几次 append, 旧容量, 新容量)。
type growthPoint struct {
	step int
	old  int
	next int
}

// growthPoints 列出容量序列中的扩容点。
func growthPoints(seq []int) []growthPoint {
	pts := make([]growthPoint, 0, 16)
	prev := 0
	for i, cap := range seq {
		if cap != prev {
			pts = append(pts, growthPoint{i + 1, prev, cap})
			prev = cap
		}
	}
	return pts
}

// amortizedMoves 统计 N 次 append 里搬家搬过的元素总数。
func amortizedMoves(seq []int) int {
	total, prev := 0, 0
	for i, cap := range seq {
		if cap != prev {
			if prev < i {
				total += prev
			} else {
				total += i // 搬的是扩容前已有的元素
			}
			prev = cap
		}
	}
	return total
}

// peakMemoryRatio 扩容瞬间新旧两块同时在世 ⇒ 峰值 = (旧 + 新) / 新。
func peakMemoryRatio(seq []int) float64 {
	worst, prev := 1.0, 0
	for _, cap := range seq {
		if cap != prev && prev > 0 {
			if r := float64(prev+cap) / float64(cap); r > worst {
				worst = r
			}
		}
		prev = cap
	}
	return worst
}

// reuseGeneration 实数模型：第几次扩容起「此前释放的块之和」能盖住新块。
// 判据为 r^i·(2-r) >= 1；这里用逐项推演，避免 float 在大 i 处吞掉差值。
func reuseGeneration(factor float64, maxSteps int) int {
	freed, cap := 0.0, 1.0
	for k := 1; k <= maxSteps; k++ {
		avail := freed + cap
		next := cap * factor
		if avail >= next {
			return k
		}
		freed, cap = avail, next
	}
	return -1 // 永不复用
}

// reuseGenerationCeil 取整模型：ceil 抬高新块，只会让复用更晚。
func reuseGenerationCeil(factor float64, maxSteps int) int {
	freed, cap := 0.0, 1.0
	for k := 1; k <= maxSteps; k++ {
		avail := freed + cap
		next := math.Ceil(factor * cap)
		if avail >= next {
			return k
		}
		freed, cap = avail, next
	}
	return -1
}

// goldenRatio 即 r^2(2-r) = 1 的正根，也是「第 2 次扩容就能复用」的上界。
func goldenRatio() float64 { return (1 + math.Sqrt(5)) / 2 }
