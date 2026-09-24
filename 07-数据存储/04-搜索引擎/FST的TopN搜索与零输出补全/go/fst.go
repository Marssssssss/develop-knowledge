// FST 结构与"输出前推"：让每个非根节点都至少有一条 NO_OUTPUT 弧。
//
// 转写自 apache/lucene@main 的 lucene/core/src/java/org/apache/lucene/util/fst/Util.java
// 所依赖的 FST 不变式（`assert foundZero`）。
//
// 建模口径：FST 的字节编码与后缀共享不还原，用显式节点/弧结构代替。
package main

import "sort"

// NoOutput 本 demo 的 output 就是整数，add 是加法，noOutput 是 0。
const NoOutput = 0

// EndLabel FST.END_LABEL
const EndLabel = -1

// Node 一个 FST 节点；Arcs 按 label 升序。
type Node struct {
	Arcs []Arc
}

// Arc 一条出弧（label / output / target）。
type Arc struct {
	Label  int
	Output int
	Target *Node
}

// AddArc 插入一条弧并保持按 label 升序。
func (n *Node) AddArc(label, output int, target *Node) {
	n.Arcs = append(n.Arcs, Arc{label, output, target})
	sort.Slice(n.Arcs, func(i, j int) bool { return n.Arcs[i].Label < n.Arcs[j].Label })
}

// HasZeroArc 是否有一条 NO_OUTPUT 弧。
func (n *Node) HasZeroArc() bool {
	for _, a := range n.Arcs {
		if a.Output == NoOutput {
			return true
		}
	}
	return false
}

// MinOutput 出弧里最小的 output。
func (n *Node) MinOutput() int {
	if len(n.Arcs) == 0 {
		return NoOutput
	}
	m := n.Arcs[0].Output
	for _, a := range n.Arcs[1:] {
		if a.Output < m {
			m = a.Output
		}
	}
	return m
}

// FST 一棵 FST。
type FST struct {
	Root *Node
}

// NewFST 构造一棵空 FST。
func NewFST() *FST { return &FST{Root: &Node{}} }

// Nodes 遍历全部节点（去重）。
func (f *FST) Nodes() []*Node {
	seen := map[*Node]bool{}
	out := []*Node{}
	stack := []*Node{f.Root}
	for len(stack) > 0 {
		n := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if seen[n] {
			continue
		}
		seen[n] = true
		out = append(out, n)
		for _, a := range n.Arcs {
			stack = append(stack, a.Target)
		}
	}
	return out
}

// CheckZeroArcInvariant 不变式只约束**非根节点**：
// 根的全部出弧由 AddStartPaths 枚举，零输出补全是从根的**目标节点**才开始的。
func (f *FST) CheckZeroArcInvariant() bool {
	for _, n := range f.Nodes() {
		if n != f.Root && len(n.Arcs) > 0 && !n.HasZeroArc() {
			return false
		}
	}
	return true
}

// PushOutputsToRoot 自底向上把输出往根推，保证每个非根节点有一条零弧。
//
// 根**不参与**：它没有入弧可吸收 min，减了会让所有路径整体偏移；而且根也不需要零弧。
func (f *FST) PushOutputsToRoot() {
	order := f.Nodes()
	incoming := map[*Node][]*Node{}
	for _, n := range order {
		for _, a := range n.Arcs {
			incoming[a.Target] = append(incoming[a.Target], n)
		}
	}
	depth := map[*Node]int{}
	stack := []struct {
		n *Node
		d int
	}{{f.Root, 0}}
	for len(stack) > 0 {
		cur := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if _, ok := depth[cur.n]; ok {
			continue
		}
		depth[cur.n] = cur.d
		for _, a := range cur.n.Arcs {
			stack = append(stack, struct {
				n *Node
				d int
			}{a.Target, cur.d + 1})
		}
	}
	sort.SliceStable(order, func(i, j int) bool { return depth[order[i]] > depth[order[j]] })
	for _, n := range order {
		if len(n.Arcs) == 0 || n == f.Root {
			continue
		}
		m := n.MinOutput()
		if m == NoOutput {
			continue
		}
		for i := range n.Arcs {
			n.Arcs[i].Output -= m
		}
		for _, src := range incoming[n] {
			for i := range src.Arcs {
				if src.Arcs[i].Target == n {
					src.Arcs[i].Output += m
				}
			}
		}
	}
}

// Build 由 (input, output) 列表建一棵 FST，可选是否做输出前推。
func Build(entries [][2]interface{}, push bool) *FST {
	fst := NewFST()
	for _, e := range entries {
		inp := e[0].([]int)
		out := e[1].(int)
		n := fst.Root
		for _, ch := range inp {
			var nxt *Node
			for _, a := range n.Arcs {
				if a.Label == ch {
					nxt = a.Target
					break
				}
			}
			if nxt == nil {
				nxt = &Node{}
				n.AddArc(ch, NoOutput, nxt)
			}
			n = nxt
		}
		n.AddArc(EndLabel, out, &Node{})
	}
	if push {
		fst.PushOutputsToRoot()
	}
	return fst
}

// AllPaths 暴力枚举所有 (input, output)，用于和搜索结果对拍。
func AllPaths(fst *FST) [][2]interface{} {
	out := [][2]interface{}{}
	var walk func(n *Node, accOut int, accIn []int)
	walk = func(n *Node, accOut int, accIn []int) {
		for _, a := range n.Arcs {
			if a.Label == EndLabel {
				cp := append([]int{}, accIn...)
				out = append(out, [2]interface{}{cp, accOut + a.Output})
			} else {
				walk(a.Target, accOut+a.Output, append(append([]int{}, accIn...), a.Label))
			}
		}
	}
	walk(fst.Root, 0, []int{})
	return out
}
