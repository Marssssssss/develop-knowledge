package main

// 混合检索与结果融合 —— BM25（Lucene 官方实现）与 RRF（Cormack 2009 原论文）。
//
// 依据（本轮实读）：
//   - Lucene BM25Similarity.java：idf = log(1 + (N-n+0.5)/(n+0.5))；
//     avgdl = sumTotalTermFreq/docCount；tf = freq/(freq + k1*((1-b)+b*dl/avgdl))，
//     **分子没有 (k1+1)**；score 的单调改写 weight - weight/(1+freq*normInverse)；
//     默认 k1=1.2、b=0.75、k3=-1（禁用）；doc 长度被压缩成 1 字节（256 档）
//   - Cormack/Clarke/Büttcher SIGIR 2009：RRFscore(d) = Σ 1/(k + r(d))，**k = 60**，
//     「fixed during a pilot investigation and not altered during subsequent validation」；
//     CombMNZ = |{r : r(d) ≤ c}| × Σ_{r: r(d) ≤ c} s_r(d)

import (
	"fmt"
	"math"
	"sort"
)

// Lucene 默认参数。
const (
	LuceneK1              = 1.2
	LuceneB               = 0.75
	LuceneK3              = -1.0
	LengthTableSize       = 256
	LengthExactUpTo       = 39
	RRFK                  = 60
)

// Bm25IDF 对应 `idf(df, docCount) = log(1 + (docCount - df + 0.5)/(df + 0.5))`。
// 分子恒为正 ⇒ Lucene 的 idf **不会是负数**（与教科书 log(N/df) 不同）。
func Bm25IDF(df, docCount int) float64 {
	return math.Log(1.0 + (float64(docCount)-float64(df)+0.5)/(float64(df)+0.5))
}

// Bm25Avgdl 对应 avgFieldLength：sumTotalTermFreq / docCount（不是 numDocs）。
func Bm25Avgdl(sumTotalTermFreq, docCount int) float64 {
	if docCount == 0 {
		panic("docCount must be > 0")
	}
	return float64(sumTotalTermFreq) / float64(docCount)
}

// Bm25NormInverse 对应 cache[i] = 1/(k1 * ((1-b) + b*dl/avgdl))。
func Bm25NormInverse(k1, b, dl, avgdl float64) float64 {
	return 1.0 / (k1 * ((1.0 - b) + b*dl/avgdl))
}

// Bm25Tf 对应 Lucene 的 tf：freq / (freq + k1*((1-b) + b*dl/avgdl))。
func Bm25Tf(freq float64, k1, b, dl, avgdl float64) float64 {
	if freq == 0 {
		return 0.0
	}
	return freq / (freq + k1*((1.0-b)+b*dl/avgdl))
}

// Bm25Score 经典写法：boost * idf * tf。
func Bm25Score(freq float64, df, docCount int, avgdl, dl, k1, b, boost float64) float64 {
	return boost * Bm25IDF(df, docCount) * Bm25Tf(freq, k1, b, dl, avgdl)
}

// Bm25ScoreMonotone 对应 doScore：weight - weight/(1 + freq*normInverse)。
// 官方注释：这样改写是为了在不提升到 double 的前提下保证对 freq 与 norm 都单调。
func Bm25ScoreMonotone(freq float64, df, docCount int, avgdl, dl, k1, b, boost float64) float64 {
	weight := boost * Bm25IDF(df, docCount)
	return weight - weight/(1.0+freq*Bm25NormInverse(k1, b, dl, avgdl))
}

// Bm25QueryTermWeight 对应 k3 饱和：k3 < 0 时线性，否则 ((k3+1)*qtf)/(k3+qtf)。
func Bm25QueryTermWeight(qtf, k3 float64) float64 {
	if k3 < 0 {
		return qtf
	}
	return ((k3 + 1.0) * qtf) / (k3 + qtf)
}

// SmallFloatByte4ToInt 对应 SmallFloat.byte4ToInt：1 字节 4 位尾数的小浮点。
func SmallFloatByte4ToInt(b int) int {
	mantissa := b & 0x07
	exponent := (b >> 3) & 0x1F
	if exponent == 0 {
		return mantissa
	}
	return (8 + mantissa) << uint(exponent-1)
}

// Item 是融合结果里的一个 (分数, 文档) 对。
type Item struct {
	Score float64
	Doc   string
}

// RRRFuse 对应 RRF：按 1/(k+rank) 求和，k 默认 60。
func RRRFuse(lists [][]string, k int, topN int) []Item {
	scores := map[string]float64{}
	for _, lst := range lists {
		for pos, doc := range lst {
			scores[doc] += 1.0 / float64(k+pos+1)
		}
	}
	out := make([]Item, 0, len(scores))
	for d, s := range scores {
		out = append(out, Item{s, d})
	}
	sort.Slice(out, func(a, b int) bool {
		if out[a].Score != out[b].Score {
			return out[a].Score > out[b].Score
		}
		return out[a].Doc < out[b].Doc
	})
	if topN > 0 && len(out) > topN {
		out = out[:topN]
	}
	return out
}

