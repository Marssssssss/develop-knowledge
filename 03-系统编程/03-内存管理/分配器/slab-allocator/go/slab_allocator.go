// slab_allocator.go — 最小 slab 分配器(Go 版)
//
// 模型对应 Bonwick 1994 + Linux Kernel Ch.8 简化版:
//   - KmemCache 管理一种固定尺寸的对象
//   - 三条 slab 链表:partial(有空闲) / full(无空闲) / free(未分配过)
//   - 每个 slab:连续字节切片 + uint32 bitmap + next 指针
//
// 注意:
//   - 这是教学简化版。真实 Linux SLAB 还有 slab coloring / 每 CPU 数组 / reap
//     回收等高级特性。
//   - Go 版用 unsafe.Pointer + uintptr 反算对象所属 slab 与 idx,避免遍历链表;
//     生产代码可考虑在 slab 内放 prologue 元数据,把"对象 → slab"做成 O(1)。
package main

import (
	"fmt"
	"reflect"
	"unsafe"
)

const (
	SLAB_BYTES   = 4096           // 单 slab = 1 页
	OBJ_SIZE     = 32             // 单对象大小(向上对齐到 8 字节)
	SLAB_OBJ_MAX = (SLAB_BYTES - 64) / OBJ_SIZE
)

// slab 是单页对象块;bitmap 用 32 位整数足够(SLAB_OBJ_MAX < 128)。
type slab struct {
	mem       []byte          // SLAB_BYTES 字节
	bitmap    uint32          // 位图(每对象 1 位)
	objCount  int             // 本 slab 容纳对象数
	used      int             // 已分配个数
	next      *slab
}

// KmemCache 类似 Linux 的 struct kmem_cache 管理一种固定尺寸对象。
type KmemCache struct {
	objSize      int
	slabsPartial *slab
	slabsFull    *slab
	slabsFree    *slab
	allocTotal   int
	freeTotal    int
}

// -------- 链表辅助 --------

func (c *KmemCache) popHead(attr **slab) *slab {
	h := *attr
	if h == nil {
		return nil
	}
	*attr = h.next
	return h
}

func (c *KmemCache) pushHead(attr **slab, s *slab) {
	s.next = *attr
	*attr = s
}

// -------- 创建 / 销毁 --------

func newSlab(objSize int) *slab {
	return &slab{
		mem:      make([]byte, SLAB_BYTES),
		bitmap:   0,
		objCount: SLAB_OBJ_MAX,
		used:     0,
	}
}

func NewKmemCache(objSize int) *KmemCache {
	c := &KmemCache{objSize: objSize}
	// 预创建第一个 free slab
	c.slabsFree = newSlab(objSize)
	return c
}

func (c *KmemCache) Destroy() {
	for _, head := range []*slab{c.slabsPartial, c.slabsFull, c.slabsFree} {
		for p := head; p != nil; {
			n := p.next
			p = n
		}
	}
}

// -------- 选 slab:partial → free → 创建新 slab --------

func (c *KmemCache) selectSlab() *slab {
	if c.slabsPartial != nil {
		return c.slabsPartial
	}
	if c.slabsFree != nil {
		s := c.popHead(&c.slabsFree)
		c.pushHead(&c.slabsPartial, s)
		return s
	}
	// 全 full:新建 slab 进入 partial
	s := newSlab(c.objSize)
	c.pushHead(&c.slabsPartial, s)
	return s
}

// -------- 分配 --------

// Alloc 分配一个 slot;返回可写入的 []byte(指向 slab 内部)。失败返回 nil。
func (c *KmemCache) Alloc() []byte {
	s := c.selectSlab()
	if s == nil {
		return nil
	}
	// 用位图找一个 0 位;SLAB_OBJ_MAX <= 126,32 位足够。
	mask := uint32((1 << s.objCount) - 1)
	freeMask := ^s.bitmap & mask
	if freeMask == 0 {
		return nil
	}
	idx := ctz32(freeMask)        // count trailing zeros → 第一个空闲 idx
	s.bitmap |= (1 << idx)
	s.used++
	c.allocTotal++

	// slab 满 → partial → full
	if s.used == s.objCount {
		c.popHead(&c.slabsPartial)   // 直接摘头(本 demo 假定 s 在头)
		c.pushHead(&c.slabsFull, s)
	}
	start := idx * c.objSize
	return s.mem[start : start+c.objSize]
}

// ctz32:返回 v 末尾 0 的个数(v 必非 0)。
func ctz32(v uint32) int {
	n := 0
	for v&1 == 0 {
		n++
		v >>= 1
	}
	return n
}

// -------- 释放 --------

