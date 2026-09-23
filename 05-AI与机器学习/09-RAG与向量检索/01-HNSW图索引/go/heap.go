package main

// 按 hnswlib 官方 hnswalg.h 转写的 HNSW 原子构件：两个方向的堆、距离、层级生成。
//
// 与 C++ 版本的差异显式落地：
//   - C++ 用 std::priority_queue + CompareByFirst（a.first < b.first ⇒ 距离大顶堆），
//     Go 用 container/heap 复刻同语义的 max/min 堆；
//   - C++ 的 (int) 截断与 Go 的 int() 同为向零取整，因 -log(U)*mult 非负故一致；
//   - C++ 同距离时无稳定顺序，Go 版本统一用 Node 作次键保证可复现。

import (
	"container/heap"
	"errors"
	"math"
	"sort"
)

// Item 是堆里的一个 (距离, 结点) 对。
type Item struct {
	Dist float64
	Node int
}

// MaxHeapOnDist 对应 top_candidates：按距离的大顶堆，top 是最远者。
type MaxHeapOnDist []Item

func (h MaxHeapOnDist) Len() int { return len(h) }
func (h MaxHeapOnDist) Less(i, j int) bool {
	if h[i].Dist != h[j].Dist {
		return h[i].Dist > h[j].Dist // 大顶
	}
	return h[i].Node < h[j].Node
}
func (h MaxHeapOnDist) Swap(i, j int)       { h[i], h[j] = h[j], h[i] }
func (h *MaxHeapOnDist) Push(x interface{}) { *h = append(*h, x.(Item)) }
func (h *MaxHeapOnDist) Pop() interface{} {
	old := *h
	n := len(old)
	x := old[n-1]
	*h = old[:n-1]
	return x
}

// PushItem 入堆一个 (距离, 结点)。
func (h *MaxHeapOnDist) PushItem(d float64, n int) { heap.Push(h, Item{Dist: d, Node: n}) }

// Top 返回堆顶（最远）。
func (h *MaxHeapOnDist) Top() Item { return (*h)[0] }

// PopItem 弹出堆顶（最远）。
func (h *MaxHeapOnDist) PopItem() Item { return heap.Pop(h).(Item) }

// Items 按距离升序返回全部元素。
func (h MaxHeapOnDist) Items() []Item {
	out := make([]Item, len(h))
	copy(out, h)
	sort.Slice(out, func(i, j int) bool {
		if out[i].Dist != out[j].Dist {
			return out[i].Dist < out[j].Dist
		}
		return out[i].Node < out[j].Node
	})
	return out
}

// MinHeapOnDist 对应 candidateSet（官方入堆存负距离 ⇒ 真实距离的小顶堆）。
type MinHeapOnDist []Item

func (h MinHeapOnDist) Len() int { return len(h) }
func (h MinHeapOnDist) Less(i, j int) bool {
	if h[i].Dist != h[j].Dist {
		return h[i].Dist < h[j].Dist
	}
	return h[i].Node < h[j].Node
}
func (h MinHeapOnDist) Swap(i, j int)       { h[i], h[j] = h[j], h[i] }
func (h *MinHeapOnDist) Push(x interface{}) { *h = append(*h, x.(Item)) }
func (h *MinHeapOnDist) Pop() interface{} {
	old := *h
	n := len(old)
	x := old[n-1]
	*h = old[:n-1]
	return x
}

// PushItem 入堆一个 (距离, 结点)。
func (h *MinHeapOnDist) PushItem(d float64, n int) { heap.Push(h, Item{Dist: d, Node: n}) }

// Top 返回堆顶（最近）。
func (h *MinHeapOnDist) Top() Item { return (*h)[0] }

// PopItem 弹出堆顶（最近）。
func (h *MinHeapOnDist) PopItem() Item { return heap.Pop(h).(Item) }

// L2Sqr 对应 space_l2.h 的标量版：返回平方距离，不开根。
func L2Sqr(a, b []float64) float64 {
	var s float64
	for i := range a {
		t := a[i] - b[i]
		s += t * t
	}
	return s
}

// GetRandomLevel 对应 getRandomLevel：(int)(-log(U) * reverse_size)。
func GetRandomLevel(u, mult float64) (int, error) {
	if !(u > 0.0 && u <= 1.0) {
		return 0, errors.New("u must be in (0, 1]")
	}
	return int(-math.Log(u) * mult), nil
}

// BruteKnn 暴力基线，用于验证 ef 足够大时图索引结果是精确的。
func BruteKnn(q []float64, vectors [][]float64, k int) []Item {
	out := make([]Item, len(vectors))
	for i, v := range vectors {
		out[i] = Item{Dist: L2Sqr(q, v), Node: i}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Dist < out[j].Dist })
	if len(out) > k {
		out = out[:k]
	}
	return out
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
