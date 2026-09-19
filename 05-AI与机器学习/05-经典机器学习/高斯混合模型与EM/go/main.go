// GMM/EM 自检(Go 侧):与 python/gmm_check.py 同一组结论,交叉验证两语言实现一致。
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

func blobs(n int, seed uint64) [][]float64 {
	r := NewRNG(seed)
	X := make([][]float64, 0, 2*n)
	for i := 0; i < n; i++ {
		X = append(X, []float64{r.Next() - 0.5 - 3.0, r.Next() - 0.5})
	}
	for i := 0; i < n; i++ {
		X = append(X, []float64{r.Next() - 0.5 + 3.0, r.Next() - 0.5 + 2.0})
	}
	return X
}

// ari 调整兰德指数:比较两个划分是否等价(簇标签可置换)。
func ari(a, b []int) float64 {
	n := len(a)
	ma, mb := 0, 0
	for i := range a {
		if a[i] > ma {
			ma = a[i]
		}
		if b[i] > mb {
			mb = b[i]
		}
	}
	sa, sb := make([]int, ma+1), make([]int, mb+1)
	tab := map[[2]int]int{}
	for i := range a {
		sa[a[i]]++
		sb[b[i]]++
		tab[[2]int{a[i], b[i]}]++
	}
	c := func(v int) float64 { return float64(v) * float64(v-1) / 2 }
	sij, xa, xb := 0.0, 0.0, 0.0
	for _, v := range tab {
		sij += c(v)
	}
	for _, v := range sa {
		xa += c(v)
	}
	for _, v := range sb {
		xb += c(v)
	}
	exp := xa * xb / c(n)
	mxp := (xa + xb) / 2
	if math.Abs(mxp-exp) < 1e-12 {
		return 1.0
	}
	return (sij - exp) / (mxp - exp)
}

