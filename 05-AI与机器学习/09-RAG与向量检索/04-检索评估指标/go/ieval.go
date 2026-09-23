package main

// 离线检索评估指标 —— 按 scikit-learn 与 trec_eval 官方实现转写的两套口径。
//
// 依据（本轮实读）：
//   - sklearn/metrics/_ranking.py：discount = 1/(log(i+2)/log(base))；k 截断是
//     `discount[k:] = 0`；ignore_ties=False 走 _tie_averaged_dcg，True 走 argsort；
//     IDCG 用 y_score=y_true 且强制 ignore_ties=True；gain 全 0 时结果置 0
//   - trec_eval/m_ndcg.c：gain 默认取相关性等级本身；discount 同为 log2(i+2)；
//     无相关文档时不产出该 topic 的值
//   - trec_eval/m_recall.c：默认截断点 5,10,15,20,30,100,200,500,1000；
//     `if (i == cutoffs[cutoff_index])` 在计数之前 ⇒ recall@k 统计 rank 1..k；
//     res_rels.num_rel == 0 时 return 0

import (
	"fmt"
	"math"
	"sort"
)

// DiscountVector 对应 `_dcg_sample_scores` 的折扣向量。
func DiscountVector(n int, k *int, logBase float64) []float64 {
	d := make([]float64, n)
	for i := 0; i < n; i++ {
		d[i] = 1.0 / (math.Log(float64(i)+2) / math.Log(logBase))
	}
	if k != nil {
		for i := *k; i < n; i++ {
			d[i] = 0.0
		}
	}
	return d
}

// ArgsortDesc 复刻 `np.argsort(y_score)[:, ::-1]`；分数相等时按下标升序。
func ArgsortDesc(scores []float64) []int {
	idx := make([]int, len(scores))
	for i := range idx {
		idx[i] = i
	}
	sort.SliceStable(idx, func(a, b int) bool { return scores[idx[a]] > scores[idx[b]] })
	return idx
}

// TieAveragedDCG 对应 `_tie_averaged_dcg`：组内取平均增益 × 该组折扣之和。
func TieAveragedDCG(yTrue, yScore, discount []float64) float64 {
	order := ArgsortDesc(yScore)
	type group struct {
		members []int
		key     float64
	}
	var groups []group
	for _, i := range order {
		if n := len(groups); n > 0 && groups[n-1].key == yScore[i] {
			groups[n-1].members = append(groups[n-1].members, i)
		} else {
			groups = append(groups, group{[]int{i}, yScore[i]})
		}
	}
	cumsum := make([]float64, len(discount))
	acc := 0.0
	for i, v := range discount {
		acc += v
		cumsum[i] = acc
	}
	total, start := 0.0, 0
	for _, g := range groups {
		end := start + len(g.members)
		sum, n := 0.0, float64(len(g.members))
		for _, i := range g.members {
			sum += yTrue[i]
		}
		before := 0.0
		if start > 0 {
			before = cumsum[start-1]
		}
		total += (sum / n) * (cumsum[end-1] - before)
		start = end
	}
	return total
}

// DCG 单个 query 的 DCG。
func DCG(yTrue, yScore []float64, k *int, ignoreTies bool) float64 {
	n := len(yTrue)
	discount := DiscountVector(n, k, 2.0)
	if ignoreTies {
		order := ArgsortDesc(yScore)
		total := 0.0
		for pos, i := range order {
			total += discount[pos] * yTrue[i]
		}
		return total
	}
	return TieAveragedDCG(yTrue, yScore, discount)
}

// NDCG 对应 ndcg_score 的单 query 版本；IDCG 用 y_score=y_true 且 ignore_ties=True。
func NDCG(yTrue, yScore []float64, k *int, ignoreTies bool) float64 {
	gain := DCG(yTrue, yScore, k, ignoreTies)
	ideal := DCG(yTrue, yTrue, k, true)
	if ideal == 0.0 {
		return 0.0
	}
	return gain / ideal
}

// TrecNDCG 对应 m_ndcg.c：gain = 相关性等级本身，无任何 relevant doc 时返回 NaN。
func TrecNDCG(rels []int, k *int) float64 {
	resultsDCG := 0.0
	for i, rel := range rels {
		if k != nil && i >= *k {
			break
		}
		if rel != 0 {
			resultsDCG += float64(rel) / math.Log(float64(i)+2, 2)
		}
	}
	ideal := []int{}
	for _, r := range rels {
		if r >= 1 {
			ideal = append(ideal, r)
		}
	}
	sort.Sort(sort.Reverse(sort.IntSlice(ideal)))
	if k != nil && len(ideal) > *k {
		ideal = ideal[:*k]
	}
	idealDCG := 0.0
	for i, rel := range ideal {
		if rel <= 0 {
			break
		}
		idealDCG += float64(rel) / math.Log(float64(i)+2, 2)
	}
	if idealDCG == 0.0 {
		return math.NaN()
	}
	return resultsDCG / idealDCG
}

