// bump_allocator.go — 最小 bump (arena) 分配器(单块 Go 版)
//
// 工作机制与 C 版完全对应:
//   - 预分配一大块字节切片(make([]byte, capacity))
//   - 维护 a.offset(相对 &a.buf[0] 的字节数)
//   - 每次分配把 offset 对齐到 alignment,返回 &buf[aligned] 的切片;
//     offset += 填充 + size
//   - 释放只能整块重置(Reset),不能 free 单个对象
//
// 与 C 版的差异:
//   - 用 Go 内置 make/切片代替 mmap,无须手动管理底层页
//   - 用 unsafe.Offsetof / unsafe.Alignof 取类型对齐(等价 C 的 _Alignof(T))
//   - 用 reflect.Type 与 unsafe 算类型 size(代替 sizeof(T))
//
// 注意事项:
//   - alignment 必须为 2 的幂,与 C 版约束一致
//   - 返回的 []byte 指向 a.buf 内部;a.Reset() 之后该切片仍指向同样地址,
//     但语义上属于复用;调用方写入前必须重新拿切片
package main

import (
	"errors"
	"fmt"
	"reflect"
	"runtime"
	"unsafe"
)

// ---------- Arena 数据结构 ----------

type Arena struct {
	buf      []byte   // 底层 buffer
	offset   int      // 下一个分配起点(offset,相对 &buf[0])
	capacity int      // 总容量
}

// NewArena 创建一段容量为 capacity(必须 2 的幂)的 bump arena。
func NewArena(capacity int) (*Arena, error) {
	if capacity <= 0 || capacity&(capacity-1) != 0 {
		return nil, fmt.Errorf("capacity must be a positive power of 2, got %d", capacity)
	}
	buf := make([]byte, capacity)
	return &Arena{buf: buf, offset: 0, capacity: capacity}, nil
}

// Reset 把 offset 归零;老的切片在逻辑上视为失效。
func (a *Arena) Reset() { a.offset = 0 }

// alignUp 把 x 向上取整到 alignment 的倍数(alignment 必为 2 的幂)。
func alignUp(x, alignment uintptr) uintptr {
	return (x + alignment - 1) &^ (alignment - 1)
}

// Alloc 分配 size 字节,按 alignment 向上对齐;返回一段写入即写入底层 buffer 的切片。
//
// 与 C 版 strict 返回 void* 不同,这里返回 []byte 更符合 Go 习惯。
// 分配失败(对齐非法或 OOM)返回 error,nil。
func (a *Arena) Alloc(size, alignment int) ([]byte, error) {
	if alignment <= 0 || alignment&(alignment-1) != 0 {
		return nil, fmt.Errorf("alignment must be a positive power of 2, got %d", alignment)
	}

	// 1) 当前 offset 对应的"绝对地址"(用 uintptr 模拟 C 的指针 → 整数)
	current := uintptr(unsafe.Pointer(&a.buf[a.offset]))
	aligned := alignUp(current, uintptr(alignment))
	pad := int(aligned - current)

	// 2) 检查剩余容量
	if a.offset+pad+size > a.capacity {
		return nil, fmt.Errorf("arena OOM: need %d bytes, only %d bytes free",
			pad+size, a.capacity-a.offset)
	}

	// 3) 推进 offset 并返回切片(指向 a.buf 内部)
	a.offset += pad + size
	return a.buf[aligned : aligned+uintptr(size)], nil
}

// AlignedSize 返回类型 T 的对齐(用 unsafe.Alignof 取代 C 的 _Alignof(T))。
// 这是 Go 1.20+ 提供的 stdlib 工具,1.21 之前可用 reflect.Type.Align()。
func AlignedSize[T any]() (size, align int) {
	var zero T
	t := reflect.TypeOf(zero)
	return int(t.Size()), int(t.Align())
}

// ------------------------- demo -------------------------

type Data struct {
	Name string // 16-byte string header
	ID   uint32
}

