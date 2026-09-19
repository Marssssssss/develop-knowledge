// 同属 main 包,与 num.go / gmm.go 一起用 `go run .` 编译。
package main

import (
	"math"
)

// GaussianMixture:EM 版 GMM。covariances 的布局随 CovType 变化:
// full → [K][d][d];tied → [d][d];diag → [K][d];spherical → [K]。
type GaussianMixture struct {
	K          int
	CovType    string
	RegCovar   float64
	Tol        float64
	MaxIter    int
	Init       string
	Seed       uint64
	Weights    []float64
	Means      [][]float64
	Cov        interface{}
	LowerBound float64
	Trace      []float64

	chol   [][][]float64
	logdet []float64
}

func NewGMM(k int, covType string, seed uint64) *GaussianMixture {
	return &GaussianMixture{K: k, CovType: covType, RegCovar: regCovar, Tol: 1e-4,
		MaxIter: 100, Init: "kmeans", Seed: seed}
}

func (g *GaussianMixture) d() int { return len(g.Means[0]) }

func (g *GaussianMixture) fullCov(k int) [][]float64 { return g.Cov.([][][]float64)[k] }
func (g *GaussianMixture) tiedCov() [][]float64      { return g.Cov.([][][]float64)[0] }
func (g *GaussianMixture) diagCov(k int) []float64   { return g.Cov.([][]float64)[k] }
func (g *GaussianMixture) sphVar(k int) float64      { return g.Cov.([]float64)[k] }

// prepare 预分解 Cholesky 并缓存 log|Σ|。
func (g *GaussianMixture) prepare() error {
	d := g.d()
	g.chol, g.logdet = nil, nil
	switch g.CovType {
	case "full", "tied":
		var mats [][][]float64
		if g.CovType == "full" {
			mats = g.Cov.([][][]float64)
		} else {
			mats = [][][]float64{g.Cov.([][][]float64)[0]}
		}
		for len(mats) < g.K {
			mats = append(mats, mats[0])
		}
		g.chol = make([][][]float64, g.K)
		g.logdet = make([]float64, g.K)
		for k := 0; k < g.K; k++ {
			L, err := cholesky(mats[imin(k, len(mats)-1)])
			if err != nil {
				return err
			}
			g.chol[k] = L
			g.logdet[k] = logDetChol(L)
		}
	case "diag":
		g.logdet = make([]float64, g.K)
		for k := 0; k < g.K; k++ {
			s := 0.0
			for _, v := range g.diagCov(k) {
				s += math.Log(math.Max(v, 1e-300))
			}
			g.logdet[k] = s
		}
	default: // spherical
		g.logdet = make([]float64, g.K)
		for k := 0; k < g.K; k++ {
			g.logdet[k] = float64(d) * math.Log(math.Max(g.sphVar(k), 1e-300))
		}
	}
	return nil
}

func imin(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func (g *GaussianMixture) logGauss(k int, x []float64) float64 {
	d := g.d()
	diff := make([]float64, d)
	for j := 0; j < d; j++ {
		diff[j] = x[j] - g.Means[k][j]
	}
	var mh float64
	switch g.CovType {
	case "full", "tied":
		mh = mahalSqChol(g.chol[k], diff)
	case "diag":
		for j := 0; j < d; j++ {
			mh += diff[j] * diff[j] / g.diagCov(k)[j]
		}
	default:
		for j := 0; j < d; j++ {
			mh += diff[j] * diff[j]
		}
		mh /= g.sphVar(k)
	}
	return -0.5 * (float64(d)*math.Log(2*math.Pi) + g.logdet[k] + mh)
}

func (g *GaussianMixture) logProb(X [][]float64) [][]float64 {
	out := make([][]float64, len(X))
	for i, x := range X {
		out[i] = make([]float64, g.K)
		for k := 0; k < g.K; k++ {
			out[i][k] = math.Log(math.Max(g.Weights[k], 1e-300)) + g.logGauss(k, x)
		}
	}
	return out
}

func (g *GaussianMixture) eStep(X [][]float64) ([][]float64, float64) {
	lp := g.logProb(X)
	resp := make([][]float64, len(X))
	total := 0.0
	for i, row := range lp {
		norm := logSumExp(row)
		resp[i] = make([]float64, g.K)
		for k := range row {
			resp[i][k] = math.Exp(row[k] - norm)
		}
		total += norm
	}
	return resp, total
}

func (g *GaussianMixture) Fit(X [][]float64) error {
	n := len(X)
	centers, labels := KMeans(X, g.K, g.Seed)
	g.Weights = make([]float64, g.K)
	g.Means = make([][]float64, g.K)
	for k := 0; k < g.K; k++ {
		g.Weights[k] = 1 / float64(g.K)
		g.Means[k] = append([]float64(nil), centers[k]...)
	}
	// 用 k-means 的硬划分跑一次 M 步,直接得到目标协方差类型的初值
	hard := make([][]float64, n)
	for i, l := range labels {
		hard[i] = make([]float64, g.K)
		hard[i][l] = 1.0
	}
	g.mStep(X, hard)
	if err := g.prepare(); err != nil {
		return err
	}
	prev := 0.0
	for it := 0; it < g.MaxIter; it++ {
		resp, ll := g.eStep(X)
		g.Trace = append(g.Trace, ll)
		g.LowerBound = ll
		g.mStep(X, resp)
		if err := g.prepare(); err != nil {
			return err
		}
		if it > 0 && math.Abs(ll-prev) < g.Tol*math.Abs(prev) {
			break
		}
		prev = ll
	}
	return nil
}

// Score 返回平均对数似然(sklearn 的 score 语义)。
func (g *GaussianMixture) Score(X [][]float64) float64 {
	_ = g.prepare()
	s := 0.0
	for _, row := range g.logProb(X) {
		s += logSumExp(row)
	}
	return s / float64(len(X))
}

func (g *GaussianMixture) NParams() int {
	d := float64(g.d())
	K := float64(g.K)
	var covP float64
	switch g.CovType {
	case "full":
		covP = K * d * (d + 1) / 2
	case "diag":
		covP = K * d
	case "tied":
		covP = d * (d + 1) / 2
	default:
		covP = K
	}
	return int(covP + d*K + K - 1)
}

func (g *GaussianMixture) BIC(X [][]float64) float64 {
	return -2*g.Score(X)*float64(len(X)) + float64(g.NParams())*math.Log(float64(len(X)))
}

func (g *GaussianMixture) AIC(X [][]float64) float64 {
	return -2*g.Score(X)*float64(len(X)) + 2*float64(g.NParams())
}

func (g *GaussianMixture) Predict(X [][]float64) []int {
	_ = g.prepare()
	out := make([]int, len(X))
	for i, row := range g.logProb(X) {
		best, bc := math.Inf(-1), 0
		for k, v := range row {
			if v > best {
				best, bc = v, k
			}
		}
		out[i] = bc
	}
	return out
}
