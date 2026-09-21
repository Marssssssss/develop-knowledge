// Package main 的构件层:随机源、数据、选择器、稳定性度量、稳定性选择。
//
// 与 Python 版共用同一条 LCG,故两边的数可以逐位对拍;
// 所有公式与语义对齐 sklearn.feature_selection 与 JMLR 18(174)/arXiv:0809.2932。
package main

import "math"

// Rng 是 Numerical Recipes 的线性同余发生器,state 落在 32 位无符号整数上。
type Rng struct{ state uint32 }

func NewRng(seed uint32) *Rng { return &Rng{state: seed} }

func (r *Rng) U32() uint32 {
	r.state = 1664525*r.state + 1013904223 // uint32 溢出即自动取模 2^32
	return r.state
}

// Uniform 返回 [0,1) 上的均匀数;用 2^32 归一,与 Python 版逐位一致。
func (r *Rng) Uniform() float64 { return float64(r.U32()) / 4294967296.0 }

// Normal 用 Box-Muller 变换,一次消耗两个均匀数(与 Python 版保持一致)。
func (r *Rng) Normal() float64 {
	u1 := r.Uniform()
	if u1 == 0 {
		u1 = 1e-12
	}
	u2 := r.Uniform()
	return math.Sqrt(-2.0*math.Log(u1)) * math.Cos(2.0*math.Pi*u2)
}

func (r *Rng) Intn(n int) int { return int(r.U32() % uint32(n)) }

// Subsample 无放回抽 size 个下标(部分 Fisher-Yates),稳定性选择用的就是它。
func Subsample(n, size int, rng *Rng) []int {
	pool := make([]int, n)
	for i := range pool {
		pool[i] = i
	}
	for i := 0; i < size; i++ {
		j := i + rng.Intn(n-i)
		pool[i], pool[j] = pool[j], pool[i]
	}
	return pool[:size]
}

// Bootstrap 有放回抽 n 个下标。
func Bootstrap(n int, rng *Rng) []int {
	out := make([]int, n)
	for i := range out {
		out[i] = rng.Intn(n)
	}
	return out
}

// Set 是特征下标集合。
type Set map[int]bool

func (s Set) Size() int { return len(s) }

func (s Set) Intersect(o Set) int {
	n := 0
	for k := range s {
		if o[k] {
			n++
		}
	}
	return n
}

func (s Set) Union(o Set) int {
	u := make(Set, len(s))
	for k := range s {
		u[k] = true
	}
	for k := range o {
		u[k] = true
	}
	return len(u)
}

// MakeDataset 造二分类数据:前 nInformative 列带类别均值差,其余是纯噪声。
func MakeDataset(n, p, nInformative int, effect float64, seed uint32) ([][]float64, []int) {
	rng := NewRng(seed)
	y := make([]int, n)
	x := make([][]float64, n)
	for i := 0; i < n; i++ {
		y[i] = i % 2
		x[i] = make([]float64, p)
		for j := 0; j < p; j++ {
			v := rng.Normal()
			if j < nInformative && y[i] == 1 {
				v += effect
			}
			x[i][j] = v
		}
	}
	return x, y
}

// FOneway 是单因素方差分析的 F 值,逐步对齐 sklearn.feature_selection.f_oneway:
// sstot = Σx² − (Σx)²/n;ssbn = Σ(Σx_k)²/n_k − (Σx)²/n;sswn = sstot − ssbn;
// F = (ssbn/dfbn)/(sswn/dfwn)。常量列是 0/0 = NaN(官方只发警告,不改写数值)。
func FOneway(groups [][]float64) float64 {
	n := 0
	ssAll, sumAll := 0.0, 0.0
	for _, g := range groups {
		n += len(g)
		for _, v := range g {
			ssAll += v * v
			sumAll += v
		}
	}
	sstot := ssAll - sumAll*sumAll/float64(n)
	ssbn := 0.0
	for _, g := range groups {
		s := 0.0
		for _, v := range g {
			s += v
		}
		ssbn += s * s / float64(len(g))
	}
	ssbn -= sumAll * sumAll / float64(n)
	sswn := sstot - ssbn
	dfbn := float64(len(groups) - 1)
	dfwn := float64(n - len(groups))
	msb, msw := ssbn/dfbn, sswn/dfwn
	if msw == 0 {
		if msb == 0 {
			return math.NaN()
		}
		return math.Inf(1)
	}
	return msb / msw
}

// FClassif 对每一列做 ANOVA F 检验。
func FClassif(x [][]float64, y []int) []float64 {
	p := len(x[0])
	out := make([]float64, p)
	for j := 0; j < p; j++ {
		groups := map[int][]float64{}
		for i, row := range x {
			groups[y[i]] = append(groups[y[i]], row[j])
		}
		order := make([]int, 0, len(groups))
		for k := range groups {
			order = append(order, k)
		}
		sortInts(order)
		gs := make([][]float64, 0, len(order))
		for _, k := range order {
			gs = append(gs, groups[k])
		}
		out[j] = FOneway(gs)
	}
	return out
}

