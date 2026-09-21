// Package main 把 EXPLAIN ANALYZE 的读数口径(PostgreSQL 18 文档 14.1)转写成 Go。
package main

import (
	"fmt"
	"strings"
)

// Node 是一个计划节点: 估算 + instrumentation。
type Node struct {
	NodeType    string
	EstStartup  float64
	EstTotal    float64
	EstRows     float64
	EstWidth    int
	Extra       [][2]string
	Children    []*Node
	NumTuples   float64
	NumLoops    int
	RowsRemoved int
	Scanned     int
	FirstSum    float64 // 各 loop 首行时刻之和
	LastSum     float64 // 各 loop 末行时刻之和
}

// Run 记录一次执行(一次 loop)。时刻参数是该 loop 的耗时。
func (n *Node) Run(tuplesOut float64, scanned int, firstMS, lastMS float64, removed int) {
	n.NumLoops++
	n.NumTuples += tuplesOut
	n.RowsRemoved += removed
	n.Scanned += scanned
	n.FirstSum += firstMS
	n.LastSum += lastMS
}

// ActualRows 文档口径: 每次执行的平均行数; BitmapAnd/BitmapOr 恒 0。
func (n *Node) ActualRows() float64 {
	if n.NodeType == "BitmapAnd" || n.NodeType == "BitmapOr" {
		return 0
	}
	if n.NumLoops == 0 {
		return 0
	}
	return n.NumTuples / float64(n.NumLoops)
}

// TotalRows 真实总行数 = rows × loops。
func (n *Node) TotalRows() float64 {
	return n.ActualRows() * float64(n.NumLoops)
}

// ActualTime 返回每次执行的平均首/末时刻。
func (n *Node) ActualTime() (float64, float64) {
	if n.NumLoops == 0 {
		return 0, 0
	}
	return n.FirstSum / float64(n.NumLoops), n.LastSum / float64(n.NumLoops)
}

// TotalTime 总耗时 = 均值 × loops。
func (n *Node) TotalTime() (float64, float64) {
	f, l := n.ActualTime()
	return f * float64(n.NumLoops), l * float64(n.NumLoops)
}

// EstError 估算行数 / 实测总行数; false 表示不可比。
func (n *Node) EstError() (float64, bool) {
	real := n.TotalRows()
	if real <= 1e-9 || n.EstRows <= 1e-9 {
		return 0, false
	}
	return n.EstRows / real, true
}

// Render 生成与 EXPLAIN 文本输出同构的字符串。
func (n *Node) Render(indent int, analyze bool) string {
	pad := strings.Repeat("  ", indent)
	if indent > 0 {
		pad += "-> "
	}
	line := fmt.Sprintf("%s%s (cost=%.2f..%.2f rows=%.0f width=%d)",
		pad, n.NodeType, n.EstStartup, n.EstTotal, n.EstRows, n.EstWidth)
	if analyze {
		f, l := n.ActualTime()
		line += fmt.Sprintf(" (actual time=%.3f..%.3f rows=%.2f loops=%d)",
			f, l, n.ActualRows(), n.NumLoops)
	}
	for _, kv := range n.Extra {
		line += "\n" + strings.Repeat("  ", indent+3) + kv[0] + ": " + kv[1]
	}
	if analyze && n.NumLoops == 0 {
		line += " (never executed)"
	}
	if n.RowsRemoved > 0 {
		line += fmt.Sprintf("\n%sRows Removed by Filter: %d",
			strings.Repeat("  ", indent+3), n.RowsRemoved)
	}
	out := []string{line}
	for _, c := range n.Children {
		out = append(out, c.Render(indent+1, analyze))
	}
	return strings.Join(out, "\n")
}

// SeqScanCost 文档算式: pages*seq_page_cost + rows*cpu_tuple_cost。
func SeqScanCost(relpages, reltuples float64) float64 {
	return relpages*1.0 + reltuples*0.01
}
