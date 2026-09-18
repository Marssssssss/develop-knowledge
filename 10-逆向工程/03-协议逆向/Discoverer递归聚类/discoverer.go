// Discoverer（USENIX Security 2007）三阶段最小复现：标识化 / 递归聚类 / 基于类型的序列比对合并。
// 用法: go run discoverer.go   (自检失败即 panic)
package main

const (
	maxPrefix     = 2048 // §4.3 Table 3: Maximum message prefix
	textMin       = 3    // §4.3 Table 3: Minimum length of text segments
	minCluster    = 2    // §4.3 Table 3 原文 20 messages，demo 规模小改为 2
	fdMaxDistinct = 8    // §4.3 正文提到但 Table 3 未列数值 → demo 自选
	nwMatch       = 1
	nwMismatch    = 0
	nwGap         = -2
	neg           = -1000000
	clsB          = "B"
	clsT          = "T"
)

type tok struct {
	cls string
	val []byte
	off int
}

type spec struct {
	cls    string
	sem    string // "" | "length" | "offset"
	isConst bool
	vals   [][]byte
	size   int
}

func printable(b byte) bool { return b >= 0x20 && b <= 0x7e }

func isDelim(b byte) bool { return b == 0x20 || b == 0x09 }

// findUTF16LE 返回 UTF-16LE 文本段（论文："We also look for Unicode encodings in messages."）。
func findUTF16LE(m []byte) [][2]int {
	var segs [][2]int
	for i := 0; i < len(m); {
		if printable(m[i]) && i+1 < len(m) && m[i+1] == 0x00 {
			j := i
			for j+1 < len(m) && printable(m[j]) && m[j+1] == 0x00 {
				j += 2
			}
			if j-i >= 4 {
				segs = append(segs, [2]int{i, j})
			}
			i = j
		} else {
			i++
		}
	}
	return segs
}

func splitDelims(run []byte) [][]byte {
	var parts [][]byte
	cur := []byte{}
	for _, b := range run {
		if isDelim(b) {
			if len(cur) > 0 {
				parts = append(parts, cur)
				cur = []byte{}
			}
		} else {
			cur = append(cur, b)
		}
	}
	if len(cur) > 0 {
		parts = append(parts, cur)
	}
	return parts
}

// tokenize §3.2.1 标识化。
func tokenize(msg []byte) []tok {
	if len(msg) > maxPrefix {
		msg = msg[:maxPrefix]
	}
	uni := findUTF16LE(msg)
	upos := 0
	out := []tok{}
	for i := 0; i < len(msg); {
		if upos < len(uni) && uni[upos][0] <= i && i < uni[upos][1] {
			s, e := uni[upos][0], uni[upos][1]
			out = append(out, tok{clsT, append([]byte{}, msg[s:e]...), s})
			i, upos = e, upos+1
			continue
		}
		if printable(msg[i]) {
			j := i
			for j < len(msg) && printable(msg[j]) &&
				!(upos < len(uni) && uni[upos][0] <= j && j < uni[upos][1]) {
				j++
			}
			run := msg[i:j]
			if len(run) >= textMin {
				off := i
				for _, p := range splitDelims(run) {
					out = append(out, tok{clsT, append([]byte{}, p...), off})
					off += len(p) + 1
				}
			} else {
				for k, b := range run {
					out = append(out, tok{clsB, []byte{b}, i + k})
				}
			}
			i = j
		} else {
			out = append(out, tok{clsB, []byte{msg[i]}, i})
			i++
		}
	}
	return out
}

func tokenPattern(dir string, ts []tok) string {
	p := dir
	for _, t := range ts {
		p += t.cls
	}
	return p
}

func beInt(bs [][]byte) int {
	v := 0
	for _, b := range bs {
		v = v<<8 | int(b[0])
	}
	return v
}

// detect 返回 (semantic, width)，复现 §3.3.1 的 length / offset 启发式。
func detect(toks [][]tok, msgs [][]byte, ntok, k int) (string, int) {
	mx := 4
	if ntok-k < mx {
		mx = ntok - k
	}
	for w := 1; w <= mx; w++ {
		allB := true
		for d := 0; d < w; d++ {
			if toks[0][k+d].cls != clsB {
				allB = false
			}
		}
		if !allB {
			continue
		}
		vals := make([]int, len(toks))
		uniq := map[int]bool{}
		for ti := range toks {
			var group [][]byte
			for d := 0; d < w; d++ {
				group = append(group, toks[ti][k+d].val)
			}
			vals[ti] = beInt(group)
			uniq[vals[ti]] = true
		}
		if len(uniq) == 1 {
			continue
		}
		allEq := func(f func(a, b int) int, target func(a, b int) int) bool {
			for a := range vals {
				for b := range vals {
					if f(a, b) != target(a, b) {
						return false
					}
				}
			}
			return true
		}
		if allEq(func(a, b int) int { return vals[a] - vals[b] },
			func(a, b int) int { return len(msgs[a]) - len(msgs[b]) }) {
			return "length", w
		}
		for t := k + w; t < ntok; t++ {
			if allEq(func(a, b int) int { return vals[a] - vals[b] },
				func(a, b int) int { return len(toks[a][t].val) - len(toks[b][t].val) }) {
				return "length", w
			}
			if allEq(func(a, b int) int { return vals[a] - vals[b] },
				func(a, b int) int { return toks[a][t].off - toks[b][t].off }) {
				return "offset", w
			}
		}
	}
	return "", 1
}

func inferFormat(msgs [][]byte) []spec {
	toks := make([][]tok, len(msgs))
	for i, m := range msgs {
		toks[i] = tokenize(m)
	}
	ntok := len(toks[0])
	var fmt_ []spec
	for k := 0; k < ntok; {
		cls := toks[0][k].cls
		sem, w := "", 1
		if cls == clsB {
			sem, w = detect(toks, msgs, ntok, k)
		}
		for d := 0; d < w; d++ {
			var vs [][]byte
			for ti := range toks {
				vs = append(vs, toks[ti][k+d].val)
			}
			fmt_ = append(fmt_, spec{cls, sem, uniq1(vs), vs, len(vs[0])})
		}
		k += w
	}
	return fmt_
}

func uniq1(vs [][]byte) bool {
	for i := 1; i < len(vs); i++ {
		if string(vs[i]) != string(vs[0]) {
			return false
		}
	}
	return true
}

func intersect(a, b [][]byte) bool {
	for _, x := range a {
		for _, y := range b {
			if string(x) == string(y) {
				return true
			}
		}
	}
	return false
}

// tokMatch §3.3.2 保守匹配策略。
func tokMatch(a, b spec) bool {
	if a.sem != "" && b.sem != "" {
		return a.sem == b.sem
	}
	switch {
	case a.isConst && b.isConst:
		return len(a.vals) == 1 && len(b.vals) == 1 && string(a.vals[0]) == string(b.vals[0])
	case a.isConst || b.isConst:
		c, v := a, b
		if b.isConst {
			c, v = b, a
		}
		return intersect(c.vals, v.vals)
	default:
		return intersect(a.vals, b.vals)
	}
}
