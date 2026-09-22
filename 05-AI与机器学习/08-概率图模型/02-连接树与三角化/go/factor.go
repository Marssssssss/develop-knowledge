package main

import (
	"fmt"
	"strings"
)

package main

import (
	"fmt"
	"sort"
	"strings"
)

// Factor 离散因子：Vars 有序，Table 以「按 Vars 顺序拼成的字符串」为键。
type Factor struct {
	Vars  []string
	Table map[string]float64
}

// NewFactor 建因子。
func NewFactor(vars []string, table map[string]float64) Factor {
	return Factor{Vars: append([]string{}, vars...), Table: table}
}

// Copy 拷贝。
func (f Factor) Copy() Factor {
	t := map[string]float64{}
	for k, v := range f.Table {
		t[k] = v
	}
	return Factor{Vars: append([]string{}, f.Vars...), Table: t}
}

func keyOf(vals []int) string {
	parts := []string{}
	for _, v := range vals {
		parts = append(parts, fmt.Sprintf("%d", v))
	}
	return strings.Join(parts, "|")
}

func parseKey(k string) []int {
	if k == "" {
		return []int{}
	}
	parts := strings.Split(k, "|")
	out := []int{}
	for _, p := range parts {
		v := 0
		fmt.Sscanf(p, "%d", &v)
		out = append(out, v)
	}
	return out
}

// Product 因子乘：变量取并集，冲突赋值跳过。
func (f Factor) Product(g Factor) Factor {
	vs := append([]string{}, f.Vars...)
	for _, v := range g.Vars {
		if !contains(vs, v) {
			vs = append(vs, v)
		}
	}
	pos := map[string]int{}
	for i, v := range vs {
		pos[v] = i
	}
	out := map[string]float64{}
	for ka, va := range f.Table {
		av := parseKey(ka)
		for kb, vb := range g.Table {
			bv := parseKey(kb)
			row := make([]int, len(vs))
			for i := range row {
				row[i] = -1
			}
			ok := true
			for i, v := range f.Vars {
				row[pos[v]] = av[i]
			}
			for i, v := range g.Vars {
				if row[pos[v]] != -1 && row[pos[v]] != bv[i] {
					ok = false
					break
				}
				row[pos[v]] = bv[i]
			}
			if ok {
				out[keyOf(row)] = va * vb
			}
		}
	}
	return Factor{Vars: vs, Table: out}
}

// Marginalize 对 drop 中的变量求和消元。
func (f Factor) Marginalize(drop []string) Factor {
	keep := []string{}
	kidx := []int{}
	for i, v := range f.Vars {
		if !contains(drop, v) {
			keep = append(keep, v)
			kidx = append(kidx, i)
		}
	}
	out := map[string]float64{}
	for k, v := range f.Table {
		av := parseKey(k)
		row := []int{}
		for _, i := range kidx {
			row = append(row, av[i])
		}
		out[keyOf(row)] += v
	}
	return Factor{Vars: keep, Table: out}
}

// Divide 同作用域相除。
func (f Factor) Divide(g Factor) Factor {
	idx := []int{}
	for _, v := range g.Vars {
		for i, x := range f.Vars {
			if x == v {
				idx = append(idx, i)
			}
		}
	}
	out := map[string]float64{}
	for k, v := range f.Table {
		av := parseKey(k)
		row := []int{}
		for _, i := range idx {
			row = append(row, av[i])
		}
		out[k] = v / g.Table[keyOf(row)]
	}
	return Factor{Vars: f.Vars, Table: out}
}

// Normalize 归一化。
func (f Factor) Normalize() Factor {
	z := 0.0
	for _, v := range f.Table {
		z += v
	}
	out := map[string]float64{}
	for k, v := range f.Table {
		out[k] = v / z
	}
	return Factor{Vars: f.Vars, Table: out}
}
