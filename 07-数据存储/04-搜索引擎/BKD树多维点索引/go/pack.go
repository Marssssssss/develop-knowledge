package main

// PackedCode 对应 recursePackIndex 写进去的那个 vInt 及其后缀字节。
type PackedCode struct {
	Prefix   int
	Delta    int
	SplitDim int
	Code     int
	Suffix   []int
}

// packIndex：BKDWriter.recursePackIndex。
// negativeDeltas 在左孩子无条件置 true、右孩子无条件置 false —— 沿路径同一维度的
// split 值单调（左递减、右递增），取反后 delta 恒为正（源码 assert > 0）。
func packIndex(root *Node, cfg *BKDConfig) []PackedCode {
	bpd := cfg.BytesPerDim
	last := make([]int, cfg.NumIndexDims*bpd)
	out := []PackedCode{}
	var walk func(n *Node, neg []bool)
	walk = func(n *Node, neg []bool) {
		if n.IsLeaf {
			return
		}
		dim := n.SplitDim
		base := dim * bpd
		cur := toBytes(n.SplitVal, bpd)
		prev := last[base : base+bpd]
		prefix := commonPrefixLength(n.SplitVal, fromBytes(prev), bpd)
		delta := 0
		var suffix []int
		if prefix < bpd {
			delta = cur[prefix] - prev[prefix]
			if neg[dim] {
				delta = -delta
			}
			if delta <= 0 {
				panic("源码 assert firstDiffByteDelta > 0")
			}
			suffix = append([]int(nil), cur[prefix+1:]...)
		}
		code := (delta*(1+bpd) + prefix) * cfg.NumIndexDims + dim
		out = append(out, PackedCode{prefix, delta, dim, code, suffix})
		saved := append([]int(nil), prev...)
		copy(last[base:base+bpd], cur)

		nl := append([]bool(nil), neg...)
		nl[dim] = true
		walk(n.Left, nl)
		nr := append([]bool(nil), neg...)
		nr[dim] = false
		walk(n.Right, nr)
		// 两个孩子都看过之后才还原
		copy(last[base:base+bpd], saved)
	}
	walk(root, make([]bool, cfg.NumIndexDims))
	return out
}

// decodePacked：按 BKDReader.readNodeData 的语义把 code 流还原成 splitValue 序列。
func decodePacked(root *Node, codes []PackedCode, cfg *BKDConfig) []int {
	bpd := cfg.BytesPerDim
	out := []int{}
	pos := 0
	var walk func(n *Node, last []int, neg []bool)
	walk = func(n *Node, last []int, neg []bool) {
		if n.IsLeaf {
			return
		}
		c := codes[pos]
		pos++
		dim := c.Code % cfg.NumIndexDims
		rest := c.Code / cfg.NumIndexDims
		prefix := rest % (1 + bpd)
		delta := rest / (1 + bpd)
		if dim != n.SplitDim || prefix != c.Prefix || delta != c.Delta {
			panic("code 解码与预期不符")
		}
		v := last[dim]
		if prefix < bpd {
			d := delta
			if neg[dim] {
				d = -delta
			}
			bs := toBytes(last[dim], bpd)
			bs[prefix] = (bs[prefix] + d) & 0xFF
			for j, b := range c.Suffix {
				bs[prefix+1+j] = b
			}
			v = fromBytes(bs)
		}
		out = append(out, v)
		nl := append([]int(nil), last...)
		nl[dim] = v
		a := append([]bool(nil), neg...)
		a[dim] = true
		walk(n.Left, nl, a)
		b := append([]bool(nil), neg...)
		b[dim] = false
		walk(n.Right, nl, b)
	}
	walk(root, make([]int, cfg.NumIndexDims), make([]bool, cfg.NumIndexDims))
	if pos != len(codes) {
		panic("code 数与内节点数不符")
	}
	return out
}
