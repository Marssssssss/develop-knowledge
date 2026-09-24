// Lucene MaxScoreBulkScorer（BlockMaxWAND）的 Go 侧实现。
//
// 转写自 apache/lucene@main:
//   core/src/java/org/apache/lucene/search/MaxScoreBulkScorer.java
//   core/src/java/org/apache/lucene/search/MaxScoreCache.java
//   core/src/java/org/apache/lucene/search/ScorerUtil.java
//   core/src/java/org/apache/lucene/util/MathUtil.java
//
// 与 Python 侧同构：只建模决策（哪些子句 essential、候选能不能被剪、窗口多大），
// 不建模字节。
package main

import (
	"fmt"
	"math"
	"sort"
)

const (
	innerWindowSize = 1 << 12
	noMoreDocs      = 0x7FFFFFFF
	k1              = 1.2
	b22             = 0.75
)

// ulpF32 是 Math.ulp(float)：按 IEEE-754 binary32 的位模式加一。
// 直接用 float64 的 ulp 会小 9 个数量级，让 minRequiredScore 的收敛循环空转。
func ulpF32(x float32) float64 {
	a := float32(math.Abs(float64(x)))
	if a == 0 {
		return 1.401298464324817e-45
	}
	bits := math.Float32bits(a)
	return float64(math.Float32frombits(bits+1)) - float64(a)
}

func sumRelativeErrorBound(numValues int) float64 {
	if numValues <= 1 {
		return 0
	}
	u := math.Ldexp(1.0, -52)
	return float64(numValues-1) * u
}

// sumUpperBound：不超过 2 个值时浮点加法与顺序无关，故不放大。
func sumUpperBound(s float64, numValues int) float64 {
	if numValues <= 2 {
		return s
	}
	return (1.0 + 2*sumRelativeErrorBound(numValues)) * s
}

func minRequiredScore(maxRemaining float64, minCompetitive float64, numScorers int) float64 {
	mrs := minCompetitive - maxRemaining
	sub := ulpF32(float32(minCompetitive))
	for n := 0; mrs > 0 && float32(sumUpperBound(mrs+maxRemaining, numScorers)) >= float32(minCompetitive); n++ {
		mrs -= sub
		if n > 1000 {
			break
		}
	}
	return mrs
}

func filterCompetitiveHits(docs []int, scores []float64, maxRemaining float64, minCompetitive float64, numScorers int) ([]int, []float64) {
	need := minRequiredScore(maxRemaining, minCompetitive, numScorers)
	if need <= 0 {
		return docs, scores
	}
	od := make([]int, 0, len(docs))
	os := make([]float64, 0, len(scores))
	for i, s := range scores {
		if s >= need {
			od = append(od, docs[i])
			os = append(os, s)
		}
	}
	return od, os
}

func applyOptionalClause(docs []int, scores []float64, pos map[int]float64) []float64 {
	out := make([]float64, len(scores))
	for i, d := range docs {
		out[i] = scores[i] + pos[d]
	}
	return out
}

// applyRequiredClause：未命中的候选直接被剔除（缓冲区原地压缩）。
func applyRequiredClause(docs []int, scores []float64, pos map[int]float64) ([]int, []float64) {
	od := make([]int, 0, len(docs))
	os := make([]float64, 0, len(scores))
	for i, d := range docs {
		if v, ok := pos[d]; ok {
			od = append(od, d)
			os = append(os, scores[i]+v)
		}
	}
	return od, os
}

// partition 是 partitionScorers 的结果。
type partition struct {
	firstEssential int
	order          []int
	maxScoreSums   []float64
	firstRequired  int
}

// partitionScorers：按 maxWindowScore/cost 升序，把能塞进 minCompetitive 预算的
// 子句划为 non-essential（放在数组前段），其余划为 essential（从尾部往前填）。
func partitionScorers(maxWindowScores, costs []float64, order []int, minCompetitive float64) *partition {
	n := len(order)
	idx := append([]int(nil), order...)
	sort.SliceStable(idx, func(a, c int) bool {
		ra := maxWindowScores[idx[a]] / math.Max(1, costs[idx[a]])
		rc := maxWindowScores[idx[c]] / math.Max(1, costs[idx[c]])
		return ra < rc
	})
	p := &partition{maxScoreSums: make([]float64, n), firstRequired: n}
	sum := 0.0
	nonEss := make([]int, 0, n)
	ess := make([]int, 0, n)
	for _, i := range idx {
		next := sum + maxWindowScores[i]
		sFloat := float32(sumUpperBound(next, p.firstEssential+1))
		if sFloat < float32(minCompetitive) {
			sum = next
			p.maxScoreSums[p.firstEssential] = sum
			p.firstEssential++
			nonEss = append(nonEss, i)
		} else {
			ess = append(ess, i)
		}
	}
	if p.firstEssential == n {
		return nil // 整窗没有匹配
	}
	// essential 子句在源码里是从数组尾部往前填的，故要反转
	for i, j := 0, len(ess)-1; i < j; i, j = i+1, j-1 {
		ess[i], ess[j] = ess[j], ess[i]
	}
	p.order = append(nonEss, ess...)
	if p.firstEssential == n-1 {
		p.firstRequired = n - 1
		maxRequired := maxWindowScores[p.order[p.firstEssential]]
		for p.firstRequired > 0 {
			withoutPrev := maxRequired
			if p.firstRequired > 1 {
				withoutPrev += p.maxScoreSums[p.firstRequired-2]
			}
			if float32(withoutPrev) >= float32(minCompetitive) {
				break
			}
			p.firstRequired--
			maxRequired += maxWindowScores[p.order[p.firstRequired]]
		}
	}
	return p
}

