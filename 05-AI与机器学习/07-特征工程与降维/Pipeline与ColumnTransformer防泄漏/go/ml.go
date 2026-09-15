// ml.go —— 极简 Estimator 契约:变换器与分类器共用 Fit / Transform / Predict。
//
// sklearn 真实实现里 TransformerMixin 与 PredictorMixin 是分开的;这里为了让
// Pipeline 能用同一种切片装下所有步骤,把 Transform 也放进同一个接口
// (分类器的 Transform 是恒等操作),语义与 Python 版 pipeline_lite.py 一致。
package main

import (
	"math"
	"math/rand"
	"sort"
)

// Estimator 是 Pipeline 里任一级的最小契约。
type Estimator interface {
	Fit(X [][]float64, y []int)
	Transform(X [][]float64) [][]float64
	Predict(X [][]float64) []int
}

func sigmoid(z float64) float64 {
	if z < -35 {
		return 0
	}
	if z > 35 {
		return 1
	}
	return 1 / (1 + math.Exp(-z))
}

// ---------------------------------------------------------------- StandardScaler

type StandardScaler struct{ mu, sd []float64 }

func (s *StandardScaler) Fit(X [][]float64, y []int) {
	n, p := len(X), len(X[0])
	s.mu, s.sd = make([]float64, p), make([]float64, p)
	for j := 0; j < p; j++ {
		var sum float64
		for i := 0; i < n; i++ {
			sum += X[i][j]
		}
		s.mu[j] = sum / float64(n)
		var v float64
		for i := 0; i < n; i++ {
			d := X[i][j] - s.mu[j]
			v += d * d
		}
		s.sd[j] = math.Sqrt(v / float64(n-1))
		if s.sd[j] == 0 { // 常数列:退化为 1,避免除零
			s.sd[j] = 1
		}
	}
}

func (s *StandardScaler) Transform(X [][]float64) [][]float64 {
	Z := make([][]float64, len(X))
	for i, row := range X {
		Z[i] = make([]float64, len(row))
		for j := range row {
			Z[i][j] = (row[j] - s.mu[j]) / s.sd[j]
		}
	}
	return Z
}

func (s *StandardScaler) Predict(X [][]float64) []int { return nil }

// ---------------------------------------------------------------- SelectKBest

// SelectKBest 用组间平方和(ANOVA F 的分子)给每个特征打分,保留 top-K。
// 关键点:counts / means 全部来自 Fit 时看到的那份数据 —— 它看到什么,就学什么。
type SelectKBest struct {
	K   int
	idx []int
}

func (s *SelectKBest) Fit(X [][]float64, y []int) {
	n, p := len(X), len(X[0])
	var yb float64
	for _, v := range y {
		yb += float64(v)
	}
	yb /= float64(n)
	scores := make([]float64, p)
	for j := 0; j < p; j++ {
		var sa, sb float64
		var na, nb int
		for i := 0; i < n; i++ {
			if y[i] == 1 {
				sa += X[i][j]
				na++
			} else {
				sb += X[i][j]
				nb++
			}
		}
		if na == 0 || nb == 0 {
			continue
		}
		da, db := sa/float64(na)-yb, sb/float64(nb)-yb
		scores[j] = float64(na)*da*da + float64(nb)*db*db
	}
	order := make([]int, p)
	for j := range order {
		order[j] = j
	}
	sort.SliceStable(order, func(a, b int) bool { return scores[order[a]] > scores[order[b]] })
	k := s.K
	if k > p {
		k = p
	}
	s.idx = order[:k]
}

func (s *SelectKBest) Transform(X [][]float64) [][]float64 {
	Z := make([][]float64, len(X))
	for i, row := range X {
		Z[i] = make([]float64, len(s.idx))
		for t, j := range s.idx {
			Z[i][t] = row[j]
		}
	}
	return Z
}

func (s *SelectKBest) Predict(X [][]float64) []int { return nil }

// ---------------------------------------------------------------- LogReg

