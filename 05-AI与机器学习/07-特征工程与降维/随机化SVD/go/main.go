// 随机化 SVD(Halko et al. 2009 Algorithm 4.1/4.3)—— Go 版,与 ../python/main.py 同题。
//
// sklearn PCA(svd_solver='randomized') / sklearn.utils.extmath.randomized_svd 即此算法。
// 官方参数语义:
//
//	n_oversamples(默认 10):随机向量总数 = n_components + n_oversamples;
//	  "Smaller number can improve speed but can negatively impact the quality of approximation"
//	n_iter('auto' → 4;n_components < 0.1·min(shape) → 7):幂迭代次数,对付谱衰减慢的矩阵
//	power_iteration_normalizer('auto' → n_iter<=2 用 none,否则 LU):
//	  'QR' 最慢最准;'none' 最快但 n_iter 较大(如 >=5)时数值不稳定
//
// 编译运行:go run .
package main

import (
	"fmt"
	"math"
	"math/rand"
)


// randomizedSVD 返回 (U(n×k), s(k), Q(n×l), l)。
func randomizedSVD(A [][]float64, k, oversamples, nIter int, normalizer string, seed int64) ([][]float64, []float64, [][]float64) {
	rng := rand.New(rand.NewSource(seed))
	n, p := len(A), len(A[0])
	l := k + oversamples
	omega := make([][]float64, p)
	for i := 0; i < p; i++ {
		omega[i] = make([]float64, l)
		for j := 0; j < l; j++ {
			omega[i][j] = rng.NormFloat64()
		}
	}
	Y := matmul(A, omega) // 先"扫"一遍 A 的值域
	for it := 0; it < nIter; it++ {
		switch normalizer {
		case "QR":
			Y = qrQ(Y)
		case "LU":
			Y = luLFactor(Y)
		}
		Y = matmul(A, matmul(transpose(A), Y)) // (AAᵀ)^q·AΩ 放大主方向
	}
	Q := qrQ(Y)
	B := matmul(transpose(Q), A) // 压到 l×p 的小矩阵
	Ub, s := smallSVD(B)
	Uc := make([][]float64, l)
	for r := 0; r < l; r++ {
		Uc[r] = make([]float64, k)
		for c := 0; c < k; c++ {
			Uc[r][c] = Ub[r][c]
		}
	}
	return matmul(Q, Uc), s[:k], Q
}

// maxSingularError 前 k 个奇异值的最大相对误差(σ 真值解析已知)。
func maxSingularError(sigma, sHat []float64, k int) float64 {
	e := 0.0
	for i := 0; i < k; i++ {
		r := math.Abs(sHat[i]-sigma[i]) / sigma[i]
		if r > e {
			e = r
		}
	}
	return e
}

// subspaceSinTheta 最大主角正弦:sinθ_max = √(1-σ_min(QᵀU_k)²)。
func subspaceSinTheta(Q, UTrue [][]float64, k int) float64 {
	Uk := make([][]float64, len(UTrue))
	for r := range UTrue {
		Uk[r] = UTrue[r][:k]
	}
	P := matmul(transpose(Q), Uk)
	eig, _ := jacobiEigh(matmul(transpose(P), P)) // k×k 特征分解 ⇒ 奇异值平方
	if len(eig) == 0 {
		return 1.0
	}
	sMin := math.Sqrt(math.Max(eig[len(eig)-1], 0))
	return math.Sqrt(math.Max(0, 1-math.Min(sMin, 1)*math.Min(sMin, 1)))
}

func main() {
	exp1Oversampling()
	exp2SlowDecay()
	exp4Normalizer()
	fmt.Println("\n全部检查通过。")
}