// Clause：docs 升序，blocks[i] = (该 impact 块的 upTo, 该块的最大分数)。
type Clause struct {
	name   string
	docs   []int
	scores []float64
	cost   int
	blocks [][2]float64 // {upTo, maxScore}
	doc    int
}

func bm25(idf float64, freq, norm float64) float64 {
	return idf * freq / (freq + k1*((1-b22)+b22*norm))
}

func newClause(name string, docs []int, freqs, norms []float64, idf float64, block int) *Clause {
	c := &Clause{name: name, docs: docs, cost: len(docs), doc: -1}
	c.scores = make([]float64, len(docs))
	for i := range docs {
		c.scores[i] = bm25(idf, freqs[i], norms[i])
	}
	for s := 0; s < len(docs); s += block {
		e := s + block
		if e > len(docs) {
			e = len(docs)
		}
		m := 0.0
		for i := s; i < e; i++ {
			if c.scores[i] > m {
				m = c.scores[i]
			}
		}
		c.blocks = append(c.blocks, [2]float64{float64(docs[e-1]), m})
	}
	all := 0.0
	for _, s := range c.scores {
		if s > all {
			all = s
		}
	}
	c.blocks = append(c.blocks, [2]float64{float64(docs[len(docs)-1]), all})
	return c
}

// advanceShallow 返回覆盖 target 的块的 upTo，并记住块下标。
func (c *Clause) advanceShallow(target int) float64 {
	if float64(target) > c.blocks[len(c.blocks)-1][0] {
		return math.Inf(1)
	}
	for i, blk := range c.blocks {
		if blk[0] >= float64(target) {
			c.doc = i
			return blk[0]
		}
	}
	return math.Inf(1)
}

// getMaxScore 从「当前块」起找第一个覆盖 upTo 的层。
func (c *Clause) getMaxScore(upTo float64) float64 {
	for i := c.doc; i < len(c.blocks); i++ {
		if c.blocks[i][0] >= upTo {
			return c.blocks[i][1]
		}
	}
	return c.blocks[len(c.blocks)-1][1]
}

func (c *Clause) nextDocAtLeast(target int) int {
	for _, d := range c.docs {
		if d >= target {
			return d
		}
	}
	return noMoreDocs
}

func main() {
	fmt.Println("== 分句划分：预算内塞进尽可能多的 non-essential ==")
	mws := []float64{0.9, 1.4, 2.2, 3.0}
	costs := []float64{900, 700, 400, 200}
	for _, mc := range []float64{0, 1.5, 3.0, 5.0, 9.0} {
		p := partitionScorers(mws, costs, []int{0, 1, 2, 3}, mc)
		if p == nil {
			fmt.Printf("  minCompetitive=%.1f -> 整窗判空\n", mc)
			continue
		}
		fmt.Printf("  minCompetitive=%.1f -> %d 个 essential 顺序 %v firstRequired=%d\n",
			mc, 4-p.firstEssential, p.order, p.firstRequired)
	}

	fmt.Println("\n== 竞争分过滤：只减一个 float ulp ==")
	mc := float32(3.6706)
	fmt.Printf("  minCompetitive=%.7f float ulp=%.3e\n", mc, ulpF32(mc))
	for _, rem := range []float64{0, 1, 2, 4} {
		need := minRequiredScore(rem, float64(mc), 3)
		tag := ""
		if need <= 0 {
			tag = "（不过滤）"
		}
		fmt.Printf("  剩余上界 %.1f -> 本子句至少要 %.7f%s\n", rem, need, tag)
	}

	fmt.Println("\n== sumUpperBound：3 个以上子句才放大 ==")
	for _, n := range []int{1, 2, 3, 8} {
		fmt.Printf("  %d 个子句: %.17g\n", n, sumUpperBound(6.0, n))
	}

	fmt.Println("\n== 端到端：朴素 OR 与 BlockMaxWAND ==")
	cs := demoClauses()
	topNaive, naiveScored := naiveOr(cs, 10, 4000)
	topBm, bmScored, cand := blockMaxWand(cs, 10, 4000)
	fmt.Printf("  朴素全量评分 %d 篇\n", naiveScored)
	fmt.Printf("  BlockMaxWAND 候选 %d 篇、全量评分 %d 篇（%.1f%%）\n",
		cand, bmScored, 100*float64(bmScored)/float64(naiveScored))
	fmt.Printf("  朴素    top-5: %v\n", docList(topNaive))
	fmt.Printf("  MaxScore top-5: %v\n", docList(topBm))
}