// LogReg 是全批量梯度下降的 L2 逻辑回归。它不是变换器,Transform 为恒等。
type LogReg struct {
	Epochs int
	Lr, L2 float64
	w      []float64
}

func (m *LogReg) Fit(X [][]float64, y []int) {
	n, p := len(X), len(X[0])+1
	m.w = make([]float64, p)
	g := make([]float64, p)
	for e := 0; e < m.Epochs; e++ {
		for j := range g {
			g[j] = 0
		}
		for i := 0; i < n; i++ {
			z := m.w[0]
			for j := 0; j < p-1; j++ {
				z += m.w[j+1] * X[i][j]
			}
			err := sigmoid(z) - float64(y[i])
			g[0] += err
			for j := 0; j < p-1; j++ {
				g[j+1] += err * X[i][j]
			}
		}
		for j := 0; j < p; j++ {
			m.w[j] -= m.Lr * (g[j]/float64(n) + m.L2*m.w[j])
		}
	}
}

func (m *LogReg) Transform(X [][]float64) [][]float64 { return X }

func (m *LogReg) score(row []float64) float64 {
	z := m.w[0]
	for j, v := range row {
		z += m.w[j+1] * v
	}
	return z
}

func (m *LogReg) Predict(X [][]float64) []int {
	out := make([]int, len(X))
	for i, row := range X {
		if m.score(row) > 0 {
			out[i] = 1
		}
	}
	return out
}

// ---------------------------------------------------------------- Pipeline

// Pipeline 保证同一批样本既用来训练变换器、也用来训练分类器。
type Pipeline struct{ steps []Estimator }

func NewPipeline(steps ...Estimator) *Pipeline { return &Pipeline{steps: steps} }

func (p *Pipeline) Fit(X [][]float64, y []int) {
	Xt := X
	for i := 0; i < len(p.steps)-1; i++ {
		p.steps[i].Fit(Xt, y) // 先用当前这一级学统计量……
		Xt = p.steps[i].Transform(Xt)
	}
	p.steps[len(p.steps)-1].Fit(Xt, y) // 最后一级是估计器,只 Fit 不 transform
}

func (p *Pipeline) Transform(X [][]float64) [][]float64 {
	Xt := X
	for i := 0; i < len(p.steps)-1; i++ {
		Xt = p.steps[i].Transform(Xt)
	}
	return Xt
}

func (p *Pipeline) Predict(X [][]float64) []int {
	return p.steps[len(p.steps)-1].Predict(p.Transform(X))
}

// ---------------------------------------------------------------- 评估工具

func accuracy(est Estimator, X [][]float64, y []int) float64 {
	pred := est.Predict(X)
	ok := 0
	for i := range y {
		if pred[i] == y[i] {
			ok++
		}
	}
	return float64(ok) / float64(len(y))
}

// crossValScore 在每一折内部重新调用 makeEst() 并 Fit ——
// "安全"的全部秘密就是这一句:整条管线在折内重建,验证折从未参与任何 Fit。
func crossValScore(makeEst func() Estimator, X [][]float64, y []int, folds int, seed int64) []float64 {
	n := len(y)
	idx := make([]int, n)
	for i := range idx {
		idx[i] = i
	}
	rand.New(rand.NewSource(seed)).Shuffle(n, func(a, b int) { idx[a], idx[b] = idx[b], idx[a] })
	scores := make([]float64, folds)
	for k := 0; k < folds; k++ {
		lo, hi := k*n/folds, (k+1)*n/folds
		inVal := make([]bool, n)
		for _, i := range idx[lo:hi] {
			inVal[i] = true
		}
		var Xtr, Xva [][]float64
		var ytr, yva []int
		for i := 0; i < n; i++ {
			if inVal[i] {
				Xva, yva = append(Xva, X[i]), append(yva, y[i])
			} else {
				Xtr, ytr = append(Xtr, X[i]), append(ytr, y[i])
			}
		}
		est := makeEst()
		est.Fit(Xtr, ytr)
		scores[k] = accuracy(est, Xva, yva)
	}
	return scores
}
