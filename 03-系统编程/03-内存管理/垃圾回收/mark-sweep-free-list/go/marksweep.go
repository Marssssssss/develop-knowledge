package main

// ---- mark-sweep ----

// MSHeap 在空闲链表堆上叠加 mark-sweep。
type MSHeap struct {
	*Heap
	Refs         map[int][]int
	Roots        []int
	SweepScanned int
	MarkVisited  int
}

// NewMSHeap 构造带引用图的堆。
func NewMSHeap(n int, policy string) *MSHeap {
	return &MSHeap{Heap: NewHeap(n, policy, false, false), Refs: map[int][]int{}}
}

// New 分配一个对象并记录其引用。
func (h *MSHeap) New(nwords int, refs []int) int {
	p := h.Malloc(nwords)
	if p < 0 {
		return -1
	}
	if refs == nil {
		refs = []int{}
	}
	h.Refs[p] = refs
	return p
}

// Mark 从 Roots 出发标记可达对象。
func (h *MSHeap) Mark() map[int]bool {
	seen := map[int]bool{}
	stack := append([]int{}, h.Roots...)
	for len(stack) > 0 {
		x := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if seen[x] {
			continue
		}
		seen[x] = true
		h.MarkVisited++
		stack = append(stack, h.Refs[x]...)
	}
	return seen
}

// Sweep 回收未标记对象并合并空闲块。
func (h *MSHeap) Sweep() int {
	live := h.Mark()
	collected := 0
	for _, b := range h.Blocks {
		h.SweepScanned++ // 清扫必须扫过整个堆
		if !b.Free && !live[b.Payload()] {
			b.Free = true
			b.Seq = h.nextSeq()
			delete(h.Refs, b.Payload())
			collected++
		}
	}
	h.Coalesce()
	return collected
}

// GCAlloc 分配失败 -> 触发 GC -> 重试。
func (h *MSHeap) GCAlloc(nwords int, refs []int) int {
	if p := h.New(nwords, refs); p >= 0 {
		return p
	}
	h.Sweep()
	return h.New(nwords, refs)
}