const nanReplacement = -1.7976931348623157e308 // float64 的最小有限值

// SelectKBest 取分数最高的 k 个下标。两处对齐 sklearn:
//  1. _clean_nans 把 NaN 换成 float 的最小有限值(源码注释说 −inf 不可靠);
//  2. argsort(..., kind="mergesort")[-k:] 是稳定排序取尾部,并列时保留下标更大的。
func SelectKBest(scores []float64, k int) Set {
	idx := make([]int, len(scores))
	for i := range idx {
		idx[i] = i
	}
	clean := make([]float64, len(scores))
	for i, s := range scores {
		if math.IsNaN(s) {
			clean[i] = nanReplacement
		} else {
			clean[i] = s
		}
	}
	sortIntsByScore(idx, clean)
	out := Set{}
	for _, i := range idx[len(idx)-k:] {
		out[i] = true
	}
	return out
}

// SelectionSets 跑 m 次「重抽样 + SelectKBest」,返回 m 个特征下标集合。
func SelectionSets(x [][]float64, y []int, k, m int, rng *Rng, bootstrap bool) []Set {
	n := len(x)
	out := make([]Set, m)
	for t := 0; t < m; t++ {
		var idx []int
		if bootstrap {
			idx = Bootstrap(n, rng)
		} else {
			idx = Subsample(n, n/2, rng) // 稳定性选择的口径:⌊n/2⌋ 无放回
		}
		xs := make([][]float64, len(idx))
		ys := make([]int, len(idx))
		for i, id := range idx {
			xs[i], ys[i] = x[id], y[id]
		}
		out[t] = SelectKBest(FClassif(xs, ys), k)
	}
	return out
}

// Jaccard 相似度。
func Jaccard(a, b Set) float64 { return float64(a.Intersect(b)) / float64(a.Union(b)) }

// Dice 相似度。
func Dice(a, b Set) float64 {
	return 2 * float64(a.Intersect(b)) / float64(a.Size()+b.Size())
}

// Kuncheva 是机会校正指标 (r·d − k²)/(k·(d − k)),只对等势集合有定义。
func Kuncheva(a, b Set, d int) float64 {
	k := a.Size()
	r := float64(a.Intersect(b))
	fk := float64(k)
	return (r*float64(d) - fk*fk) / (fk * (float64(d) - fk))
}

// MeanPairwise 计算所有无序对的相似度平均。
func MeanPairwise(sets []Set, sim func(a, b Set) float64) float64 {
	m := len(sets)
	tot := 0.0
	for i := 0; i < m; i++ {
		for j := i + 1; j < m; j++ {
			tot += sim(sets[i], sets[j])
		}
	}
	return tot / (float64(m) * float64(m-1) / 2)
}

// PhiStability 是 Nogueira 等的估计量(Definition 4):
// Φ̂ = 1 − [Σ_f s²_f / d] / [(k̄/d)(1 − k̄/d)],s²_f = M/(M−1)·p̂_f(1−p̂_f)。
func PhiStability(sets []Set, d int) float64 {
	m := len(sets)
	count := make([]float64, d)
	kSum := 0.0
	for _, s := range sets {
		kSum += float64(s.Size())
		for f := range s {
			count[f]++
		}
	}
	varSum := 0.0
	for f := 0; f < d; f++ {
		p := count[f] / float64(m)
		varSum += float64(m) / float64(m-1) * p * (1 - p)
	}
	varSum /= float64(d)
	kBar := kSum / float64(m)
	return 1.0 - varSum/((kBar/float64(d))*(1-kBar/float64(d)))
}

// RandomSets 生成零模型集合:每个都是从 d 个特征里均匀无放回抽 k 个。
func RandomSets(m, k, d int, rng *Rng) []Set {
	out := make([]Set, m)
	for t := 0; t < m; t++ {
		s := Set{}
		for _, f := range Subsample(d, k, rng) {
			s[f] = true
		}
		out[t] = s
	}
	return out
}

// SelectionProbabilities 返回每列的入选频率。
func SelectionProbabilities(sets []Set, d int) []float64 {
	m := len(sets)
	out := make([]float64, d)
	for _, s := range sets {
		for f := range s {
			out[f] += 1.0 / float64(m)
		}
	}
	return out
}

// StableFeatures 是 Definition 2:{k : max_λ Π̂^λ_k ≥ π_thr}。
func StableFeatures(probs []float64, piThr float64) Set {
	out := Set{}
	for f, p := range probs {
		if p >= piThr {
			out[f] = true
		}
	}
	return out
}

// PferBound 是 Theorem 1:E(V) ≤ q²/((2π_thr − 1)·p),q 是平均选中个数。
func PferBound(q, piThr float64, p int) float64 {
	return q * q / ((2*piThr - 1) * float64(p))
}
