// gc_tri_color.go — 最小三色标记垃圾收集器(stop-the-world Go 版)
//
// 算法对应 Dijkstra, Lamport, Martin, Scholten, Steffens(1978)
// "On-the-Fly Garbage Collection: An Exercise in Cooperation"
//
// 模型:
//   - 堆 = 一组对象(Object)+ 它们之间的"引用"边(简化:用 ID 表示节点)
//   - 三色标记:每个对象初始白色,根可达 → 灰色,所有子节点已访问 → 黑色
//   - 不变式(no black → white):任何黑色对象的出度不能指向白色对象
//   - 终止条件:没有灰色对象 → 所有白色对象都是不可达,可回收
//
// 本 demo 是教学简化版(stop-the-world),无写屏障;并发版需要 Dijkstra / Yuasa / SATB
// 之一,见 README。
package main

import "fmt"

type Color int

const (
	WHITE Color = 0
	GRAY  Color = 1
	BLACK Color = 2
)

// Object 单个堆对象:id + 子引用 + mark state
type Object struct {
	ID         int
	Kids       []int
	MarkState  Color
}

// Heap 简化版"堆":全局对象表 + roots + 灰色工作栈
type Heap struct {
	objects map[int]*Object
	roots   []int
	gray    []int
}

func NewHeap() *Heap {
	return &Heap{objects: map[int]*Object{}}
}

func (h *Heap) alloc(id int) *Object {
	o := &Object{ID: id, MarkState: WHITE}
	h.objects[id] = o
	return o
}

func (h *Heap) addKid(parentID, kidID int) {
	h.objects[parentID].Kids = append(h.objects[parentID].Kids, kidID)
}

func (h *Heap) addRoot(id int) { h.roots = append(h.roots, id) }

// --- mark ---

func (h *Heap) mark() {
	// 1) 根:白 → 灰,入栈
	for _, rid := range h.roots {
		o := h.objects[rid]
		if o != nil && o.MarkState == WHITE {
			o.MarkState = GRAY
			h.gray = append(h.gray, rid)
		}
	}

	// 2) 主循环:pop 灰 → 子白转灰 → 自己转黑
	for len(h.gray) > 0 {
		// pop
		n := len(h.gray) - 1
		curID := h.gray[n]
		h.gray = h.gray[:n]

		cur := h.objects[curID]
		for _, kidID := range cur.Kids {
			kid := h.objects[kidID]
			if kid != nil && kid.MarkState == WHITE {
				kid.MarkState = GRAY
				h.gray = append(h.gray, kidID)
			}
		}
		cur.MarkState = BLACK
	}
}

// sweep 回收白色;活对象重置 WHITE 为下次 GC 做准备
func (h *Heap) sweep() []int {
	var reclaimed []int
	for _, o := range h.objects {
		if o.MarkState == WHITE {
			reclaimed = append(reclaimed, o.ID)
		} else {
			o.MarkState = WHITE
		}
	}
	return reclaimed
}

func (h *Heap) counts() (w, g, b int) {
	for _, o := range h.objects {
		switch o.MarkState {
		case WHITE:
			w++
		case GRAY:
			g++
		case BLACK:
			b++
		}
	}
	return
}

func printState(h *Heap, tag string) {
	w, g, b := h.counts()
	fmt.Printf("    [%-15s] white=%d gray=%d black=%d\n", tag, w, g, b)
}

// --------------------- demo ---------------------

func demoSimple() {
	fmt.Println("[1] simple graph: roots -> A -> B, X -> Y (X,Y unreachable)")
	h := NewHeap()
	h.alloc(1); h.addKid(1, 2)
	h.alloc(2)
	h.alloc(10); h.addKid(10, 11)
	h.alloc(11)
	h.addRoot(1)

	printState(h, "initial")
	h.mark()
	printState(h, "after mark")
	rec := h.sweep()
	fmt.Printf("    reclaimed=%v (expected [10 11])\n", rec)
}

func demoCyclic() {
	fmt.Println("\n[2] cycle that refcount CANNOT collect: A <-> B")
	fmt.Println("    A is a root, A→B and B→A form a cycle")
	h := NewHeap()
	h.alloc(1); h.addKid(1, 2)
	h.alloc(2); h.addKid(2, 1)
	h.addRoot(1)

	h.mark()
	printState(h, "after mark")
	rec := h.sweep()
	fmt.Printf("    reclaimed=%v (expected []; both reachable via cycle)\n", rec)
}

func demoDisconnected() {
	fmt.Println("\n[3] disconnected sub-graph")
	fmt.Println("    roots -> A; D -> E (D,E unreachable)")
	h := NewHeap()
	h.alloc(1)
	h.alloc(4); h.addKid(4, 5)
	h.alloc(5)
	h.addRoot(1)

	h.mark()
	printState(h, "after mark")
	rec := h.sweep()
	fmt.Printf("    reclaimed=%v (expected [4 5])\n", rec)
}

func demoDiamond() {
	fmt.Println("\n[4] diamond — shared kid (must only mark D once)")
	fmt.Println("    A → B, A → C, B → D, C → D")
	h := NewHeap()
	h.alloc(1); h.addKid(1, 2); h.addKid(1, 3)
	h.alloc(2); h.addKid(2, 4)
	h.alloc(3); h.addKid(3, 4)
	h.alloc(4)
	h.addRoot(1)

	h.mark()
	printState(h, "after mark")
	rec := h.sweep()
	fmt.Printf("    reclaimed=%v (expected []; gray-stack 不会重复入 D)\n", rec)
}

func demoOrphanSubtree() {
	fmt.Println("\n[5] orphan subtree via B → X")
	h := NewHeap()
	h.alloc(1); h.addKid(1, 2)
	h.alloc(2); h.addKid(2, 20)
	h.alloc(20)
	h.addRoot(1)

	h.mark()
	printState(h, "after mark")
	rec := h.sweep()
	fmt.Printf("    reclaimed=%v (expected [])\n", rec)
}

func main() {
	fmt.Println("=== tri-color mark-and-sweep GC demo (stop-the-world, Go) ===")

	demoSimple()
	demoCyclic()
	demoDisconnected()
	demoDiamond()
	demoOrphanSubtree()

	fmt.Println("\n[ok] all cycles shown. Tri-color reclaims cycles that refcount cannot.")
}