// DefaultRecallCutoffs 对应 trec_eval m_recall.c 的默认截断点。
var DefaultRecallCutoffs = []int{5, 10, 15, 20, 30, 100, 200, 500, 1000}

// RecallAt 对应 te_calc_recall；numRel == 0 时返回空（不产出值）。
func RecallAt(rels []int, cutoffs []int) map[int]float64 {
	if cutoffs == nil {
		cutoffs = DefaultRecallCutoffs
	}
	numRel := 0
	for _, r := range rels {
		if r >= 1 {
			numRel++
		}
	}
	if numRel == 0 {
		return map[int]float64{}
	}
	cs := append([]int{}, cutoffs...)
	sort.Ints(cs)
	out := map[int]float64{}
	hit, ci := 0, 0
	for i, rel := range rels {
		for ci < len(cs) && i == cs[ci] {
			out[cs[ci]] = float64(hit) / float64(numRel)
			ci++
		}
		if rel >= 1 {
			hit++
		}
	}
	for ci < len(cs) {
		out[cs[ci]] = float64(hit) / float64(numRel)
		ci++
	}
	return out
}

// PrecisionAt Precision@k。
func PrecisionAt(rels []int, k int) float64 {
	hit := 0
	for i, r := range rels {
		if i >= k {
			break
		}
		if r >= 1 {
			hit++
		}
	}
	return float64(hit) / float64(k)
}

// MRR 倒数排序平均。
func MRR(lists [][]int) float64 {
	total := 0.0
	for _, rels := range lists {
		for i, r := range rels {
			if r >= 1 {
				total += 1.0 / float64(i+1)
				break
			}
		}
	}
	return total / float64(len(lists))
}

// AveragePrecision 平均精度。
func AveragePrecision(rels []int) float64 {
	numRel := 0
	for _, r := range rels {
		if r >= 1 {
			numRel++
		}
	}
	if numRel == 0 {
		return 0.0
	}
	hit, total := 0, 0.0
	for i, r := range rels {
		if r >= 1 {
			hit++
			total += float64(hit) / float64(i+1)
		}
	}
	return total / float64(numRel)
}

func kptr(k int) *int { return &k }

func main() {
	rels := []int{2, 0, 1, 0, 3, 0}
	pred := []float64{0.9, 0.8, 0.7, 0.6, 0.5, 0.4}
	yt := []float64{2, 0, 1, 0, 3, 0}

	fmt.Println("== 折扣向量 1/log2(i+2) ==")
	d := DiscountVector(6, nil, 2.0)
	fmt.Printf("  disc   : ")
	for _, v := range d {
		fmt.Printf("%6.4f ", v)
	}
	fmt.Println()

	fmt.Println("\n== 同一份结果的两套 NDCG ==")
	fmt.Printf("  sklearn          = %.6f\n", NDCG(yt, pred, nil, false))
	fmt.Printf("  sklearn @3       = %.6f\n", NDCG(yt, pred, kptr(3), false))
	fmt.Printf("  sklearn ties=T   = %.6f\n", NDCG(yt, pred, nil, true))
	fmt.Printf("  trec_eval        = %.6f\n", TrecNDCG(rels, nil))
	fmt.Printf("  trec_eval @3     = %.6f\n", TrecNDCG(rels, kptr(3)))
	fmt.Printf("  完美排序         = %.6f\n", NDCG(yt, yt, nil, false))

	fmt.Println("\n== recall / precision ==")
	rl := []int{1, 0, 1, 0, 1, 0, 0, 0, 0, 1}
	r := RecallAt(rl, []int{1, 3, 5, 10, 20})
	for _, c := range []int{1, 3, 5, 10, 20} {
		fmt.Printf("  @%-3d recall=%.2f precision=%.2f\n", c, r[c], PrecisionAt(rl, c))
	}

	fmt.Println("\n== MRR / MAP ==")
	lists := [][]int{{1, 0, 0, 1}, {0, 0, 1}, {0, 0, 0}}
	fmt.Printf("  MRR = %.6f\n", MRR(lists))
	mapTotal := 0.0
	for _, l := range lists {
		fmt.Printf("  AP  = %.4f\n", AveragePrecision(l))
		mapTotal += AveragePrecision(l)
	}
	fmt.Printf("  MAP = %.6f\n", mapTotal/float64(len(lists)))

	fmt.Println("\n== 全不相关 ==")
	fmt.Printf("  sklearn ndcg     = %.1f\n", NDCG([]float64{0, 0}, []float64{1, 0}, nil, false))
	fmt.Printf("  trec_eval ndcg   = %v\n", math.IsNaN(TrecNDCG([]int{0, 0}, nil)))
	fmt.Printf("  recall_at 空?    = %v\n", len(RecallAt([]int{0, 0, 0}, nil)) == 0)
}