// Free 释放一个对象;返回是否成功。
func (c *KmemCache) Free(obj []byte) bool {
	if len(obj) == 0 {
		return false
	}
	objAddr := uintptr(unsafe.Pointer(&obj[0]))

	// 在 partial / full 两个链表里查找 obj 所属 slab
	for _, attr := range []*slab{c.slabsPartial, c.slabsFull} {
		var prev *slab
		cur := attr
		for cur != nil {
			baseAddr := uintptr(unsafe.Pointer(&cur.mem[0]))
			endAddr := baseAddr + uintptr(cur.objCount*c.objSize)
			if baseAddr <= objAddr && objAddr < endAddr {
				off := objAddr - baseAddr
				idx := int(off / uintptr(c.objSize))
				cur.bitmap &^= (1 << idx)
				cur.used--
				c.freeTotal++
				_ = prev
				// full → partial 升级
				if attr == c.slabsFull {
					if prev != nil {
						prev.next = cur.next
					} else {
						// cur 在链表头
						c.slabsFull = cur.next
					}
					c.pushHead(&c.slabsPartial, cur)
				}
				return true
			}
			prev = cur
			cur = cur.next
		}
		_ = prev
	}
	return false
}

// -------- 统计 --------

func (c *KmemCache) counts() (int, int, int) {
	lengthOf := func(h *slab) int { n := 0; for p := h; p != nil; p = p.next { n++ }; return n }
	return lengthOf(c.slabsPartial), lengthOf(c.slabsFull), lengthOf(c.slabsFree)
}

func (c *KmemCache) stats() string {
	p, f, fr := c.counts()
	return fmt.Sprintf("partial=%d full=%d free=%d (alloc_total=%d free_total=%d)",
		p, f, fr, c.allocTotal, c.freeTotal)
}

// -------- demo --------

type Toy struct {
	A   int32
	B   byte
	Pad [27]byte // 让 sizeof(Toy) == 32
}

func demoBasic(c *KmemCache) {
	fmt.Println("[1] basic alloc/free — same-size objects reuse a single slab")
	fmt.Printf("    obj_size = %dB, slab = %dB (1 page)\n", OBJ_SIZE, SLAB_BYTES)
	objs := make([][]byte, 4)
	for i := 0; i < 4; i++ {
		objs[i] = c.Alloc()
		*(*int32)(unsafe.Pointer(&objs[i][0])) = int32(100 + i)
		fmt.Printf("    alloc[%d] -> id=%d\n", i, *(*int32)(unsafe.Pointer(&objs[i][0])))
	}
	fmt.Println("    " + c.stats())
	for _, v := range objs {
		c.Free(v)
	}
	fmt.Println("    freed all 4\n    " + c.stats())
}

func demoGrow(c *KmemCache) {
	fmt.Println("\n[2] slab chain growth — alloc past SLAB_OBJ_MAX triggers a new slab")
	fmt.Printf("    SLAB_OBJ_MAX = %d objs per slab\n", SLAB_OBJ_MAX)

	N := SLAB_OBJ_MAX + 5
	batch := make([][]byte, N)
	for i := 0; i < N; i++ {
		batch[i] = c.Alloc()
	}
	fmt.Printf("    allocated %d objects\n    %s\n", N, c.stats())

	for i := 0; i < N; i += 3 {
		c.Free(batch[i])
		batch[i] = nil
	}
	fmt.Println("    after freeing every 3rd object:\n    " + c.stats())

	for _, v := range batch {
		if v != nil {
			c.Free(v)
		}
	}
	fmt.Println("    freed all, slabs stay in chains (no reap in this demo)\n    " + c.stats())
}

func demoObjectsAreIsolated(c *KmemCache) {
	fmt.Println("\n[3] per-slab continuity — adjacent objs in one slab land size apart")
	objs := make([][]byte, 5)
	for i := 0; i < 5; i++ {
		objs[i] = c.Alloc()
		*(*int32)(unsafe.Pointer(&objs[i][0])) = int32(200 + i)
	}
	addrs := make([]uintptr, 5)
	for i, v := range objs {
		addrs[i] = uintptr(unsafe.Pointer(&v[0]))
	}
	for _, i := range []int{1, 3} {
		fmt.Printf("    objs[0..%d] distance = %d bytes (expected %d)\n",
			i, addrs[i]-addrs[0], OBJ_SIZE*i)
	}
	for _, v := range objs {
		c.Free(v)
	}
}

func main() {
	fmt.Println("=== slab allocator demo (simplified, Go) ===")
	c := NewKmemCache(OBJ_SIZE)

	demoBasic(c)
	demoGrow(c)
	demoObjectsAreIsolated(c)

	c.Destroy()
	fmt.Println("\n[ok] cache destroyed (all slabs GCed).")

	// 防 reflect 包被 goimports 误删
	_ = reflect.TypeOf
}
