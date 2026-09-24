package main

import (
	"fmt"
	"math/bits"
)

const (
	maxDims                    = 16
	maxIndexDims               = 8
	defaultMaxPointsInLeafNode = 512
	splitsBeforeExactBounds    = 4
)

// BKDConfig 对应 org.apache.lucene.util.bkd.BKDConfig。
type BKDConfig struct {
	NumDims             int
	NumIndexDims        int
	BytesPerDim         int
	MaxPointsInLeafNode int
}

var defaultConfigs = [][3]int{
	{1, 1, 2}, {1, 1, 4}, {1, 1, 8}, {1, 1, 16},
	{2, 2, 2}, {2, 2, 4}, {2, 2, 8}, {2, 2, 16},
	{7, 4, 4},
}

func newBKDConfig(numDims, numIndexDims, bytesPerDim, maxPoints int) (*BKDConfig, error) {
	if numDims < 1 || numDims > maxDims {
		return nil, fmt.Errorf("numDims must be 1 .. %d (got: %d)", maxDims, numDims)
	}
	if numIndexDims < 1 || numIndexDims > maxIndexDims {
		return nil, fmt.Errorf("numIndexDims must be 1 .. %d (got: %d)", maxIndexDims, numIndexDims)
	}
	if numIndexDims > numDims {
		return nil, fmt.Errorf("numIndexDims cannot exceed numDims")
	}
	if bytesPerDim <= 0 {
		return nil, fmt.Errorf("bytesPerDim must be > 0")
	}
	if maxPoints <= 0 {
		return nil, fmt.Errorf("maxPointsInLeafNode must be > 0")
	}
	return &BKDConfig{numDims, numIndexDims, bytesPerDim, maxPoints}, nil
}

func (c *BKDConfig) packedBytesLength() int      { return c.NumDims * c.BytesPerDim }
func (c *BKDConfig) packedIndexBytesLength() int { return c.NumIndexDims * c.BytesPerDim }
func (c *BKDConfig) bytesPerDoc() int            { return c.packedBytesLength() + 4 }

func (c *BKDConfig) isDefault() bool {
	for _, d := range defaultConfigs {
		if d[0] == c.NumDims && d[1] == c.NumIndexDims && d[2] == c.BytesPerDim {
			return true
		}
	}
	return false
}

// ---------------------------------------------------------------- 字节与形状

func byteAt(v, bpd, idx int) int { return (v >> (8 * (bpd - 1 - idx))) & 0xFF }

func toBytes(v, bpd int) []int {
	out := make([]int, bpd)
	for i := range out {
		out[i] = byteAt(v, bpd, i)
	}
	return out
}

func fromBytes(bs []int) int {
	v := 0
	for _, b := range bs {
		v = (v << 8) | b
	}
	return v
}

// commonPrefixLength：BKDUtil 的逐字节版本（4/8 字节时源码走 numberOfLeadingZeros
// 的快路径，语义一致）。
func commonPrefixLength(a, b, bpd int) int {
	i := 0
	for i < bpd && byteAt(a, bpd, i) == byteAt(b, bpd, i) {
		i++
	}
	return i
}

func numLeavesOf(pointCount, maxPoints int) int {
	return (pointCount + maxPoints - 1) / maxPoints
}

func nlz(x int) int {
	if x <= 0 {
		return 32
	}
	return bits.LeadingZeros32(uint32(x))
}

// getNumLeftLeafNodes：把叶子尽量铺成满二叉树，左半最多不超过右半的 2 倍。
func getNumLeftLeafNodes(numLeaves int) int {
	if numLeaves <= 1 {
		panic("numLeaves must be > 1")
	}
	lastFullLevel := 31 - nlz(numLeaves)
	leavesFullLevel := 1 << lastFullLevel
	numLeft := leavesFullLevel / 2
	unbalanced := numLeaves - leavesFullLevel
	numLeft += min(unbalanced, numLeft)
	if numLeft < numLeaves-numLeft || numLeft > 2*(numLeaves-numLeft) {
		panic("getNumLeftLeafNodes invariant broken")
	}
	return numLeft
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// chooseSplitDim：先看「2 倍」规则，再挑跨度最大的维度。
func chooseSplitDim(mins, maxs, parentSplits []int, numIndexDims int) int {
	maxNumSplits := 0
	for _, s := range parentSplits {
		if s > maxNumSplits {
			maxNumSplits = s
		}
	}
	for dim := 0; dim < numIndexDims; dim++ {
		if parentSplits[dim] < maxNumSplits/2 && mins[dim] != maxs[dim] {
			return dim
		}
	}
	splitDim := -1
	bestSpan := 0
	for dim := 0; dim < numIndexDims; dim++ {
		span := maxs[dim] - mins[dim]
		if splitDim == -1 || span > bestSpan {
			bestSpan = span
			splitDim = dim
		}
	}
	return splitDim
}

// needsExactBounds：只在「非根 + 维度 > 2 + 切分次数是 4 的倍数」时重算包围盒。
func needsExactBounds(numLeaves, totalLeaves, numIndexDims int, parentSplits []int) bool {
	if numLeaves == totalLeaves || numIndexDims <= 2 {
		return false
	}
	sum := 0
	for _, s := range parentSplits {
		sum += s
	}
	return sum%splitsBeforeExactBounds == 0
}
