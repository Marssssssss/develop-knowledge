// bit 级字段切分：相邻 bit 的「联合取值基数 vs 各自基数之积」判据。
package main

import "math"

// nBits 本 demo 针对 16 位的标志字段（RFC 1035 §4.1.1 DNS 头）。
const nBits = 16

// bit：bit 0 是 16 位标志字段的最高位（网络序）。
func bit(msg, i int) int { return (msg >> (nBits - 1 - i)) & 1 }

func windowValue(msg int, idxs []int) int {
	v := 0
	for _, i := range idxs {
		v = v<<1 | bit(msg, i)
	}
	return v
}

// cardinality 该 bit 窗口在语料里观测到的联合取值个数。
func cardinality(msgs []int, idxs []int) int {
	seen := map[int]bool{}
	for _, m := range msgs {
		seen[windowValue(m, idxs)] = true
	}
	return len(seen)
}

func entropyBits(msgs []int, idxs []int) float64 {
	cnt := map[int]int{}
	for _, m := range msgs {
		cnt[windowValue(m, idxs)]++
	}
	n := float64(len(msgs))
	e := 0.0
	for _, v := range cnt {
		p := float64(v) / n
		e -= p * math.Log2(p)
	}
	return e
}

func varyingBits(msgs []int) []int {
	out := []int{}
	for i := 0; i < nBits; i++ {
		if cardinality(msgs, []int{i}) > 1 {
			out = append(out, i)
		}
	}
	return out
}

type run struct{ lo, hi int }

// constantRuns 把常量位聚成连续段。
func constantRuns(constSet map[int]bool) []run {
	out := []run{}
	curLo, in := -1, false
	for i := 0; i < nBits; i++ {
		if constSet[i] {
			if !in {
				curLo, in = i, true
			}
		} else if in {
			out = append(out, run{curLo, i})
			in = false
		}
	}
	if in {
		out = append(out, run{curLo, nBits})
	}
	return out
}

// segment 返回 (变量位块, 常量位段)。
// 常量位无法由统计判据归属，单独返回 —— 这是 bit 级分析的真实能力边界。
func segment(msgs []int) ([]run, []run) {
	vb := varyingBits(msgs)
	if len(vb) == 0 {
		all := map[int]bool{}
		for i := 0; i < nBits; i++ {
			all[i] = true
		}
		return nil, constantRuns(all)
	}
	blocks := []run{}
	cur := []int{vb[0]}
	for _, b := range vb[1:] {
		ca := cardinality(msgs, cur)
		cb := cardinality(msgs, []int{b})
		if cardinality(msgs, append(append([]int{}, cur...), b)) < ca*cb {
			cur = append(cur, b) // 相关 → 同一字段
		} else {
			blocks = append(blocks, run{cur[0], cur[len(cur)-1] + 1}) // 独立 → 边界
			cur = []int{b}
		}
	}
	blocks = append(blocks, run{cur[0], cur[len(cur)-1] + 1})
	cs := map[int]bool{}
	for i := 0; i < nBits; i++ {
		cs[i] = true
	}
	for _, i := range vb {
		cs[i] = false
	}
	return blocks, constantRuns(cs)
}

// byteView 字节粒度对照视图：每 8 位一个「字段」。
func byteView(msgs []int) []int {
	out := []int{}
	for s := 0; s < nBits; s += 8 {
		end := s + 8
		if end > nBits {
			end = nBits
		}
		idxs := []int{}
		for i := s; i < end; i++ {
			idxs = append(idxs, i)
		}
		out = append(out, cardinality(msgs, idxs))
	}
	return out
}
