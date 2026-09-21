package main

import "sort"

// ---------------------------------------------------------------- 特征

// Set 是排序去重的字符串集合，用于 Jaccard。
type Set []string

func newSet(xs ...string) Set {
	m := map[string]bool{}
	for _, x := range xs {
		m[x] = true
	}
	out := make(Set, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func (s Set) has(x string) bool {
	for _, v := range s {
		if v == x {
			return true
		}
	}
	return false
}

func jaccard(a, b Set) float64 {
	if len(a) == 0 && len(b) == 0 {
		return 1.0
	}
	inter, union := 0, map[string]bool{}
	for _, x := range a {
		union[x] = true
	}
	for _, x := range b {
		union[x] = true
	}
	for x := range union {
		if a.has(x) && b.has(x) {
			inter++
		}
	}
	return float64(inter) / float64(len(union))
}

// Msg 一条被监控到的协议消息。
type Msg struct {
	ID        string
	Direction string
	Keywords  Set
	Funcs     Set
	Syscalls  Set
	FileOps   Set
}

// Distance d(a,b) = 1 - sum_i w_i*s_i(a,b)；三组各 1/3，组内等权。
func Distance(a, b Msg) float64 {
	d := 0.0
	if a.Direction == b.Direction {
		d += 1.0
	}
	s := (1.0/6)*d + (1.0/6)*jaccard(a.Keywords, b.Keywords) +
		(1.0/6)*jaccard(a.Funcs, b.Funcs) + (1.0/6)*jaccard(a.Syscalls, b.Syscalls) +
		(1.0/3)*jaccard(a.FileOps, b.FileOps)
	return 1.0 - s
}
