// slice_mechanism.go — 单文件演示 slice 内部结构、append/growslice 与共享数组陷阱
// Demo 2: slice 底层机制(三字头 ptr/len/cap, append 增长, 共享底层数组)
package main

import (
	"fmt"
	"reflect"
	"unsafe"
)

// sliceHeader 模拟 runtime.sliceHeader 以展示三字头(实际反射 unexported)
type sliceHeader struct {
	ptr unsafe.Pointer
	len int
	cap int
}

// shim 把任意 slice 转成可读的 header
func shim[T any](s []T) sliceHeader {
	sh := (*sliceHeader)(unsafe.Pointer(&s))
	return *sh
}

func main() {
	// ---------- 1) 三字头 ptr/len/cap 的内存布局 ----------
	fmt.Println("[1] 三字头: ptr + len + cap(24 字节)")
	s := []int{10, 20, 30, 40, 50}
	h := shim(s)
	fmt.Printf("  ptr=%p len=%d cap=%d (sizeOf sliceHeader=%d 字节)\n",
		h.ptr, h.len, h.cap, unsafe.Sizeof(h))

	// ---------- 2) make 等价性: make([]T, len, cap) ≡ new([cap]T)[0:len] ----------
	fmt.Println("\n[2] make vs new+slice 等价性")
	a := make([]int, 4, 8)
	b := new([8]int)[0:4]
	fmt.Printf("  a (make):  ptr=%p len=%d cap=%d\n", &a[0], len(a), cap(a))
	fmt.Printf("  b (new):   ptr=%p len=%d cap=%d\n", &b[0], len(b), cap(b))
	// 都是连续的 [cap]int 数组, len=4 cap=8

	// ---------- 3) append 不分配 vs 触发 growslice ----------
	fmt.Println("\n[3] append: cap 够时不分配, 不够时 growslice(1.18+ 1.25x + 192)")
	g := make([]int, 0, 3)
	for i := 0; i < 12; i++ {
		oldCap := cap(g)
		g = append(g, i*10)
		newCap := cap(g)
		ptrChanged := false
		if i > 0 && newCap != oldCap {
			ptrChanged = true
		}
		fmt.Printf("  i=%2d  len=%2d cap=%2d  ptr=%p  cap 增长:%v\n",
			i, len(g), cap(g), &g[0], ptrChanged)
	}
	// 观察: cap 序列 3→6→12→24; 从 24 起按 ~1.25x 增长(24→30→38→48...)

	// ---------- 4) 共享底层数组陷阱 ----------
	fmt.Println("\n[4] 共享底层数组: 修改 sub 会透过原 slice")
	orig := []int{1, 2, 3, 4, 5}
	sub := orig[1:4] // ptr 同 orig, len=3 cap=4 (5-1)
	fmt.Printf("  改前: orig=%v sub=%v\n", orig, sub)
	sub[0] = 999
	fmt.Printf("  sub[0]=999 后: orig=%v (orig[1] 也变了)\n", orig)
	sub = append(sub, 600) // cap 足够(4), 不分配
	fmt.Printf("  append 不分配: orig=%v (orig[4] 也变了)\n", orig)

	// ---------- 5) 三索引切片防止覆盖 ----------
	fmt.Println("\n[5] 三索引切片: big[lo:hi:max] 切断 max 之后的共享")
	big := []int{10, 20, 30, 40, 50, 60, 70, 80, 90}
	view := big[2:5:5] // ptr=&big[2], len=3, cap=3
	fmt.Printf("  改前: view=%v, big[5..]=%v\n", view, big[5:])
	view = append(view, 999) // cap 不足(len==cap), 触发 growslice, 分配新数组
	fmt.Printf("  append 后: view=%v, big=%v (big 不变)\n", view, big)

	// ---------- 6) copy: 内置拷贝 n 个元素 ----------
	fmt.Println("\n[6] copy: 复制 min(len(dst), len(src)) 个元素")
	dst := make([]int, 4)
	src := []int{1, 2, 3, 5, 6, 7}
	n := copy(dst, src) // n=4 (受 len(dst) 限制)
	fmt.Printf("  copied %d elements: dst=%v (src[3]=%d 不进 dst)\n", n, dst, src[3])

	// ---------- 7) nil slice 与空 slice 区别 ----------
	fmt.Println("\n[7] nil slice vs []T{}: 长度都为 0, 但 nil != []T{}")
	var nilSlice []int         // nil slice, ptr=nil
	emptySlice := []int{}      // 空 slice, ptr 指向 length=0 的数组
	fmt.Printf("  nilSlice  == nil? %v  len=%d cap=%d\n", nilSlice == nil, len(nilSlice), cap(nilSlice))
	fmt.Printf("  emptySlice== nil? %v  len=%d cap=%d\n", emptySlice == nil, len(emptySlice), cap(emptySlice))
	// nil 和 empty 都可以 append, 都会得到非 nil 切片

	// ---------- 8) string-byte 互转都用 copy: 共享只读不可写 ----------
	fmt.Println("\n[8] string 与 []byte 互转: 零拷贝用 unsafe, 一般用 []byte(...) 强制拷贝")
	s8 := "Hello, 世界"
	b8 := []byte(s8) // 强制分配并拷贝
	fmt.Printf("  string=%q len=%d\n", s8, len(s8))
	fmt.Printf("  []byte(s)=%v len=%d (UTF-8 字节数)\n", b8, len(b8))

	// ---------- 9) reflect.SliceHeader / unsafe 构造 ----------
	fmt.Println("\n[9] reflect.SliceHeader 与 unsafe.Pointer 构造")
	arr := [6]int{1, 2, 3, 4, 5, 6}
	// 用 reflect.SliceHeader 从数组"切"出 slice, 零拷贝
	sh := reflect.SliceHeader{
		Data: uintptr(unsafe.Pointer(&arr[2])),
		Len:  3,
		Cap:  4, // cap = 6-2
	}
	s9 := *(*[]int)(unsafe.Pointer(&sh))
	fmt.Printf("  从数组 arr[2..] 切: s9=%v, 修改 s9[0] 后 arr=%v\n", s9, arr)
	s9[0] = 999
	fmt.Printf("  s9[0]=999 后 arr=%v (零拷贝, 共用底层)\n", arr)
}