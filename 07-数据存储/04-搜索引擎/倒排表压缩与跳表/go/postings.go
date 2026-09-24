// Lucene 倒排表压缩与跳表 —— Go 侧实现（PForUtil 的 token/例外语义 + 跳表层数）。
//
// 转写自 apache/lucene@main:
//   core/src/java/org/apache/lucene/codecs/lucene104/PForUtil.java
//   core/src/java/org/apache/lucene/codecs/lucene104/ForUtil.java
//   core/src/java/org/apache/lucene/codecs/MultiLevelSkipListWriter.java
//
// 与 Python 侧的分工：本文件聚焦「块怎么被切成 token + 定长区 + patch 区」以及
// 跳表层数的推导；ForUtil 的 SIMD 泳道位打包（collapse8/16 与放置图）见 python/forutil.py。
package main

import "fmt"

const (
	blockSize      = 256
	blockSizeLog2  = 8
	maxExceptions  = 7
	bitsPerByte    = 8
	defaultBuckets = 10
)

// bitsRequired 等价 PackedInts.bitsRequired：0 需要 0 位。
func bitsRequired(v int) int {
	if v < 0 {
		panic("bitsRequired 只接受非负数")
	}
	n := 0
	for v > 0 {
		n++
		v >>= 1
	}
	return n
}

func allEqual(l []int) bool {
	for i := 1; i < len(l); i++ {
		if l[i] != l[0] {
			return false
		}
	}
	return true
}

// numBytes 是 ForUtil.numBytes：256 个 bpv 位的值占多少字节。
func numBytes(bpv int) int {
	return bpv << (blockSizeLog2 - 3)
}

func primitiveSizeOf(bpv int) int {
	if bpv <= 8 {
		return 8
	}
	if bpv <= 16 {
		return 16
	}
	return 32
}

// patchLayout 复刻 PForUtil.encode 的位数试探，返回块的结构参数。
type patchLayout struct {
	maxBits       int
	patchedBits   int
	numExceptions int
	maxUnpatched  int
	allEqualAfter bool
	histogram     [32]int
}

func planPFor(ints []int) patchLayout {
	var pl patchLayout
	for i := 0; i < blockSize; i++ {
		b := bitsRequired(ints[i])
		pl.histogram[b]++
		if b > pl.maxBits {
			pl.maxBits = b
		}
	}
	// patch 只占 1 字节，故位数最多下调 8
	minBits := pl.maxBits - bitsPerByte
	if minBits < 0 {
		minBits = 0
	}
	cumulative := 0
	pl.patchedBits = pl.maxBits
	for b := pl.maxBits; b >= minBits; b-- {
		if cumulative > maxExceptions {
			break
		}
		pl.patchedBits = b
		pl.numExceptions = cumulative
		cumulative += pl.histogram[b]
	}
	pl.maxUnpatched = (1 << uint(pl.patchedBits)) - 1
	pl.allEqualAfter = pl.maxBits <= bitsPerByte
	return pl
}

// blockLen 给出一个块编码后的字节数（token + 定长区或 vInt + patch 区）。
func blockLen(pl patchLayout, allEq bool) int {
	body := numBytes(pl.patchedBits)
	if allEq && pl.maxBits <= bitsPerByte {
		body = vIntLen(pl.maxUnpatched)
	}
	return 1 + body + 2*pl.numExceptions
}

func vIntLen(v int) int {
	n := 1
	for v >>= 7; v > 0; v >>= 7 {
		n++
	}
	return n
}

// Lucene104PostingsFormat 自己做的两级跳表：每 32 个 256-块一条 level1 datum。
const (
	level1Factor   = 32
	level1NumDocs  = level1Factor * blockSize // 8192
	level1Mask     = level1NumDocs - 1
)

func level1GroupOf(doc int) int { return doc / level1NumDocs }

func level1OffsetInGroup(doc int) int { return doc & level1Mask }

// skipLevels 是 MultiLevelSkipListWriter 的层数推导。
func skipLevels(df, skipInterval, skipMultiplier, maxSkipLevels int) int {
	if df <= skipInterval {
		return 1
	}
	return min(1+utilLog(df/skipInterval, skipMultiplier), maxSkipLevels)
}

// utilLog 是 MathUtil.log：满足 base^ret <= x 的最大 ret。
func utilLog(x, base int) int {
	ret := 0
	cur := 1
	for base*cur <= x {
		cur *= base
		ret++
	}
	return ret
}

