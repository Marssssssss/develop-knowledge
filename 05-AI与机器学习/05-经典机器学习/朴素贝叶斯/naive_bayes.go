// 朴素贝叶斯 Go 实现:高斯 + 多项式(log-sum-exp 防上溢)。
//
// 权威来源(与 naive_bayes.py 同一批,实际读过):scikit-learn 1.9《1.9. Naive Bayes》
// https://scikit-learn.org/stable/modules/naive_bayes.html —— P(y|x) ∝ P(y)·∏P(xi|y);
// θ̂yi = (Nyi+α)/(Ny+αn),α=1 为 Laplace 平滑;GaussianNB 用正态密度;官方原文 "a decent
// classifier but a bad estimator";实现对齐 sklearn 的 _joint_log_likelihood。
// Go 特有点:negInf 用 var(常量不能是函数调用)、无内建 logsumexp/softmax、无可变默认参数。
package main
import (
	"fmt"
	"math"
	"sort"
)

var negInf = math.Inf(-1)

const log2pi = 1.8378770664093453 // ln(2π)

// safeLog 返回 ln(p);p<=0 时返回 −inf,用来复现"未平滑 => 整类后验被钉死"。
func safeLog(p float64) float64 {
	if p <= 0 {
		return negInf
	}
	return math.Log(p)
}

// logSumExp = ln Σ exp(xi);减去 max 防上溢,再补回。
func logSumExp(xs []float64) float64 {
	m, s := negInf, 0.0
	for _, x := range xs {
		if x > m {
			m = x
		}
	}
	if math.IsInf(m, -1) { // 全为 −inf:exp(x−m) 会算出 NaN,必须短路
		return negInf
	}
	for _, x := range xs {
		s += math.Exp(x - m)
	}
	return m + math.Log(s)
}

func softmaxFromLog(jll []float64) []float64 {
	z, out := logSumExp(jll), make([]float64, len(jll))
	if math.IsInf(z, -1) {
		return out
	}
	for i, v := range jll {
		out[i] = math.Exp(v - z)
	}
	return out
}

func argmax(xs []float64) int {
	best := 0
	for i, v := range xs {
		if v > xs[best] {
			best = i
		}
	}
	return best
}

// sortedClasses 去重并升序 —— 与 Python 版 sorted(set(y)) 对齐,保证 [0,1] 顺序一致。
func sortedClasses(y []int) []int {
	seen, out := map[int]bool{}, []int{}
	for _, c := range y {
		if !seen[c] {
			seen[c] = true
			out = append(out, c)
		}
	}
	sort.Ints(out)
	return out
}

type MultinomialNB struct {
	alpha          float64
	nFeatures      int
	classes        []int
	featureLogProb map[int][]float64
	classLogPrior  map[int]float64
}

func NewMultinomialNB(alpha float64) *MultinomialNB {
	return &MultinomialNB{alpha: alpha, featureLogProb: map[int][]float64{}, classLogPrior: map[int]float64{}}
}

func (m *MultinomialNB) Fit(X [][]int, y []int) *MultinomialNB {
	m.nFeatures, m.classes = len(X[0]), sortedClasses(y)
	for _, c := range m.classes {
		cnt, k := make([]int, m.nFeatures), 0
		for i, yi := range y {
			if yi != c {
				continue
			}
			k++
			for j := range cnt {
				cnt[j] += X[i][j]
			}
		}
		m.classLogPrior[c] = math.Log(float64(k) / float64(len(y)))
		total := 0
		for _, v := range cnt {
			total += v
		}
		den := float64(total) + m.alpha*float64(m.nFeatures) // θ̂ = (Nyi+α)/(Ny+αn)
		fl := make([]float64, m.nFeatures)
		for j := range cnt {
			fl[j] = safeLog((float64(cnt[j]) + m.alpha) / den)
		}
		m.featureLogProb[c] = fl
	}
	return m
}

