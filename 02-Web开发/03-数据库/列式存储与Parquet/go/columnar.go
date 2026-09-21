// Package columnar —— Parquet 嵌套列式编码（parquet-format 官方规范转写）。
//
// 与 Python 版的**显式语言差异**：
//   * Python 的 None 在 Go 里用 (value any, isNull bool) 显式区分，
//     因为 map[string]any 的 nil 与「键不存在」是两回事；
//   * Python 的 int 是无精度限制的，Go 用 int64，位移必须显式掩码；
//   * Python 抛异常，这里用 (value, error)。
package main

import (
	"fmt"
	"sort"
)

const (
	Required = "required"
	Optional = "optional"
	Repeated = "repeated"
)

// Node 是 schema 里的一个节点。
type Node struct {
	Name     string
	Rep      string
	Children []*Node
}

func (n *Node) IsLeaf() bool { return len(n.Children) == 0 }

// Find 按列名路径取出节点链（链里含起点自身）。
func (n *Node) Find(path ...string) ([]*Node, error) {
	if len(path) == 0 || path[0] != n.Name {
		return nil, fmt.Errorf("path %v does not start at %s", path, n.Name)
	}
	chain := []*Node{n}
	cur := n
	for _, name := range path[1:] {
		var next *Node
		for _, c := range cur.Children {
			if c.Name == name {
				next = c
				break
			}
		}
		if next == nil {
			return nil, fmt.Errorf("no such field %s", name)
		}
		chain = append(chain, next)
		cur = next
	}
	return chain, nil
}

// DocumentSchema 是 Dremel 论文的 Document schema。
func DocumentSchema() *Node {
	return &Node{"Document", Required, []*Node{
		{Name: "DocId", Rep: Required},
		{Name: "Links", Rep: Optional, Children: []*Node{
			{Name: "Backward", Rep: Repeated},
			{Name: "Forward", Rep: Repeated},
		}},
		{Name: "Name", Rep: Repeated, Children: []*Node{
			{Name: "Language", Rep: Repeated, Children: []*Node{
				{Name: "Code", Rep: Required},
				{Name: "Country", Rep: Optional},
			}},
			{Name: "Url", Rep: Optional},
		}},
	}}
}

// MaxDefinitionLevel 数路径上 optional 与 repeated 的节点个数。
func MaxDefinitionLevel(path []*Node) int {
	n := 0
	for _, node := range path {
		if node.Rep == Optional || node.Rep == Repeated {
			n++
		}
	}
	return n
}

// MaxRepetitionLevel 数路径上 repeated 的节点个数。
func MaxRepetitionLevel(path []*Node) int {
	n := 0
	for _, node := range path {
		if node.Rep == Repeated {
			n++
		}
	}
	return n
}

// DefCounts 给出每层的累计 optional/repeated 个数：node i 被定义 <=> d >= defcounts[i]。
func DefCounts(path []*Node) []int {
	out := make([]int, len(path))
	c := 0
	for i, node := range path {
		if node.Rep == Optional || node.Rep == Repeated {
			c++
		}
		out[i] = c
	}
	return out
}

// RepeatedIndex 给出每个 repeated 节点在路径里的序号（1 起）。
func RepeatedIndex(path []*Node) map[int]int {
	out := map[int]int{}
	k := 0
	for i, node := range path {
		if node.Rep == Repeated {
			k++
			out[i] = k
		}
	}
	return out
}