func demoBasic(a *Arena) {
	fmt.Printf("[1] basic allocations (default align = %d bytes)\n", unsafe.Alignof(int(0)))

	// int
	intSize, intAlign := AlignedSize[int]()
	view, err := a.Alloc(intSize, intAlign)
	if err != nil {
		fmt.Println("    alloc err:", err)
		return
	}
	*(*int)(unsafe.Pointer(&view[0])) = 42
	fmt.Printf("    int*   -> %d-byte aligned, value=%d\n", intAlign, *(*int)(unsafe.Pointer(&view[0])))

	// float64(double)
	dSize, dAlign := AlignedSize[float64]()
	dv, _ := a.Alloc(dSize, dAlign)
	*(*float64)(unsafe.Pointer(&dv[0])) = 3.14
	fmt.Printf("    double* -> value=%.2f\n", *(*float64)(unsafe.Pointer(&dv[0])))

	// struct Data
	sSize, sAlign := AlignedSize[Data]()
	sv, _ := a.Alloc(sSize, sAlign)
	d := (*Data)(unsafe.Pointer(&sv[0]))
	d.Name = "hello"
	d.ID = 7
	fmt.Printf("    struct Data -> {Name=%q, ID=%d}\n", d.Name, d.ID)
	fmt.Printf("    arena offset after 3 allocs = %d bytes\n", a.offset)
}

func demoAlignment(a *Arena) {
	fmt.Println("\n[2] explicit alignment (32-byte)")

	// 先 alloc 1 字节 char,把 offset 推到非对齐位置
	a.Alloc(1, 1)
	before := a.offset

	v, _ := a.Alloc(4, 32)
	after := a.offset
	*(*int32)(unsafe.Pointer(&v[0])) = 7
	fmt.Printf("    char* offset before = %d\n", before-1)
	fmt.Printf("    int*  32-byte aligned; padding = %d bytes (offset %d -> %d)\n",
		after-before-4, before, after)
	fmt.Printf("    int value = %d\n", *(*int32)(unsafe.Pointer(&v[0])))
}

func demoResetReuse(a *Arena) {
	fmt.Println("\n[3] reset & reuse — bump allocator has no per-object free")

	before := a.offset
	a.Reset()
	fmt.Printf("    reset(): offset %d -> 0\n", before)

	p1, _ := a.Alloc(4, 4)
	*(*int32)(unsafe.Pointer(&p1[0])) = 100
	p2, _ := a.Alloc(4, 4)
	*(*int32)(unsafe.Pointer(&p2[0])) = 200
	fmt.Printf("    reuse: p1=%d, p2=%d  (offset=%d)\n",
		*(*int32)(unsafe.Pointer(&p1[0])), *(*int32)(unsafe.Pointer(&p2[0])), a.offset)
	fmt.Println("    note: previous int=42, double=3.14, etc. are now logically freed")
}

func demoOOBCheck(a *Arena) {
	fmt.Println("\n[4] OOM check — request too large for remaining capacity")
	remaining := a.capacity - a.offset
	huge := a.capacity
	fmt.Printf("    remaining = %d bytes, capacity = %d bytes\n", remaining, a.capacity)
	if _, err := a.Alloc(huge, 1); err != nil {
		fmt.Printf("    arena.Alloc(%d) returned error as expected ✓ (%v)\n", huge, err)
	} else {
		fmt.Println("    unexpected: alloc succeeded??")
	}
}

func main() {
	fmt.Println("=== bump (arena) allocator demo (Go) ===")
	const CAP = 64 * 1024
	fmt.Printf("capacity = %d KiB\n", CAP/1024)

	a, err := NewArena(CAP)
	if err != nil {
		fmt.Println("NewArena:", err)
		return
	}

	demoBasic(a)
	demoAlignment(a)
	demoResetReuse(a)
	demoOOBCheck(a)

	// 显式把 a 标 nil 让 GC 回收;runtime.GC 只是为了观察;实际演示非必需
	a = nil
	runtime.GC()
	fmt.Println("\n[ok] arena discarded (GC will free backing slice).")

	// 防止 errors 被 import-errcheck 之类工具误报
	_ = errors.New
}