func (m *MultinomialNB) JointLogLikelihood(x []int) []float64 {
	out := make([]float64, 0, len(m.classes))
	for _, c := range m.classes {
		fl := m.featureLogProb[c]
		ll := m.classLogPrior[c]
		for j, xj := range x {
			if xj != 0 { // 跳过 0 计数,避免 0·(−inf) → NaN
				ll += float64(xj) * fl[j]
			}
		}
		out = append(out, ll)
	}
	return out
}

func (m *MultinomialNB) PredictProba(x []int) []float64 {
	return softmaxFromLog(m.JointLogLikelihood(x))
}

func (m *MultinomialNB) Predict(x []int) int {
	return m.classes[argmax(m.JointLogLikelihood(x))]
}

type GaussianNB struct {
	varSmoothing  float64
	nFeatures     int
	classes       []int
	theta, vars   map[int][]float64
	classLogPrior map[int]float64
}

func NewGaussianNB(varSmoothing float64) *GaussianNB {
	return &GaussianNB{varSmoothing: varSmoothing, theta: map[int][]float64{},
		vars: map[int][]float64{}, classLogPrior: map[int]float64{}}
}

func (g *GaussianNB) Fit(X [][]float64, y []int) *GaussianNB {
	n := len(X)
	g.nFeatures, g.classes = len(X[0]), sortedClasses(y)
	spread := 0.0 // eps = var_smoothing × 各列极差的最大值(sklearn 的 _epsilon 做法)
	for j := 0; j < g.nFeatures; j++ {
		lo, hi := X[0][j], X[0][j]
		for i := 1; i < n; i++ {
			lo, hi = math.Min(lo, X[i][j]), math.Max(hi, X[i][j])
		}
		spread = math.Max(spread, hi-lo)
	}
	eps := g.varSmoothing * spread
	for _, c := range g.classes {
		rows := []int{}
		for i, yi := range y {
			if yi == c {
				rows = append(rows, i)
			}
		}
		g.classLogPrior[c] = math.Log(float64(len(rows)) / float64(n))
		mu := make([]float64, g.nFeatures)
		for _, i := range rows {
			for j := range mu {
				mu[j] += X[i][j]
			}
		}
		for j := range mu {
			mu[j] /= float64(len(rows))
		}
		va := make([]float64, g.nFeatures)
		for _, i := range rows {
			for j := range va {
				d := X[i][j] - mu[j]
				va[j] += d * d
			}
		}
		for j := range va {
			va[j] = va[j]/float64(len(rows)) + eps
		}
		g.theta[c], g.vars[c] = mu, va
	}
	return g
}

func (g *GaussianNB) JointLogLikelihood(x []float64) []float64 {
	out := make([]float64, 0, len(g.classes))
	for _, c := range g.classes {
		ll := g.classLogPrior[c]
		for j := 0; j < g.nFeatures; j++ {
			mu, va := g.theta[c][j], g.vars[c][j]
			ll += -0.5*(log2pi+math.Log(va)) - (x[j]-mu)*(x[j]-mu)/(2*va)
		}
		out = append(out, ll)
	}
	return out
}

func (g *GaussianNB) Predict(x []float64) int {
	return g.classes[argmax(g.JointLogLikelihood(x))]
}

func (g *GaussianNB) PredictProba(x []float64) []float64 {
	return softmaxFromLog(g.JointLogLikelihood(x))
}

var vocab = []string{"free", "money", "win", "click", "meeting",
	"report", "project", "lunch", "urgent", "team"}

var docs = [][]int{
	{3, 2, 1, 1, 0, 0, 0, 0, 0, 0}, {2, 1, 1, 1, 0, 0, 0, 0, 0, 0},
	{1, 0, 1, 1, 0, 0, 0, 0, 0, 0}, {1, 1, 0, 0, 0, 0, 0, 0, 1, 0},
	{1, 1, 1, 0, 0, 0, 0, 0, 0, 0}, {0, 0, 0, 0, 1, 1, 1, 0, 0, 0},
	{0, 0, 0, 0, 1, 0, 0, 1, 0, 1}, {0, 0, 0, 0, 0, 1, 1, 0, 0, 1},
	{0, 0, 0, 0, 1, 0, 1, 0, 1, 0}, {0, 0, 0, 0, 0, 1, 1, 1, 0, 0},
	{0, 0, 0, 0, 0, 1, 1, 0, 0, 1}, {0, 0, 0, 0, 1, 0, 0, 1, 0, 1},
}

