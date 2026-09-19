// LDA / QDA 自检(Go 侧):与 python/lda_qda_check.py 同一组结论,交叉验证两语言实现一致。
package main

import (
	"fmt"
	"math"
	"os"
)

var total, passed int
var fails []string

func check(name string, cond bool, detail string) {
	total++
	if cond {
		passed++
		fmt.Printf("  [PASS] %s  %s\n", name, detail)
	} else {
		fails = append(fails, name)
		fmt.Printf("  [FAIL] %s  %s\n", name, detail)
	}
}

func make3(seed uint64, n int) ([][]float64, []int) {
	r := newRNG(seed)
	means := [][]float64{{0, 0}, {3, 2}, {-2, 3}}
	X, y := [][]float64{}, []int{}
	for c, m := range means {
		for i := 0; i < n; i++ {
			X = append(X, []float64{m[0] + 0.6*r.gauss(), m[1] + 0.6*r.gauss()})
			y = append(y, c)
		}
	}
	return X, y
}

func makeHetero(seed uint64, n int) ([][]float64, []int) {
	r := newRNG(seed)
	X, y := [][]float64{}, []int{}
	for i := 0; i < n; i++ {
		X = append(X, []float64{0.4 * r.gauss(), 0.4 * r.gauss()})
		y = append(y, 0)
	}
	for i := 0; i < n; i++ {
		X = append(X, []float64{3.0 * r.gauss(), 0.25 * r.gauss()})
		y = append(y, 1)
	}
	return X, y
}

func acc(a, b []int) float64 {
	c := 0
	for i := range a {
		if a[i] == b[i] {
			c++
		}
	}
	return float64(c) / float64(len(a))
}