// CombMNZ 对应 CMNZscore：|{r : r(d) ≤ c}| × Σ s_r(d)。需要**归一化**的分数。
func CombMNZ(lists [][]string, scoreMaps []map[string]float64, cutoff int) map[string]float64 {
	acc := map[string][2]float64{}
	for i, lst := range lists {
		for pos, doc := range lst {
			if pos+1 > cutoff {
				continue
			}
			a := acc[doc]
			a[0] += 1
			a[1] += scoreMaps[i][doc]
			acc[doc] = a
		}
	}
	out := map[string]float64{}
	for d, a := range acc {
		out[d] = a[0] * a[1]
	}
	return out
}

// CondorcetFuse 按两两多数投票排序。
func CondorcetFuse(lists [][]string, candidates []string) []Item {
	pos := make([]map[string]int, len(lists))
	for i, lst := range lists {
		pos[i] = map[string]int{}
		for j, d := range lst {
			pos[i][d] = j
		}
	}
	wins := map[string]int{}
	for _, a := range candidates {
		for _, b := range candidates {
			if a == b {
				continue
			}
			vote := 0
			for _, p := range pos {
				if _, ok1 := p[a]; ok1 {
					if _, ok2 := p[b]; ok2 {
						if p[a] < p[b] {
							vote++
						} else {
							vote--
						}
					}
				}
			}
			if vote > 0 {
				wins[a]++
			}
		}
	}
	out := make([]Item, 0, len(candidates))
	for _, d := range candidates {
		out = append(out, Item{float64(wins[d]), d})
	}
	sort.Slice(out, func(a, b int) bool {
		if out[a].Score != out[b].Score {
			return out[a].Score > out[b].Score
		}
		return out[a].Doc < out[b].Doc
	})
	return out
}

func main() {
	fmt.Println("== Lucene BM25：idf 恒为正 ==")
	for _, df := range []int{1, 5, 12, 100, 500, 900, 1000} {
		fmt.Printf("  df=%4d  idf=%.6f\n", df, Bm25IDF(df, 1000))
	}

	fmt.Println("\n== tf 归一化：b 的作用（dl=120/tf=4, dl=60/tf=3, dl=300/tf=5） ==")
	for _, b := range []float64{0.0, 0.3, 0.75, 1.0} {
		fmt.Printf("  b=%.2f  %.4f  %.4f  %.4f\n", b,
			Bm25Tf(4, LuceneK1, b, 120, 100),
			Bm25Tf(3, LuceneK1, b, 60, 100),
			Bm25Tf(5, LuceneK1, b, 300, 100))
	}

	fmt.Println("\n== 两种写法给出同一个分 ==")
	for _, c := range []struct {
		name string
		tf   float64
		dl   float64
	}{{"d1", 4, 120}, {"d2", 3, 60}, {"d3", 5, 300}, {"d4", 1, 100}} {
		a := Bm25Score(c.tf, 12, 1000, 100, c.dl, LuceneK1, LuceneB, 1.0)
		z := Bm25ScoreMonotone(c.tf, 12, 1000, 100, c.dl, LuceneK1, LuceneB, 1.0)
		fmt.Printf("  %s  %.10f  %.10f  差=%.3e\n", c.name, a, z, math.Abs(a-z))
	}

	fmt.Println("\n== RRF 融合（k=60） ==")
	dense := []string{"d3", "d1", "d5", "d2"}
	sparse := []string{"d1", "d4", "d2", "d7"}
	for _, it := range RRRFuse([][]string{dense, sparse}, RRFK, 0) {
		fmt.Printf("  %-4s RRF=%.6f\n", it.Doc, it.Score)
	}
	fmt.Printf("  单系统顺序不变: ")
	for _, it := range RRRFuse([][]string{dense}, RRFK, 0) {
		fmt.Printf("%s ", it.Doc)
	}
	fmt.Println()

	fmt.Println("\n== k 的作用：相邻名次贡献差 ==")
	for _, k := range []int{0, 10, 60, 100, 500} {
		fmt.Printf("  k=%3d  1/(k+1)=%.6f  1/(k+2)=%.6f  差=%.6f\n",
			k, 1/float64(k+1), 1/float64(k+2), 1/float64(k+1)-1/float64(k+2))
	}

	fmt.Println("\n== CombMNZ 随尺度放大 / RRF 不随 ==")
	s1 := map[string]float64{"d3": 0.9, "d1": 0.8, "d5": 0.7, "d2": 0.6}
	s2 := map[string]float64{"d1": 0.95, "d4": 0.85, "d2": 0.5, "d7": 0.4}
	cm := CombMNZ([][]string{dense, sparse}, []map[string]float64{s1, s2}, 4)
	big := map[string]float64{}
	for k, v := range s1 {
		big[k] = v * 100
	}
	big2 := map[string]float64{}
	for k, v := range s2 {
		big2[k] = v * 100
	}
	cm2 := CombMNZ([][]string{dense, sparse}, []map[string]float64{big, big2}, 4)
	fmt.Printf("  CombMNZ 原尺度 d1=%.4f   ×100 后 d1=%.2f\n", cm["d1"], cm2["d1"])

	fmt.Println("\n== doc 长度量化（SmallFloat，4 位尾数） ==")
	fmt.Printf("  byte 199 -> %d\n", SmallFloatByte4ToInt(199))
	fmt.Printf("  byte 200 -> %d  (相对步长 %.2f%%)\n", SmallFloatByte4ToInt(200),
		(float64(SmallFloatByte4ToInt(200))/float64(SmallFloatByte4ToInt(199))-1)*100)
}