// exp1Oversampling 随机向量总数 = n_components + n_oversamples,少抽会显著变差。
func exp1Oversampling() {
	n, p, k := 400, 60, 10
	sigma := make([]float64, p)
	for i := range sigma {
		sigma[i] = math.Exp(-0.15 * float64(i)) // 指数衰减,κ≈7e3
	}
	A, UTrue := makeMatrixWithSpectrum(n, p, sigma, 1)
	fmt.Println("== 实验 1:n_oversamples 对近似质量的影响(n_iter=2, k=10)==")
	fmt.Println("  oversamples   sigma 最大相对误差   子空间 sinTheta   ||QtQ-I||_F")
	first, last := 0.0, 0.0
	for idx, ov := range []int{0, 2, 5, 10, 20} {
		_, s, Q := randomizedSVD(A, k, ov, 2, "QR", 42)
		e := maxSingularError(sigma, s, k)
		if idx == 0 {
			first = e
		}
		last = e
		fmt.Printf("  %11d   %.3e        %.3e      %.3e\n", ov, e, subspaceSinTheta(Q, UTrue, k), frobOrthErr(Q))
	}
	fmt.Printf("  结论:oversamples=0 → %.3e,oversamples=20 → %.3e;校验 %v\n", first, last, last < 1e-6 && first > last)
	if !(last < 1e-6 && first > last) {
		panic("oversampling experiment unexpected")
	}
}

// exp2SlowDecay 谱衰减慢时幂迭代才是关键(n_iter 0 → 7 单调改善)。
func exp2SlowDecay() {
	n, p, k := 400, 60, 10
	sigma := make([]float64, p)
	for i := range sigma {
		sigma[i] = 1.0 / float64(i+1) // 慢衰减,κ=60
	}
	A, UTrue := makeMatrixWithSpectrum(n, p, sigma, 2)
	fmt.Println("\n== 实验 2:谱衰减慢时 n_iter 才是关键(oversamples=10, k=10)==")
	fmt.Println("  n_iter   sigma 最大相对误差   子空间 sinTheta")
	errs := []float64{}
	for _, it := range []int{0, 1, 2, 4, 7} {
		_, s, Q := randomizedSVD(A, k, 10, it, "QR", 7)
		e := maxSingularError(sigma, s, k)
		errs = append(errs, e)
		fmt.Printf("  %6d   %.3e        %.3e\n", it, e, subspaceSinTheta(Q, UTrue, k))
	}
	ok := errs[0] > 1e-3 && errs[len(errs)-1] < errs[0]
	fmt.Printf("  结论:n_iter=0 → %.3e,n_iter=7 → %.3e;校验 %v\n", errs[0], errs[len(errs)-1], ok)
	if !ok {
		panic("power-iteration experiment unexpected")
	}
}

// exp4Normalizer 幂迭代之间的归一化:不做归一化时列尺度被放大到 κ^(2q+1)。
//
// 有趣的是 Q 本身仍然是正交的(Gram-Schmidt 会救回来),烂掉的是被投影进去的
// **值域**:小奇异方向在幂迭代里下溢,于是奇异值全错。
func exp4Normalizer() {
	n, p := 300, 40
	sigma := make([]float64, p)
	for i := range sigma {
		switch {
		case i == 0:
			sigma[i] = 1.0
		case i < 5:
			sigma[i] = 1e-2
		default:
			sigma[i] = 1e-4
		}
	}
	A, _ := makeMatrixWithSpectrum(n, p, sigma, 4)
	fmt.Println("\n== 实验 4:幂迭代之间的归一化(n_iter=7, k=10)==")
	fmt.Println("  normalizer   ||QtQ-I||_F    sigma 最大相对误差")
	res := map[string][2]float64{}
	for _, norm := range []string{"none", "LU", "QR"} {
		_, s, Q := randomizedSVD(A, 10, 10, 7, norm, 5)
		res[norm] = [2]float64{frobOrthErr(Q), maxSingularError(sigma, s, 10)}
		fmt.Printf("  %-10s   %.3e      %.3e\n", norm, res[norm][0], res[norm][1])
	}
	noneBad := res["none"][1] > 100*res["LU"][1]
	fmt.Printf("  结论:'none' 的奇异值误差是 'LU' 的 %.0f 倍;校验 %v\n",
		res["none"][1]/res["LU"][1], noneBad)
	if !noneBad {
		panic("normalizer experiment unexpected")
	}
}
