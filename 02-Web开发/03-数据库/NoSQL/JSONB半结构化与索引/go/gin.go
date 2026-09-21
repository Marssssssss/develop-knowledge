package main

import (
	"sort"
)

// GinIndex 是 GIN 倒排索引（lossy，必须 recheck）。
type GinIndex struct {
	Name          string
	Opclass       string
	FastUpdate    bool
	PendingLimit  int
	Main          map[string]map[int]bool
	Pending       []entry
	Cleanups      int
}

type entry struct {
	Key string
	TID int
}

func NewGinIndex(name, opclass string, fastUpdate bool, limit int) (*GinIndex, error) {
	if opclass != "jsonb_ops" && opclass != "jsonb_path_ops" {
		return nil, ErrOpclass
	}
	return &GinIndex{
		Name: name, Opclass: opclass, FastUpdate: fastUpdate, PendingLimit: limit,
		Main: map[string]map[int]bool{},
	}, nil
}

// Supports 两个操作符类支持的运算符不同。
func (g *GinIndex) Supports(op string) bool {
	if op == "@>" || op == "@?" || op == "@@" {
		return true
	}
	return g.Opclass == "jsonb_ops" // ? ?| ?& 只有 jsonb_ops 支持
}

// keysOf 抽取索引条目：jsonb_ops 键/值分开，jsonb_path_ops 合成路径+值哈希。
func (g *GinIndex) keysOf(doc any, prefix string) []string {
	var out []string
	switch x := doc.(type) {
	case map[string]any:
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			if g.Opclass == "jsonb_ops" {
				out = append(out, "K"+prefix+"."+k)
			}
			out = append(out, g.keysOf(x[k], prefix+"."+k)...)
		}
	case []any:
		for i, v := range x {
			out = append(out, g.keysOf(v, fmt.Sprintf("%s[%d]", prefix, i))...)
		}
	default:
		if g.Opclass == "jsonb_ops" {
			out = append(out, "V"+fmt.Sprint(x))
		} else {
			out = append(out, "H"+prefix+"="+fmt.Sprint(x))
		}
	}
	return out
}

func (g *GinIndex) Add(tid int, doc any) {
	if !g.FastUpdate {
		for _, k := range g.keysOf(doc, "") {
			if g.Main[k] == nil {
				g.Main[k] = map[int]bool{}
			}
			g.Main[k][tid] = true
		}
		return
	}
	for _, k := range g.keysOf(doc, "") {
		g.Pending = append(g.Pending, entry{k, tid})
	}
	if len(g.Pending) > g.PendingLimit {
		g.Cleanup()
	}
}

// Cleanup 超过 gin_pending_list_limit 就把 pending 合并进主结构。
func (g *GinIndex) Cleanup() {
	for _, e := range g.Pending {
		if g.Main[e.Key] == nil {
			g.Main[e.Key] = map[int]bool{}
		}
		g.Main[e.Key][e.TID] = true
	}
	g.Pending = nil
	g.Cleanups++
}

// Search 返回 (候选集, recheck 后的真结果)。
func (g *GinIndex) Search(docs map[int]any, needle any) ([]int, []int) {
	want := map[string]bool{}
	for _, k := range g.keysOf(needle, "") {
		want[k] = true
	}
	seen := map[int]bool{}
	for k, tids := range g.Main {
		if !want[k] {
			continue
		}
		for tid := range tids {
			seen[tid] = true
		}
	}
	for _, e := range g.Pending { // 查询必须同时扫 pending list，否则会漏
		if want[e.Key] {
			seen[e.TID] = true
		}
	}
	var cand, real []int
	for tid := range seen {
		cand = append(cand, tid)
		if Contains(docs[tid], needle) {
			real = append(real, tid)
		}
	}
	sort.Ints(cand)
	sort.Ints(real)
	return cand, real
}