// bufferSkipLevels 是 bufferSkip：这一条 datum 会写进哪几层。
func bufferSkipLevels(df, skipInterval, skipMultiplier, numLevels int) []int {
	windowLength := skipInterval * skipMultiplier
	levels := []int{0}
	n := 1
	d := df
	if d%windowLength == 0 {
		n++
		d /= windowLength
		for d%skipMultiplier == 0 && n < numLevels {
			n++
			d /= skipMultiplier
		}
	}
	for i := 1; i < n; i++ {
		levels = append(levels, i)
	}
	return levels
}

// entriesPerLevel：层级 i 的条目数 = floor(df / (skipInterval * skipMultiplier^i))。
func entriesPerLevel(df, skipInterval, skipMultiplier, level int) int {
	div := skipInterval
	for i := 0; i < level; i++ {
		div *= skipMultiplier
	}
	return df / div
}

// skipTo 复刻 MultiLevelSkipListReader.skipTo 的「先上爬」阶段。
func skipTo(target int, levelDocs []int) int {
	level := 0
	for level < len(levelDocs)-1 && target > levelDocs[level+1] {
		level++
	}
	return level
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func main() {
	fmt.Println("== PForUtil 位数试探 ==")
	cases := []struct {
		desc string
		blk  []int
	}{
		{"256 个 1", repeat(1, blockSize)},
		{"255 个 1 加 1 个 300", withTail(1, 255, 300)},
		{"250 个 1 加 6 个 5", withTailN(1, 250, 5, 6)},
		{"255 个 7 加 1 个 4000", withTail(7, 255, 4000)},
	}
	for _, c := range cases {
		pl := planPFor(c.blk)
		fmt.Printf("  %-20s max=%2d -> %2d 位, 例外 %d, 块长 %4d\n",
			c.desc, pl.maxBits, pl.patchedBits, pl.numExceptions,
			blockLen(pl, allEqualMasked(c.blk, pl.maxUnpatched)))
	}

	fmt.Println("\n== ForUtil.numBytes（只由位数决定）==")
	for _, bpv := range []int{1, 3, 8, 16, 17, 32} {
		fmt.Printf("  bpv=%2d primitiveSize=%2d -> %4d 字节\n",
			bpv, primitiveSizeOf(bpv), numBytes(bpv))
	}

	fmt.Println("\n== Lucene104 的两级跳表（LEVEL1_FACTOR=32）==")
	for _, doc := range []int{0, 8191, 8192, 123456} {
		fmt.Printf("  doc=%6d -> level1 组 %d, 组内偏移 %d\n",
			doc, level1GroupOf(doc), level1OffsetInGroup(doc))
	}

	fmt.Println("\n== 通用 MultiLevelSkipList 的层数（举例 skipInterval=128, skipMultiplier=8）==")
	for _, df := range []int{100, 128, 1000, 10000, 100000} {
		fmt.Printf("  df=%7d -> %d 层\n", df, skipLevels(df, 128, 8, 10))
	}

	fmt.Println("\n== bufferSkip 的层级分配 ==")
	for _, df := range []int{128, 1024, 8192, 65536} {
		fmt.Printf("  df=%6d -> %v\n", df, bufferSkipLevels(df, 128, 8, 4))
	}

	fmt.Println("\n== 各层条目数（df=100000）==")
	for lvl := 0; lvl < 4; lvl++ {
		fmt.Printf("  level %d -> %d 条\n", lvl, entriesPerLevel(100000, 128, 8, lvl))
	}

	fmt.Println("\n== skipTo 上爬 ==")
	lvlDocs := []int{128, 1024, 8192}
	for _, t := range []int{500, 2000, 10000} {
		fmt.Printf("  target=%6d -> level %d (datum %d)\n",
			t, skipTo(t, lvlDocs), lvlDocs[skipTo(t, lvlDocs)])
	}
}

func repeat(v, n int) []int {
	out := make([]int, n)
	for i := range out {
		out[i] = v
	}
	return out
}

func withTail(v, n, tail int) []int {
	out := repeat(v, n)
	return append(out, tail)
}

func withTailN(v, n, tail, m int) []int {
	out := repeat(v, n)
	for i := 0; i < m; i++ {
		out = append(out, tail)
	}
	return out
}

// allEqualMasked 是 PForUtil 里那个容易看漏的顺序：先把例外 `&= maxUnpatched`
// 写回原数组，**再**判整块是否全等 —— 所以例外也参与 allEqual 的判断。
func allEqualMasked(blk []int, mask int) bool {
	if len(blk) == 0 {
		return true
	}
	first := blk[0] & mask
	for _, v := range blk {
		if v&mask != first {
			return false
		}
	}
	return true
}