func main() {
	fmt.Println("LDA / QDA 自检(Go)")
	fmt.Println("==========================================================================")
	X, y := make3(11, 60)

	// A. 两种写法只差常数
	m := NewLDA(0, 0)
	if err := m.Fit(X, y); err != nil {
		fmt.Println("fit error:", err)
		os.Exit(1)
	}
	q := &QDA{}
	q.Fit(X, y)
	q.Cov = make([][][]float64, len(m.Classes))
	for k := range q.Cov {
		q.Cov[k] = m.Cov // 强行共享协方差 → 与 LDA 同模型
	}
	q.Means, q.Priors = m.Means, m.Priors
	df, raw := m.DecisionFunction(X), q.LogPosterior(X)
	okA := true
	for i := range X {
		mn, mx := math.Inf(1), math.Inf(-1)
		for k := range m.Classes {
			v := raw[i][k] - df[i][k]
			if v < mn {
				mn = v
			}
			if v > mx {
				mx = v
			}
		}
		if mx-mn > 1e-9 {
			okA = false
		}
	}
	check("A1 log 后验与 ωᵀx+ω0 只差一个与类别无关的常数", okA, "")
	Sk := cholSolve(m.chol, m.Means[1])
	back := make([]float64, 2)
	for a := 0; a < 2; a++ {
		for j := 0; j < 2; j++ {
			back[a] += m.Cov[a][j] * Sk[j]
		}
	}
	check("A2 ω_k = Σ⁻¹μ_k", math.Abs(back[0]-m.Means[1][0]) < 1e-10 &&
		math.Abs(back[1]-m.Means[1][1]) < 1e-10, "")

	// B. 共享协方差下两者都高精度
	q2 := &QDA{Reg: 1e-6}
	q2.Fit(X, y)
	check("B1 LDA 与 QDA 在共享协方差数据上精度都很高",
		acc(y, m.Predict(X)) > 0.95 && acc(y, q2.Predict(X)) > 0.95,
		fmt.Sprintf("LDA=%.3f QDA=%.3f", acc(y, m.Predict(X)), acc(y, q2.Predict(X))))

	// C. 对角 QDA ⇔ GaussianNB
	qd := &QDA{Diag: true, Reg: 0}
	qd.Fit(X, y)
	nb := &GaussianNB{}
	nb.Fit(X, y)
	for k := range nb.Classes {
		for j := 0; j < 2; j++ {
			nb.Vars[k][j] = qd.Cov[k][j][j]
		}
	}
	check("C1 逐样本预测完全相同", acc(qd.Predict(X), nb.Predict(X)) == 1.0, "")
	A, B := qd.LogPosterior(X), nb.JointLogLikelihood(X)
	mnC, mxC := math.Inf(1), math.Inf(-1)
	for i := range X {
		for k := range nb.Classes {
			v := A[i][k] - B[i][k]
			if v < mnC {
				mnC = v
			}
			if v > mxC {
				mxC = v
			}
		}
	}
	cst := 0.5 * float64(len(X[0])) * math.Log(2*math.Pi)
	check("C2 两者只差常数 ½·d·log2π", mxC-mnC < 1e-9 && math.Abs(mnC-cst) < 1e-9,
		fmt.Sprintf("%.9f vs %.9f", mnC, cst))

	// D. 降维上限 K−1
	r := newRNG(5)
	X5, y3 := [][]float64{}, []int{}
	for c, mv := range [][]float64{{0, 0, 0, 0, 0}, {2, 0, 1, 0, 0}, {0, 3, 0, 0, 0}} {
		for i := 0; i < 50; i++ {
			row := make([]float64, 5)
			for j := 0; j < 5; j++ {
				row[j] = mv[j] + 0.3*r.gauss()
			}
			X5 = append(X5, row)
			y3 = append(y3, c)
		}
	}
	m5 := NewLDA(0, 0)
	m5.Fit(X5, y3)
	check("D1 K=3 时只有 2 个判别分量", len(m5.Dirs) == 2, fmt.Sprintf("%d", len(m5.Dirs)))
	Z := m5.Transform(X5)
	check("D2 transform 后是 2 维", len(Z[0]) == 2, "")

	// E. shrinkage 口径
	cov := [][]float64{{4, 1}, {1, 2}}
	s0 := shrunkCovariance(cov, 0.0)
	check("E1 γ=0 → 原协方差", math.Abs(s0[0][1]-1) < 1e-12 && math.Abs(s0[0][0]-4) < 1e-12, "")
	s1 := shrunkCovariance(cov, 1.0)
	check("E2 γ=1 → 平均方差 × 单位阵", math.Abs(s1[0][1]) < 1e-12 &&
		math.Abs(s1[0][0]-3) < 1e-12 && math.Abs(s1[1][1]-3) < 1e-12,
		fmt.Sprintf("diag=(%.3f, %.3f)", s1[0][0], s1[1][1]))

	// F. LDA 方向 vs PCA 方向
	r2 := newRNG(33)
	Xf, yf := [][]float64{}, []int{}
	for c, shift := range []float64{-1.2, 1.2} {
		for i := 0; i < 80; i++ {
			Xf = append(Xf, []float64{4.0 * r2.gauss(), shift + 0.3*r2.gauss()})
			yf = append(yf, c)
		}
	}
	mf := NewLDA(0, 0)
	mf.Fit(Xf, yf)
	n := len(Xf)
	mu := []float64{0, 0}
	for _, x := range Xf {
		mu[0] += x[0]
		mu[1] += x[1]
	}
	mu[0] /= float64(n)
	mu[1] /= float64(n)
	cb := [][]float64{{0, 0}, {0, 0}}
	for _, x := range Xf {
		for a := 0; a < 2; a++ {
			for b := 0; b < 2; b++ {
				cb[a][b] += (x[a] - mu[a]) * (x[b] - mu[b]) / float64(n)
			}
		}
	}
	_, pv := powerEig(cb, 3)
	lv := mf.Dirs[0]
	check("F1 PCA 第一主成分几乎沿 x1(方差最大)", math.Abs(pv[0]) > 0.98,
		fmt.Sprintf("(%.3f, %.3f)", pv[0], pv[1]))
	check("F2 LDA 判别方向几乎沿 x2(类间差异)", math.Abs(lv[1]) > 0.98,
		fmt.Sprintf("(%.3f, %.3f)", lv[0], lv[1]))
	projAcc := func(v []float64) float64 {
		z := make([]float64, n)
		for i, x := range Xf {
			z[i] = v[0]*x[0] + v[1]*x[1]
		}
		s0, s1, c0, c1 := 0.0, 0.0, 0, 0
		for i := range z {
			if yf[i] == 0 {
				s0 += z[i]
				c0++
			} else {
				s1 += z[i]
				c1++
			}
		}
		thr := (s0/float64(c0) + s1/float64(c1)) / 2
		hit := 0
		for i := range z {
			if (z[i] < thr) == (yf[i] == 0) {
				hit++
			}
		}
		return float64(hit) / float64(n)
	}
	check("F3 沿 LDA 方向投影后完全可分", math.Abs(projAcc(lv)-1) < 1e-12,
		fmt.Sprintf("%.3f", projAcc(lv)))
	check("F4 沿 PCA 方向投影后几乎分不开", projAcc(pv) < 0.65, fmt.Sprintf("%.3f", projAcc(pv)))

	// G. 异方差:QDA 更灵活
	Xh, yh := makeHetero(17, 80)
	lh, qh := NewLDA(0, 0), &QDA{Reg: 1e-6}
	lh.Fit(Xh, yh)
	qh.Fit(Xh, yh)
	al, aq := acc(yh, lh.Predict(Xh)), acc(yh, qh.Predict(Xh))
	check("G1 异方差数据上 QDA 精度不低于 LDA", aq >= al-1e-12,
		fmt.Sprintf("LDA=%.3f QDA=%.3f", al, aq))

	fmt.Println("--------------------------------------------------------------------------")
	fmt.Printf("断言 %d/%d 通过\n", passed, total)
	if len(fails) > 0 {
		fmt.Println("失败项:", fails)
		os.Exit(1)
	}
	fmt.Println("全部通过")
}