var labels = []int{1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0} // 1=spam 0=ham

func main() {
	fmt.Println("======================================================================")
	fmt.Println("朴素贝叶斯 Go 实现(高斯 / 多项式)")
	fmt.Println("======================================================================")

	X, y := [][]float64{}, []int{}
	seed := uint64(20260914)
	rnd := func() float64 { // 线性同余,保证跨平台可复现
		seed = seed*6364136223846793005 + 1442695040888963407
		return float64(seed>>11) / float64(uint64(1)<<53)
	}
	for c, ctr := range [][2]float64{{0, 0}, {2.5, 2.0}} {
		for i := 0; i < 60; i++ {
			X = append(X, []float64{ctr[0] + (rnd()-0.5)*3.4, ctr[1] + (rnd()-0.5)*4.1})
			y = append(y, c)
		}
	}
	gnb := NewGaussianNB(1e-9).Fit(X, y)
	correct := 0
	for i := range X {
		if gnb.Predict(X[i]) == y[i] {
			correct++
		}
	}
	fmt.Printf("\n① GaussianNB  训练 %d 样本 / 2 类 / 2 特征\n", len(X))
	for _, c := range gnb.classes {
		fmt.Printf("   class %d: prior=%.4f theta=(%+.4f, %+.4f) var=(%.4f, %.4f)\n",
			c, math.Exp(gnb.classLogPrior[c]), gnb.theta[c][0], gnb.theta[c][1],
			gnb.vars[c][0], gnb.vars[c][1])
	}
	fmt.Printf("   训练集准确率 = %.4f\n", float64(correct)/float64(len(X)))
	p := gnb.PredictProba([]float64{1.0, 1.0})
	fmt.Printf("   x=(1.0,1.0) => P = [%.6f %.6f]   ΣP = %.15f\n", p[0], p[1], p[0]+p[1])

	fmt.Printf("\n② MultinomialNB  文本分类 %d 篇 / 词表 %d 词"+
		"(Laplace / Lidstone / 未平滑 MLE)\n", len(docs), len(vocab))
	for _, c := range []struct {
		a   float64
		tag string
	}{{1.0, "Laplace"}, {0.1, "Lidstone"}, {0.0, "未平滑 MLE"}} {
		m := NewMultinomialNB(c.a).Fit(docs, labels)
		ok := 0
		for i, d := range docs {
			if m.Predict(d) == labels[i] {
				ok++
			}
		}
		fmt.Printf("   alpha=%-5v %-12s 训练准确率 = %.4f\n", c.a, c.tag, float64(ok)/float64(len(docs)))
	}

	winOnly := []int{0, 0, 1, 0, 0, 0, 0, 0, 0, 0}
	fmt.Println("\n③ 平滑的必要性:ham 类从未见过 'win' => logθ = −inf => P(ham|x) = 0")
	for _, a := range []float64{0.0, 1.0} {
		jll := NewMultinomialNB(a).Fit(docs, labels).JointLogLikelihood(winOnly)
		sh, pr := "", softmaxFromLog(jll)
		for _, v := range jll {
			if math.IsInf(v, -1) {
				sh += " −inf"
			} else {
				sh += fmt.Sprintf(" %+.4f", v)
			}
		}
		fmt.Printf("   alpha=%-5v joint_ll(ham,spam)=[%s]  P(ham)=%.6f P(spam)=%.6f\n",
			a, sh, pr[0], pr[1])
	}
	fmt.Println("----------------------------------------------------------------------")
}