func main() {
	fmt.Println("GMM / EM 自检(Go)")
	fmt.Println("==========================================================================")

	// A. log 域
	v := []float64{-1000.0, -1001.0, -999.5}
	naive := 0.0
	for _, x := range v {
		naive += math.Exp(x)
	}
	check("A1 朴素 exp 求和在这些量级上全部下溢为 0", naive == 0.0, fmt.Sprintf("naive=%v", naive))
	check("A2 logsumexp 得到有限值", !math.IsInf(logSumExp(v), 0), fmt.Sprintf("%.4f", logSumExp(v)))

	// B. Cholesky / logdet / 马氏距离
	A := [][]float64{{4, 1}, {1, 3}}
	L, err := cholesky(A)
	check("B1 Cholesky 分解成功", err == nil, "")
	rec := [][]float64{{0, 0}, {0, 0}}
	for i := 0; i < 2; i++ {
		for j := 0; j < 2; j++ {
			for k := 0; k < 2; k++ {
				rec[i][j] += L[i][k] * L[j][k]
			}
		}
	}
	check("B2 L·Lᵀ 还原 A", math.Abs(rec[0][0]-4) < 1e-12 && math.Abs(rec[1][1]-3) < 1e-12 &&
		math.Abs(rec[0][1]-1) < 1e-12, fmt.Sprintf("rec=%v", rec))
	det := A[0][0]*A[1][1] - A[0][1]*A[1][0]
	check("B3 log|Σ| 与 2×2 行列式一致", math.Abs(logDetChol(L)-math.Log(det)) < 1e-12,
		fmt.Sprintf("%.6f vs %.6f", logDetChol(L), math.Log(det)))
	dv := []float64{1.0, -2.0}
	inv := [][]float64{{A[1][1] / det, -A[0][1] / det}, {-A[1][0] / det, A[0][0] / det}}
	ref := 0.0
	for i := 0; i < 2; i++ {
		for j := 0; j < 2; j++ {
			ref += dv[i] * inv[i][j] * dv[j]
		}
	}
	check("B4 马氏距离与显式求逆一致", math.Abs(mahalSqChol(L, dv)-ref) < 1e-10,
		fmt.Sprintf("%.8f vs %.8f", mahalSqChol(L, dv), ref))
	Ls := [][]float64{{2.0, 0.0}, {0.0, 5.0}}
	check("B5 对角协方差退化为 Σd²/σ²", math.Abs(mahalSqChol(Ls, []float64{1, 1})-(0.25+0.04)) < 1e-12, "")

	// C. E 步归一 + EM 单调
	X := blobs(60, 3)
	g := NewGMM(2, "full", 1)
	if err := g.Fit(X); err != nil {
		fmt.Println("fit error:", err)
		os.Exit(1)
	}
	resp, _ := g.eStep(X)
	okSum := true
	for _, r := range resp {
		s := 0.0
		for _, x := range r {
			s += x
		}
		if math.Abs(s-1) > 1e-9 {
			okSum = false
		}
	}
	check("C1 responsibilities 每行和为 1", okSum, "")
	mono := true
	for i := 1; i < len(g.Trace); i++ {
		if g.Trace[i] < g.Trace[i-1]-1e-9 {
			mono = false
		}
	}
	check("C2 对数似然单调不降", mono, fmt.Sprintf("%d 轮 %.3f → %.3f",
		len(g.Trace), g.Trace[0], g.Trace[len(g.Trace)-1]))

	// D. 参数计数与 BIC/AIC
	expect := map[string]int{"full": 11, "tied": 8, "diag": 9, "spherical": 7}
	for _, t := range []string{"full", "tied", "diag", "spherical"} {
		m := NewGMM(2, t, 1)
		m.Fit(X)
		check("D-"+t+" 自由参数数", m.NParams() == expect[t],
			fmt.Sprintf("got=%d want=%d", m.NParams(), expect[t]))
	}
	check("D-BIC/AIC 关系:BIC − AIC == p·(ln n − 2)",
		math.Abs((g.BIC(X)-g.AIC(X))-float64(g.NParams())*(math.Log(float64(len(X)))-2)) < 1e-9,
		fmt.Sprintf("p=%d n=%d", g.NParams(), len(X)))

	// E. 奇异性:reg_covar=0 会抛错
	Xdup := make([][]float64, 0, 40)
	for i := 0; i < 20; i++ {
		Xdup = append(Xdup, []float64{1, 2})
	}
	for i := 0; i < 20; i++ {
		Xdup = append(Xdup, []float64{3, 4})
	}
	gs := NewGMM(2, "full", 1)
	gs.RegCovar = 0.0
	e0 := gs.Fit(Xdup)
	check("E1 reg_covar=0 时零方差成分让分解失败(似然发散)", e0 != nil, "")
	gs2 := NewGMM(2, "full", 1)
	check("E2 reg_covar=1e-6(默认)压住奇异性", gs2.Fit(Xdup) == nil, "")

	// F. M 步闭式解
	Xh := [][]float64{{0}, {1}, {2}, {3}}
	mf := NewGMM(2, "diag", 0)
	mf.Means = [][]float64{{0}, {3}}
	mf.Cov = [][]float64{{1}, {1}}
	w := [][]float64{{0.9, 0.1}, {0.8, 0.2}, {0.2, 0.8}, {0.1, 0.9}}
	mf.mStep(Xh, w)
	num, den := 0.0, 0.0
	for i := range Xh {
		num += w[i][0] * Xh[i][0]
		den += w[i][0]
	}
	check("F1 μ_k = Σγx / Σγ", math.Abs(mf.Means[0][0]-num/den) < 1e-12,
		fmt.Sprintf("%.6f vs %.6f", mf.Means[0][0], num/den))
	check("F2 π_k = N_k / N 且 Σπ = 1",
		math.Abs(mf.Weights[0]+mf.Weights[1]-1) < 1e-12 && math.Abs(mf.Weights[0]-den/4) < 1e-12, "")

	// G. GMM ⊃ k-means
	Xk := blobs(50, 11)
	mk := NewGMM(2, "spherical", 2)
	mk.Fit(Xk)
	_, kl := KMeans(Xk, 2, 2)
	a := ari(kl, mk.Predict(Xk))
	check("G1 球形小方差下的硬划分与 k-means 等价(ARI==1)", math.Abs(a-1) < 1e-9,
		fmt.Sprintf("ARI=%.6f", a))

	// H. BIC 选成分数
	Xb := blobs(80, 21)
	best, bestV := 0, math.Inf(1)
	for _, K := range []int{1, 2, 3, 4} {
		m := NewGMM(K, "full", 7)
		m.Fit(Xb)
		b := m.BIC(Xb)
		if b < bestV {
			best, bestV = K, b
		}
	}
	check("H1 两簇数据的 BIC 最小值出现在 K=2", best == 2, fmt.Sprintf("K=%d", best))

	fmt.Println("--------------------------------------------------------------------------")
	fmt.Printf("断言 %d/%d 通过\n", passed, total)
	if len(fails) > 0 {
		fmt.Println("失败项:", fails)
		os.Exit(1)
	}
	fmt.Println("全部通过")
}
