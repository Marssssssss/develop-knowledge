package main

import (
	"fmt"
	"sort"
)

// Node 既当内节点也当叶子用。
type Node struct {
	IsLeaf    bool
	SplitDim  int
	SplitVal  int
	Left      *Node
	Right     *Node
	Docs      []int // 叶子：文档号，与 Pts 一一对应
	Pts       [][]int
	CPL       []int // 叶子：各维度公共前缀长度
	SortedDim int
	Card      int // 叶子：leafCardinality
}

func leavesOf(n *Node) []*Node {
	if n.IsLeaf {
		return []*Node{n}
	}
	out := leavesOf(n.Left)
	return append(out, leavesOf(n.Right)...)
}

func nodeCount(n *Node) int {
	if n.IsLeaf {
		return 1
	}
	return 1 + nodeCount(n.Left) + nodeCount(n.Right)
}

func bounds(pts [][]int, frm, to, numDims int) (mins, maxs []int) {
	mins = make([]int, numDims)
	maxs = make([]int, numDims)
	for d := 0; d < numDims; d++ {
		mins[d] = pts[frm][d]
		maxs[d] = pts[frm][d]
		for i := frm + 1; i < to; i++ {
			if pts[i][d] < mins[d] {
				mins[d] = pts[i][d]
			}
			if pts[i][d] > maxs[d] {
				maxs[d] = pts[i][d]
			}
		}
	}
	return
}

func samePoint(a, b []int) bool {
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// sortRange：把 [frm,to) 按 dim 稳定排序（等价 MutablePointTreeReaderUtils.partition
// 中「把中位数放到 mid」的效果，排序后 pts[mid] 即分裂值）。
func sortRange(pts [][]int, docs []int, frm, to, dim int) {
	order := make([]int, to-frm)
	for i := range order {
		order[i] = frm + i
	}
	sort.SliceStable(order, func(a, b int) bool {
		return pts[order[a]][dim] < pts[order[b]][dim]
	})
	np := make([][]int, len(order))
	nd := make([]int, len(order))
	for i, ix := range order {
		np[i] = pts[ix]
		nd[i] = docs[ix]
	}
	copy(pts[frm:to], np)
	copy(docs[frm:to], nd)
}

func leafStats(pts [][]int, docs []int, frm, to int, cfg *BKDConfig) *Node {
	n := cfg.NumDims
	bpd := cfg.BytesPerDim
	cpl := make([]int, n)
	for i := range cpl {
		cpl[i] = bpd
	}
	for i := frm + 1; i < to; i++ {
		for d := 0; d < n; d++ {
			c := commonPrefixLength(pts[frm][d], pts[i][d], bpd)
			if c < cpl[d] {
				cpl[d] = c
			}
		}
	}
	// usedBytes：源码从 from+1 起统计，第一个点不参与
	used := map[int]int{}
	for d := 0; d < n; d++ {
		if cpl[d] < bpd {
			seen := map[int]bool{}
			for i := frm + 1; i < to; i++ {
				seen[byteAt(pts[i][d], bpd, cpl[d])] = true
			}
			used[d] = len(seen)
		}
	}
	sortedDim := 0
	best := -1
	for d := 0; d < n; d++ {
		if c, ok := used[d]; ok && (best == -1 || c < best) {
			best = c
			sortedDim = d
		}
	}
	sortRange(pts, docs, frm, to, sortedDim)
	np := make([][]int, to-frm)
	nd := make([]int, to-frm)
	copy(np, pts[frm:to])
	copy(nd, docs[frm:to])
	card := 1
	for i := 1; i < len(np); i++ {
		if !samePoint(np[i], np[i-1]) {
			card++
		}
	}
	return &Node{IsLeaf: true, Docs: nd, Pts: np, CPL: cpl, SortedDim: sortedDim, Card: card}
}

func build(pts [][]int, docs []int, frm, to, numLeaves, totalLeaves int,
	mins, maxs, parentSplits []int, cfg *BKDConfig) *Node {
	if numLeaves == 1 {
		return leafStats(pts, docs, frm, to, cfg)
	}
	splitDim := 0
	if cfg.NumIndexDims > 1 {
		if needsExactBounds(numLeaves, totalLeaves, cfg.NumIndexDims, parentSplits) {
			mins, maxs = bounds(pts, frm, to, cfg.NumDims)
		}
		splitDim = chooseSplitDim(mins, maxs, parentSplits, cfg.NumIndexDims)
	}
	numLeft := getNumLeftLeafNodes(numLeaves)
	mid := frm + numLeft*cfg.MaxPointsInLeafNode
	if frm >= mid || mid >= to {
		panic(fmt.Sprintf("mid=%d out of (%d,%d)", mid, frm, to))
	}
	sortRange(pts, docs, frm, to, splitDim)
	splitVal := pts[mid][splitDim]

	leftMaxs := append([]int(nil), maxs...)
	leftMaxs[splitDim] = splitVal
	rightMins := append([]int(nil), mins...)
	rightMins[splitDim] = splitVal

	parentSplits[splitDim]++
	left := build(pts, docs, frm, mid, numLeft, totalLeaves, mins, leftMaxs, parentSplits, cfg)
	right := build(pts, docs, mid, to, numLeaves-numLeft, totalLeaves, rightMins, maxs, parentSplits, cfg)
	parentSplits[splitDim]--
	return &Node{IsLeaf: false, SplitDim: splitDim, SplitVal: splitVal, Left: left, Right: right}
}

// buildTree：points 为 [numDims]int 的切片，docs 与之等长。
func buildTree(pts [][]int, docs []int, cfg *BKDConfig) *Node {
	total := numLeavesOf(len(pts), cfg.MaxPointsInLeafNode)
	mins, maxs := bounds(pts, 0, len(pts), cfg.NumDims)
	ps := make([]int, cfg.NumIndexDims)
	return build(pts, docs, 0, len(pts), total, total, mins, maxs, ps, cfg)
}
