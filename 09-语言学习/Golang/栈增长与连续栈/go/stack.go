// Package main 用 Go 复刻 goroutine 栈的增长与收缩（src/runtime/stack.go + proc.go 的 newstack）。
//
// 官方实现里栈是「连续栈」：不够就整块复制到一块 2 倍大的新内存，
// 并把栈上的指针按偏移量逐个修正（adjustpointers/adjustframe）。这里只保留
// **容量计算与搬动语义**，去掉指针修正与 GC 交互。
package main

import "fmt"

// 官方常量。
const (
	stackMin          = 2048 // runtime/stack.go
	stackNosplitBase  = 800  // internal/abi
	stackSmall        = 128  // internal/abi
	stackBig          = 4096 // internal/abi
	maxStackSize64    = 1000000000
	maxStackSize32    = 250000000
	stackGuardMult    = 1 // StackGuardMultiplier = 1 + IsAix + IsOpenbsd + isRace
)

// StackOverflow 对应 runtime 的 throw("stack overflow")。
type StackOverflow struct {
	NewSize int64
	Max     int64
}

func (e *StackOverflow) Error() string {
	return fmt.Sprintf("stack overflow: %d > %d", e.NewSize, e.Max)
}

// stackSystem 对应 runtime/stack.go 的 stackSystem。
func stackSystem(goos, goarch string) int64 {
	var v int64
	if goos == "windows" {
		v += 4096
	}
	if goos == "plan9" {
		v += 512
	}
	if goos == "ios" && goarch == "arm64" {
		v += 1024
	}
	return v
}

// fixedStack 对应 fixedStack0..fixedStack6 的「向上取整到 2 的幂」位运算。
func fixedStack(goos, goarch string) int64 {
	x := stackMin + stackSystem(goos, goarch)
	var p int64 = 1
	for p < x {
		p *= 2
	}
	return p
}

func stackNosplit() int64 { return stackNosplitBase * stackGuardMult }

// stackGuard 对应 stackGuard = stackNosplit + stackSystem + abi.StackSmall。
func stackGuard(goos, goarch string) int64 {
	return stackNosplit() + stackSystem(goos, goarch) + stackSmall
}

// guardSentinel 复现 g.stackguard0 的三个哨兵值（都存的是很大的无符号数）。
func guardSentinel(v int64, ptrSize int) uint64 {
	mask := uint64(1)<<(8*uint(ptrSize)) - 1
	return mask & uint64(v)
}

// G 对应 runtime.g 的栈相关字段。
type G struct {
	Lo, Hi, Sp int64
	Goos       string
	Goarch     string
	Moves      int
}

// NewG 创建一块 size 大小的栈，spOffset 是已用字节数。
func NewG(size, spOffset int64, goos string) *G {
	g := &G{Lo: 0x1000, Hi: 0x1000 + size, Goos: goos, Goarch: "amd64"}
	g.Sp = g.Hi - spOffset
	return g
}

func (g *G) size() int64 { return g.Hi - g.Lo }
func (g *G) used() int64 { return g.Hi - g.Sp }

// copyStack 对应 stack.go 的 copystack：整块复制，栈内相对偏移保持不变。
func (g *G) copyStack(newSize int64) {
	used := g.used()
	g.Lo = 0x1000 + int64(g.Moves+1)*0x100000
	g.Hi = g.Lo + newSize
	g.Sp = g.Hi - used
	g.Moves++
}

// newStack 对应 proc.go 的 newstack 中「算新栈大小」的部分。
func newStack(g *G, funcMaxSPDelta int64, hasDelta, forceMove bool, ptrSize int) (int64, error) {
	oldSize := g.size()
	newSize := oldSize * 2
	if hasDelta {
		needed := funcMaxSPDelta + stackGuard(g.Goos, g.Goarch)
		used := g.used()
		for newSize-used < needed {
			newSize *= 2
		}
	}
	if forceMove {
		newSize = oldSize // stackForceMove：调试用，故意不翻倍
	}
	maxSize := int64(maxStackSize64)
	if ptrSize == 4 {
		maxSize = maxStackSize32
	}
	if newSize > maxSize || newSize > 2*maxSize {
		return 0, &StackOverflow{newSize, maxSize}
	}
	g.copyStack(newSize)
	return newSize, nil
}

// shrinkStack 对应 stack.go 的 shrinkstack：返回 0 表示不收缩。
func shrinkStack(g *G) int64 {
	oldSize := g.size()
	newSize := oldSize / 2
	if newSize < fixedStack(g.Goos, g.Goarch) {
		return 0 // 不低于最小栈分配
	}
	used := g.used() + stackNosplit()
	if used >= oldSize/4 {
		return 0 // 用了超过四分之一就不收缩
	}
	g.copyStack(newSize)
	return newSize
}

// isShrinkStackSafe 对应 stack.go 的 isShrinkStackSafe 四条否决条件。
func isShrinkStackSafe(syscallSP int64, asyncSafePoint, parkingOnChan, waitingForSuspend bool) bool {
	if syscallSP != 0 {
		return false
	}
	if asyncSafePoint {
		return false
	}
	if parkingOnChan {
		return false
	}
	if waitingForSuspend {
		return false
	}
	return true
}
