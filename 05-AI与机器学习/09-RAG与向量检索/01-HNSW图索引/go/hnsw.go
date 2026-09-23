package main

// HNSW 索引本体 —— 按 hnswlib 官方 hnswalg.h 转写。
//
// 转写对照：
//   - `mult_ = 1 / log(1.0 * M_)`、`revSize_ = 1.0 / mult_`
//   - `maxM_ = M_`、`maxM0_ = M_ * 2`、`Mcurmax = level ? maxM_ : maxM0_`
//   - `searchBaseLayer` 的双堆 + 打断条件 + `lowerBound = top_candidates.top().first`
//   - `getNeighborsByHeuristic2` 的多样性剪枝
//   - `mutuallyConnectNewElement` 的回连与满载收缩
//   - `searchKnn` 在 >0 层是纯贪心（ef=1），底层用 `ef = max(ef, k)`

import (
	"container/heap"
	"math"
)

// Index 是去掉锁、删除标记与内存池后的语义骨架。
type Index struct {
	Dim            int
	M              int
	MaxM           int
	MaxM0          int
	Mult           float64
	RevSize        float64
	EfConstruction int
	Ef             int

	Vectors [][]float64
	Levels  []int
	Links   []map[int][]int

	EnterPoint int
	MaxLevel   int

	uniforms []float64
	uPos     int

	Hops             int
	DistComputations int
	BrokeEarly       bool
}

// NewIndex 构造索引；mult = 1/log(M)，maxM0 = 2*M（官方 hnswalg.h 第 118-119 行）。
func NewIndex(dim, m, efConstruction, ef int, uniforms []float64) *Index {
	return &Index{
		Dim: dim, M: m, MaxM: m, MaxM0: m * 2,
		Mult:           1.0 / math.Log(float64(m)),
		RevSize:        math.Log(float64(m)),
		EfConstruction: efConstruction, Ef: ef,
		uniforms:   uniforms,
		EnterPoint: -1, MaxLevel: -1,
	}
}

func (idx *Index) nextU() float64 {
	u := idx.uniforms[idx.uPos%len(idx.uniforms)]
	idx.uPos++
	return u
}

func (idx *Index) dist(a, b []float64) float64 {
	idx.DistComputations++
	return L2Sqr(a, b)
}

// Mcurmax 对应 `size_t Mcurmax = level ? maxM_ : maxM0_;`
func (idx *Index) Mcurmax(level int) int {
	if level == 0 {
		return idx.MaxM0
	}
	return idx.MaxM
}

func (idx *Index) neighbors(node, level int) []int {
	return idx.Links[node][level]
}

// AddPoint 对应 addPoint：高层纯贪心下降，curlevel 及以下做 ef_construction 搜索 + 双向连接。
func (idx *Index) AddPoint(vec []float64) int {
	curC := len(idx.Vectors)
	idx.Vectors = append(idx.Vectors, vec)
	idx.Links = append(idx.Links, map[int][]int{})
	lv, _ := GetRandomLevel(idx.nextU(), idx.Mult)
	idx.Levels = append(idx.Levels, lv)

	if idx.EnterPoint < 0 {
		// 官方：第一个元素「Do nothing」
		idx.EnterPoint = 0
		idx.MaxLevel = lv
		return curC
	}

	maxLevelCopy := idx.MaxLevel
	ep := idx.EnterPoint
	nextClosest := ep

	for level := maxLevelCopy; level > lv; level-- {
		ep = idx.greedyDescend(vec, ep, level)
	}

	for level := min(lv, maxLevelCopy); level >= 0; level-- {
		top := idx.SearchLayer(vec, ep, idx.EfConstruction, level)
		selected := idx.mutuallyConnect(curC, top, level)
		if len(selected) > 0 {
			best := selected[0]
			for _, s := range selected {
				if s.Dist < best.Dist {
					best = s
				}
			}
			// 官方 `next_closest_entry_point = selectedNeighbors.back()`
			nextClosest = best.Node
		}
		ep = nextClosest
	}

	// 官方：入口点只在 curlevel > maxlevelcopy 时换成新点
	if lv > maxLevelCopy {
		idx.EnterPoint = curC
		idx.MaxLevel = lv
	}
	return curC
}

func (idx *Index) greedyDescend(vec []float64, ep, level int) int {
	curr, curDist := ep, idx.dist(vec, idx.Vectors[ep])
	changed := true
	for changed {
		changed = false
		idx.Hops++
		for _, c := range idx.neighbors(curr, level) {
			d := idx.dist(vec, idx.Vectors[c])
			if d < curDist {
				curDist, curr, changed = d, c, true
			}
		}
	}
	return curr
}

