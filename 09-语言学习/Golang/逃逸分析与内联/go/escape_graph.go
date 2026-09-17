// 位置的图模型：顶点 = 分配语句/表达式，边权 = 解引用次数 − 取地址次数。
//
// 依据 cmd/compile/internal/escape/escape.go 头部注释。与 python/escape_graph.py 同构。
package main

import (
	"fmt"
	"strings"
)

// location 是位置图的一个顶点：一条分配语句或表达式。
type location struct {
	name    string
	kind    string
	escapes bool
}

// edge 表示一次赋值，derefs = 解引用次数 - 取地址次数。
type edge struct {
	dst    *location
	src    *location
	derefs int
}

type escapeGraph struct {
	locs      map[string]*location
	edges     []edge
	leaks     map[string]map[int]bool // func -> 第 i 个参数会进堆
	results   map[string]map[int]bool // func -> 第 i 个参数会作为结果返回
	notes     []string
}

func newEscapeGraph() *escapeGraph {
	return &escapeGraph{
		locs:    map[string]*location{},
		leaks:   map[string]map[int]bool{},
		results: map[string]map[int]bool{},
	}
}

func (g *escapeGraph) loc(name string) *location {
	if l, ok := g.locs[name]; ok {
		return l
	}
	kind := kindLocal
	if name == "heap" || name == "callee" || name == "blank" {
		kind = name
	}
	l := &location{name: name, kind: kind, escapes: kind == kindHeap || kind == kindCallee}
	g.locs[name] = l
	return l
}

func (g *escapeGraph) assign(dst, src string, derefs int) *escapeGraph {
	g.edges = append(g.edges, edge{g.loc(dst), g.loc(src), derefs})
	return g
}

func (g *escapeGraph) defineFunc(name string, leaks, results []int) {
	g.leaks[name] = map[int]bool{}
	g.results[name] = map[int]bool{}
	for _, i := range leaks {
		g.leaks[name][i] = true
	}
	for _, i := range results {
		g.results[name][i] = true
	}
}

func (g *escapeGraph) call(name string, args ...string) {
	for i := range g.leaks[name] {
		g.loc(args[i]).escapes = true
		g.notes = append(g.notes, fmt.Sprintf("%s 参数 #%d → 堆 (leaks)", name, i))
	}
	for i := range g.results[name] {
		g.loc(args[i]).escapes = true
		g.notes = append(g.notes, fmt.Sprintf("%s 参数 #%d → 返回值 (result)", name, i))
	}
}

// solve 是不动点：dst 逃逸且 derefs <= 0 → src 逃逸。
func (g *escapeGraph) solve() *escapeGraph {
	for changed := true; changed; {
		changed = false
		for _, e := range g.edges {
			if e.dst.escapes && e.derefs <= 0 && !e.src.escapes {
				e.src.escapes = true
				changed = true
			}
		}
	}
	return g
}

func (g *escapeGraph) stackLocals() []string {
	out := []string{}
	for name, l := range g.locs {
		if l.kind == kindLocal && !l.escapes {
			out = append(out, name)
		}
	}
	return out
}

// derefsOf 从赋值语句文本算出 escape.go 里的边权：解引用次数 − 取地址次数。
// 官方注释给出的五个例子（含 `p = **&**&q` 为 2）都由本函数重算得到。
func derefsOf(assign string) int {
	i := strings.Index(assign, "=")
	rhs := strings.TrimSpace(assign[i+1:])
	return strings.Count(rhs, "*") - strings.Count(rhs, "&")
}
