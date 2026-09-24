package main

import (
	"fmt"
	"sort"
)

// Stats：遍历统计。
type Stats struct{ Leaves, Inner, Pruned, Scanned int }

func rel(lo, hi, qmin, qmax []int) string {
	allIn := true
	for d := range lo {
		if hi[d] < qmin[d] || lo[d] > qmax[d] {
			return "OUTSIDE"
		}
		if lo[d] < qmin[d] || hi[d] > qmax[d] {
			allIn = false
		}
	}
	if allIn {
		return "INSIDE"
	}
	return "CROSSES"
}

// intersect：BKDReader 的 pushBoundsLeft / pushBoundsRight 下推包围盒并剪枝。
func intersect(root *Node, cfg *BKDConfig, mins, maxs, qmin, qmax []int) ([]int, *Stats) {
	hits := []int{}
	st := &Stats{}
	var collect func(n *Node)
	collect = func(n *Node) {
		if n.IsLeaf {
			st.Leaves++
			st.Scanned += len(n.Docs)
			hits = append(hits, n.Docs...)
			return
		}
		st.Inner++
		collect(n.Left)
		collect(n.Right)
	}
	var walk func(n *Node, lo, hi []int)
	walk = func(n *Node, lo, hi []int) {
		r := rel(lo, hi, qmin, qmax)
		if r == "OUTSIDE" {
			st.Pruned++
			return
		}
		if n.IsLeaf {
			st.Leaves++
			if r == "INSIDE" {
				st.Scanned += len(n.Docs)
				hits = append(hits, n.Docs...)
				return
			}
			for i, p := range n.Pts {
				st.Scanned++
				in := true
				for d := range p {
					if p[d] < qmin[d] || p[d] > qmax[d] {
						in = false
						break
					}
				}
				if in {
					hits = append(hits, n.Docs[i])
				}
			}
			return
		}
		st.Inner++
		if r == "INSIDE" {
			collect(n.Left)
			collect(n.Right)
			return
		}
		lh := append([]int(nil), hi...)
		lh[n.SplitDim] = n.SplitVal // pushBoundsLeft：换上界
		walk(n.Left, lo, lh)
		rl := append([]int(nil), lo...)
		rl[n.SplitDim] = n.SplitVal // pushBoundsRight：换下界
		walk(n.Right, rl, hi)
	}
	walk(root, mins, maxs)
	sort.Ints(hits)
	return hits, st
}

func bruteForce(pts [][]int, qmin, qmax []int) []int {
	out := []int{}
	for i, p := range pts {
		in := true
		for d := range p {
			if p[d] < qmin[d] || p[d] > qmax[d] {
				in = false
				break
			}
		}
		if in {
			out = append(out, i)
		}
	}
	return out
}

func main() {
	cfg, err := newBKDConfig(2, 2, 8, 32)
	if err != nil {
		panic(err)
	}
	n := 1000
	pts := make([][]int, n)
	docs := make([]int, n)
	seed := uint32(2024)
	next := func(m int) int { // xorshift，确定性随机
		seed ^= seed << 13
		seed ^= seed >> 17
		seed ^= seed << 5
		return int(seed) % m
	}
	for i := 0; i < n; i++ {
		docs[i] = i
		pts[i] = []int{next(1 << 20), next(1 << 20)}
	}
	tree := buildTree(pts, docs, cfg)

	fmt.Println("== 1. 配置 ==")
	fmt.Printf("  numDims=%d numIndexDims=%d bytesPerDim=%d maxPointsInLeafNode=%d\n",
		cfg.NumDims, cfg.NumIndexDims, cfg.BytesPerDim, cfg.MaxPointsInLeafNode)
	fmt.Printf("  packedBytesLength=%d packedIndexBytesLength=%d bytesPerDoc=%d isDefault=%v\n",
		cfg.packedBytesLength(), cfg.packedIndexBytesLength(), cfg.bytesPerDoc(), cfg.isDefault())

	fmt.Println("== 2. 建树 ==")
	fmt.Printf("  点数=%d 叶子=%d 内节点=%d 节点总数=%d\n",
		n, len(leavesOf(tree)), nodeCount(tree)-len(leavesOf(tree)), nodeCount(tree))
	fmt.Printf("  根切分 dim=%d value=%d 左子树叶子=%d\n",
		tree.SplitDim, tree.SplitVal, len(leavesOf(tree.Left)))

	fmt.Println("== 3. 索引前缀编码 ==")
	codes := packIndex(tree, cfg)
	naive := len(codes) * cfg.BytesPerDim
	packed := 0
	for _, c := range codes {
		packed += 1 + len(c.Suffix)
	}
	fmt.Printf("  内节点=%d 朴素=%d 字节 前缀编码=%d 字节 省 %.1f%%\n",
		len(codes), naive, packed, 100.0*float64(naive-packed)/float64(naive))
	truth := []int{}
	var collectSplit func(nd *Node)
	collectSplit = func(nd *Node) {
		if nd.IsLeaf {
			return
		}
		truth = append(truth, nd.SplitVal)
		collectSplit(nd.Left)
		collectSplit(nd.Right)
	}
	collectSplit(tree)
	dec := decodePacked(tree, codes, cfg)
	for i := range dec {
		if dec[i] != truth[i] {
			panic(fmt.Sprintf("splitValue[%d] 解码 %d != 真值 %d", i, dec[i], truth[i]))
		}
	}
	fmt.Printf("  解码 %d 个 splitValue 全部与真值一致\n", len(dec))

	fmt.Println("== 4. 范围查询 ==")
	mins, maxs := make([]int, 2), make([]int, 2)
	for i := range pts {
		for d := 0; d < 2; d++ {
			if i == 0 || pts[i][d] < mins[d] {
				mins[d] = pts[i][d]
			}
			if i == 0 || pts[i][d] > maxs[d] {
				maxs[d] = pts[i][d]
			}
		}
	}
	fmt.Printf("  %-20s %6s %6s %6s %6s\n", "查询区间", "命中", "访问叶", "剪枝", "扫点")
	for _, q := range [][2]int{{0, (1 << 20) - 1}, {100000, 900000}, {200000, 400000}} {
		qmin := []int{q[0], q[0]}
		qmax := []int{q[1], q[1]}
		hits, st := intersect(tree, cfg, mins, maxs, qmin, qmax)
		want := bruteForce(pts, qmin, qmax)
		if len(hits) != len(want) {
			panic("命中数与暴力扫描不一致")
		}
		for i := range hits {
			if hits[i] != want[i] {
				panic("命中文档号与暴力扫描不一致")
			}
		}
		fmt.Printf("  [%7d,%7d]^2  %6d %6d %6d %6d\n",
			q[0], q[1], len(hits), st.Leaves, st.Pruned, st.Scanned)
	}
	fmt.Println("OK")
}
