// FST 与 completion suggester：权重编码、payload 切分、top-N 队列容量启发式。
//
// 转写自 apache/lucene@main
//   lucene/suggest/src/java/org/apache/lucene/search/suggest/document/NRTSuggester.java
//   lucene/analysis/common/.../miscellaneous/ConcatenateGraphFilter.java
//   lucene/core/src/java/org/apache/lucene/analysis/TokenStreamToAutomaton.java
//   lucene/core/src/java/org/apache/lucene/util/automaton/Operations.java
//
// 语言差异（已在代码中显式落地）：
//   * Java 的 int 是 32 位有符号，Go 侧统一用 int 并在 encode 上显式比较 INT_MAX；
//   * Java 的 UnsupportedOperationException / assert 在 Go 里用 panic 对应；
//   * vInt 的无符号右移用 Go 的 >> 配合非负前提（源码也只写非负 docId）。
//
// 建模口径：真实 FST 是带后缀共享的最小化 DAG，这里用前缀 trie 代替；
// 所有被断言的性质只依赖「路径输出可累加」，与是否最小化无关。
package main

import (
	"fmt"
	"math"
	"sort"
)

const (
	intMax                 = int(^uint32(0) >> 1) // Integer.MAX_VALUE
	maxTopNQueueSize       = 5000
	defaultPayloadSep      = 0x1F
	posSep                 = 0x001F // TokenStreamToAutomaton.POS_SEP
	hole                   = 0x001E // TokenStreamToAutomaton.HOLE
	defaultGraphExpansions = 10000  // Operations.DEFAULT_DETERMINIZE_WORK_LIMIT
)

// ------------------------------------------------------------------ 权重编码

// Encode 权重 → FST 里的 output1。越大越"小"，这样升序 top-N 先取到重权重。
func Encode(weight int) int64 {
	if weight < 0 || int64(weight) > int64(intMax) {
		panic(fmt.Sprintf("cannot encode value: %d", weight))
	}
	return int64(intMax) - int64(weight)
}

// Decode decode(output) = Integer.MAX_VALUE - output
func Decode(output int64) int64 {
	if output < 0 || output > int64(intMax) {
		panic(fmt.Sprintf("decoded output: %d is not within 0 and Integer.MAX_VALUE", output))
	}
	return int64(intMax) - output
}

// ------------------------------------------------------------------ vInt

// WriteVInt 每字节 7 位，高位为 continuation，最多 5 字节。
func WriteVInt(i int) []byte {
	if i < 0 {
		panic("vInt 不能编码负数")
	}
	out := []byte{}
	for (i & ^0x7F) != 0 {
		out = append(out, byte((i&0x7F)|0x80))
		i >>= 7
	}
	return append(out, byte(i))
}

// ReadVInt 返回 (值, 新位置)；第 5 字节仍有 continuation 位则 panic（Invalid vInt）。
func ReadVInt(buf []byte, pos int) (int, int) {
	b := buf[pos]
	pos++
	val := int(b & 0x7F)
	shift := 7
	nbytes := 1
	for (b & 0x80) != 0 {
		if nbytes == 5 {
			panic("Invalid vInt (too long)")
		}
		b = buf[pos]
		pos++
		nbytes++
		val |= int(b&0x7F) << shift
		shift += 7
	}
	return val, pos
}

// ------------------------------------------------------------------ payload

// PayloadProcessor payload = surface + PAYLOAD_SEP + vint(docID)。
type PayloadProcessor struct {
	Sep byte
}

// MaxDocIDLenWithSep vint 最多 5 字节 + 1 字节分隔符。
const MaxDocIDLenWithSep = 6

// ParseSurfaceForm 找**第一个** payloadSep，它之前是 surface form。
func (p PayloadProcessor) ParseSurfaceForm(output []byte) ([]byte, int) {
	idx := -1
	for i, b := range output {
		if b == p.Sep {
			idx = i
			break
		}
	}
	if idx == -1 {
		panic("no payloadSep found, unable to determine surface form")
	}
	return output[:idx], idx
}

// Make 拼出 surface + sep + vint(docID)。
func (p PayloadProcessor) Make(surface []byte, docID int) []byte {
	out := append([]byte{}, surface...)
	out = append(out, p.Sep)
	return append(out, WriteVInt(docID)...)
}

// ------------------------------------------------- 队列容量 / liveDocs 启发式

// CalculateLiveDocRatio numDocs / maxDocs；numDocs == 0 → -1（lookup 直接 return）。
func CalculateLiveDocRatio(numDocs, maxDocs int) float64 {
	if numDocs == 0 {
		return -1
	}
	return float64(numDocs) / float64(maxDocs)
}

// GetMaxTopNQueueSize 源码注释自称 "simple heuristics"，且**不保证** admissibility。
//
//	maxQueueSize = topN * maxAnalyzedPathsPerOutput / liveDocsRatio
//	if (filterEnabled) maxQueueSize += numDocs / 2
//	return min(MAX_TOP_N_QUEUE_SIZE, maxQueueSize)
func GetMaxTopNQueueSize(topN, numDocs int, liveDocsRatio float64,
	filterEnabled bool, maxAnalyzedPathsPerOutput int) int {
	if liveDocsRatio > 1.0 {
		panic("liveDocRatio can be at most 1.0")
	}
	size := float64(topN*maxAnalyzedPathsPerOutput) / liveDocsRatio
	if filterEnabled {
		size += float64(numDocs) / 2
	}
	return int(math.Min(maxTopNQueueSize, size))
}

