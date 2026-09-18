// Tupni（CCS 2008）最小复现：chunk → 字段 → 记录序列 → 记录类型。
package main

import "sort"

// Inst 执行轨迹里的一条**非 mov** 指令：地址 + 它访问的输入偏移。
type Inst struct {
	Eip  string
	Offs []int
}

type Chunk struct{ Lo, Hi int }

// splitRuns 把一组偏移切成若干连续段（一个操作数里可能有多段）。
func splitRuns(offs []int) []Chunk {
	s := append([]int{}, offs...)
	sort.Ints(s)
	out := []Chunk{}
	lo, hi := s[0], s[0]+1
	for _, o := range s[1:] {
		if o == hi {
			hi = o + 1
		} else {
			out = append(out, Chunk{lo, hi})
			lo, hi = o, o+1
		}
	}
	return append(out, Chunk{lo, hi})
}

// buildChunks §3.3：每个 chunk 的权重 = 访问它的指令条数（mov 已在轨迹外）。
func buildChunks(trace []Inst) map[Chunk]int {
	w := map[Chunk]int{}
	for _, it := range trace {
		for _, c := range splitRuns(it.Offs) {
			w[c]++
		}
	}
	return w
}

// greedyPacking §3.3.1：加权 Maximum k-Set Packing 的贪心解（权重降序取不重叠者）。
func greedyPacking(w map[Chunk]int) []Chunk {
	keys := make([]Chunk, 0, len(w))
	for k := range w {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(i, j int) bool {
		if w[keys[i]] != w[keys[j]] {
			return w[keys[i]] > w[keys[j]]
		}
		return keys[i].Lo < keys[j].Lo
	})
	out := []Chunk{}
	for _, c := range keys {
		ok := true
		for _, x := range out {
			if c.Lo < x.Hi && x.Lo < c.Hi {
				ok = false
				break
			}
		}
		if ok {
			out = append(out, c)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Lo < out[j].Lo })
	return out
}

// virtualFields §3.3.1：未被访问的连续区间 → virtual field。
func virtualFields(fields []Chunk, msgLen int) []Chunk {
	out, pos := []Chunk{}, 0
	for _, f := range fields {
		if f.Lo > pos {
			out = append(out, Chunk{pos, f.Lo})
		}
		pos = f.Hi
	}
	if pos < msgLen {
		out = append(out, Chunk{pos, msgLen})
	}
	return out
}

func fieldOf(fields []Chunk, off int) int {
	for i, f := range fields {
		if f.Lo <= off && off < f.Hi {
			return i
		}
	}
	return -1
}

// splitIterations §3.4.1：按循环唯一入口点把子序列切成各次迭代。
func splitIterations(trace []Inst, entry string) [][]Inst {
	iters, cur := [][]Inst{}, []Inst{}
	for _, r := range trace {
		if r.Eip == entry && len(cur) > 0 {
			iters = append(iters, cur)
			cur = []Inst{}
		}
		cur = append(cur, r)
	}
	if len(cur) > 0 {
		iters = append(iters, cur)
	}
	return iters
}

// iterationDependent §3.4.2：Ii = 第 i 次迭代里访问了「其他迭代都没访问的字段」的指令。
// 论文：若 I_n 为空则把循环当作只有 n-1 次迭代。
func iterationDependent(iters [][]Inst, fields []Chunk) ([]map[string]bool, int) {
	n := len(iters)
	touched := make([]map[[2]interface{}]bool, n)
	perInst := make([]map[string]map[int]bool, n)
	for i := range iters {
		touched[i] = map[[2]interface{}]bool{}
		perInst[i] = map[string]map[int]bool{}
	}
	for i, it := range iters {
		for _, ins := range it {
			if perInst[i][ins.Eip] == nil {
				perInst[i][ins.Eip] = map[int]bool{}
			}
			for _, o := range ins.Offs {
				if f := fieldOf(fields, o); f >= 0 {
					touched[i][[2]interface{}{ins.Eip, f}] = true
					perInst[i][ins.Eip][f] = true
				}
			}
		}
	}
	I := make([]map[string]bool, n)
	for i := 0; i < n; i++ {
		I[i] = map[string]bool{}
		for eip, fs := range perInst[i] {
			uniq := false
			for f := range fs {
				seen := false
				for j := 0; j < n && !seen; j++ {
					if j != i && touched[j][[2]interface{}{eip, f}] {
						seen = true
					}
				}
				if !seen {
					uniq = true
				}
			}
			if uniq {
				I[i][eip] = true
			}
		}
	}
	if n > 0 && len(I[n-1]) == 0 {
		n--
		I = I[:n]
	}
	return I, n
}

type boundaries struct {
	s, e []int
}

// findRecordBoundaries §3.4.3 Figure 4。
func findRecordBoundaries(n int, I []map[string]bool, fields []Chunk, iters [][]Inst) boundaries {
	instField := make([]map[string]map[int]bool, n)
	for i := 0; i < n; i++ {
		instField[i] = map[string]map[int]bool{}
		for _, ins := range iters[i] {
			if instField[i][ins.Eip] == nil {
				instField[i][ins.Eip] = map[int]bool{}
			}
			for _, o := range ins.Offs {
				if f := fieldOf(fields, o); f >= 0 {
					instField[i][ins.Eip][f] = true
				}
			}
		}
	}
	startOf := func(eip string, i int) int {
		best := -1
		for f := range instField[i][eip] {
			if best < 0 || fields[f].Lo < best {
				best = fields[f].Lo
			}
		}
		return best
	}
	s := make([]int, n)
	for i := range s {
		s[i] = -1
	}
	for j := 0; j < n; j++ {
		if s[j] != -1 {
			continue
		}
		mn := -1
		for eip := range I[j] {
			if v := startOf(eip, j); v >= 0 && (mn < 0 || v < mn) {
				mn = v
			}
		}
		s[j] = mn
		cur := map[string]bool{}
		for eip := range I[j] {
			if startOf(eip, j) == s[j] {
				cur[eip] = true
			}
		}
		for i := j + 1; i < n; i++ {
			mn2 := -1
			for eip := range cur {
				if I[i][eip] {
					if v := startOf(eip, i); v >= 0 && (mn2 < 0 || v < mn2) {
						mn2 = v
					}
				}
			}
			if mn2 >= 0 {
				s[i] = mn2
			}
		}
	}
	e := make([]int, n)
	for j := 0; j < n-1; j++ {
		e[j] = s[j+1] - 1
	}
	last := 0
	for _, fs := range instField[n-1] {
		for f := range fs {
			if fields[f].Hi > last {
				last = fields[f].Hi
			}
		}
	}
	e[n-1] = last
	return boundaries{s, e}
}