// SearchLayer 对应 searchBaseLayer：双堆 + 打断条件 + lowerBound = top().Dist。
func (idx *Index) SearchLayer(q []float64, ep, ef, level int) *MaxHeapOnDist {
	visited := map[int]bool{ep: true}
	top := &MaxHeapOnDist{}
	cand := &MinHeapOnDist{}
	heap.Init(top)
	heap.Init(cand)

	d := idx.dist(q, idx.Vectors[ep])
	top.PushItem(d, ep)
	lowerBound := d
	cand.PushItem(d, ep)

	idx.BrokeEarly = false
	for cand.Len() > 0 {
		c := cand.Top()
		// `if ((-curr_el_pair.first) > lowerBound && top_candidates.size() == ef_construction_) break;`
		if c.Dist > lowerBound && top.Len() == ef {
			idx.BrokeEarly = true
			break
		}
		cand.PopItem()
		for _, nb := range idx.neighbors(c.Node, level) {
			if visited[nb] {
				continue
			}
			visited[nb] = true
			d1 := idx.dist(q, idx.Vectors[nb])
			if top.Len() < ef || lowerBound > d1 {
				cand.PushItem(d1, nb)
				top.PushItem(d1, nb)
				if top.Len() > ef {
					top.PopItem()
				}
				if top.Len() > 0 {
					lowerBound = top.Top().Dist
				}
			}
		}
	}
	return top
}

// SearchKnn 对应 searchKnn：高层贪心 + 底层 ef = max(ef, k)。
func (idx *Index) SearchKnn(q []float64, k, ef int) []Item {
	if len(idx.Vectors) == 0 {
		return nil
	}
	effEf := ef
	if k > effEf {
		effEf = k
	}
	curr := idx.EnterPoint
	curDist := idx.dist(q, idx.Vectors[curr])
	for level := idx.MaxLevel; level > 0; level-- {
		changed := true
		for changed {
			changed = false
			idx.Hops++
			for _, c := range idx.neighbors(curr, level) {
				d := idx.dist(q, idx.Vectors[c])
				if d < curDist {
					curDist, curr, changed = d, c, true
				}
			}
		}
	}
	top := idx.SearchLayer(q, curr, effEf, 0)
	items := top.Items()
	if len(items) > k {
		items = items[:k]
	}
	return items
}

func (idx *Index) mutuallyConnect(curC int, top *MaxHeapOnDist, level int) []Item {
	mCurMax := idx.Mcurmax(level)
	selected := GetNeighborsByHeuristic2(top, idx.M, idx.Vectors)
	if len(selected) == 0 {
		idx.Links[curC][level] = nil
		return nil
	}
	ids := make([]int, 0, len(selected))
	for _, s := range selected {
		ids = append(ids, s.Node)
	}
	idx.Links[curC][level] = ids

	for _, s := range selected {
		links := append([]int{}, idx.neighbors(s.Node, level)...)
		if len(links) < mCurMax {
			idx.Links[s.Node][level] = append(links, curC)
			continue
		}
		// 满了：把新点并进去再跑一次启发式（以 s 为「查询」），容量用 Mcurmax
		cands := &MaxHeapOnDist{}
		heap.Init(cands)
		for _, x := range links {
			cands.PushItem(idx.dist(idx.Vectors[s.Node], idx.Vectors[x]), x)
		}
		cands.PushItem(s.Dist, curC)
		pruned := GetNeighborsByHeuristic2(cands, mCurMax, idx.Vectors)
		out := make([]int, 0, len(pruned))
		for _, p := range pruned {
			out = append(out, p.Node)
		}
		idx.Links[s.Node][level] = out
	}
	return selected
}

// GetNeighborsByHeuristic2 对应 getNeighborsByHeuristic2：
// 候选 c 被丢弃 ⇔ 存在已入选 s 使 d(s,c) < d(q,c)。
func GetNeighborsByHeuristic2(top *MaxHeapOnDist, m int, vectors [][]float64) []Item {
	items := top.Items()
	if len(items) < m {
		return items // 官方 `if (top_candidates.size() < M) return;`
	}
	chosen := make([]Item, 0, m)
	for _, c := range items {
		if len(chosen) >= m {
			break
		}
		good := true
		for _, s := range chosen {
			if L2Sqr(vectors[s.Node], vectors[c.Node]) < c.Dist {
				good = false
				break
			}
		}
		if good {
			chosen = append(chosen, c)
		}
	}
	return chosen
}
