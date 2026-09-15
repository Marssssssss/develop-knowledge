// PCA 的两条数值路线(Go 版):协方差矩阵特征分解 vs 数据矩阵直接 SVD。
//
// 与 ../python/main.py、../c/main.c 同题。对应 sklearn PCA 的两个精确求解器
// 'covariance_eigh' 与 'full'(参数与语义见 sklearn PCA API 文档)。数学上等价:
// λ_i = σ_i²/(n-1);差别在数值 —— 协方差路线的条件是 SVD 路线的平方。
//
// 编译运行:go run .
package main

import (
	"fmt"
	"math"
	"math/rand"
)

func main() {
	exp1Anchor()
	exp2ConditionNumber()
	exp3CenterNotScale()
	fmt.Println("\n全部检查通过。")
}

// exp1Anchor 对齐 sklearn PCA 文档 docstring:singular_values_ = [6.30061, 0.54980],
// explained_variance_ratio_ = [0.9924, 0.0075](X 用文档里那 6×2 的样例)。
func exp1Anchor() {
	X := [][]float64{{-1, -1}, {-2, -1}, {-3, -2}, {1, 1}, {2, 1}, {3, 2}}
	lam, s, _ := pcaViaSVD(center(X), 2)
	total := lam[0] + lam[1]
	fmt.Println("== 实验 1:对齐 sklearn PCA 文档的数值锚点 ==")
	fmt.Printf("  奇异值       : [%.5f, %.5f]   期望 [6.30061, 0.54980]\n", s[0], s[1])
	fmt.Printf("  方差解释比例 : [%.4f, %.4f]   期望 [0.9924, 0.0075]\n", lam[0]/total, lam[1]/total)
	if math.Abs(s[0]-6.30061) > 1e-5 || math.Abs(s[1]-0.54980) > 1e-5 {
		panic("anchor mismatch")
	}
}

// exp2ConditionNumber 谱跨度 1e12 时两条路线的精度对比。
//
//	直接 SVD  :σ 的绝对精度 ~ eps·σ_max          ⇒ σ_min 相对误差 ~ eps·κ
//	协方差路线:λ 的绝对精度 ~ eps·λ_max,σ=√(λ(n-1)) ⇒ 相对误差 ~ eps·κ²/2
//
// κ=1e12 时后者的误差尺度已远超 1,最小特征值会被压到 0 以下,开方得到 nan。
func exp2ConditionNumber() {
	n, p := 200, 6
	sigma := []float64{1, 1e-2, 1e-4, 1e-8, 1e-10, 1e-12}
	Xc := center(makeMatrixWithSpectrum(n, p, sigma, 7))
	lamCov := pcaViaCovariance(Xc, p)
	_, sSVD, _ := pcaViaSVD(Xc, p)
	kappa := sigma[0] / sigma[p-1]
	fmt.Println("\n== 实验 2:条件数被平方(covariance_eigh 的数值代价)==")
	fmt.Printf("  kappa(Xc)=%.0e -> kappa(C)=k^2=%.0e,而 1/eps ~= %.0e\n", kappa, kappa*kappa, 1/2.22e-16)
	fmt.Println("  i   sigma_真值  sigma_SVD      相对误差   sigma_协方差   相对误差")
	maxSVD, errCovAt3, errSVDAt3, nanCount := 0.0, 0.0, 0.0, 0
	for i := 0; i < p; i++ {
		eSVD := math.Abs(sSVD[i]-sigma[i]) / sigma[i]
		if eSVD > maxSVD {
			maxSVD = eSVD
		}
		if i == 3 {
			errSVDAt3 = eSVD
		}
		if lamCov[i] <= 0 {
			nanCount++
			fmt.Printf("  %d  %.0e     %.6e  %.2e   %-10s    %s\n",
				i, sigma[i], sSVD[i], eSVD, "nan", "<- lambda<0,开方后连数都不是")
			continue
		}
		sCov := math.Sqrt(lamCov[i] * float64(n-1))
		eCov := math.Abs(sCov-sigma[i]) / sigma[i]
		if i == 3 {
			errCovAt3 = eCov
		}
		fmt.Printf("  %d  %.0e     %.6e  %.2e   %.6e   %.2e\n", i, sigma[i], sSVD[i], eSVD, sCov, eCov)
	}
	fmt.Printf("  SVD 路线最大相对误差 %.2e(上限 eps*kappa=%.1e);协方差路线在 sigma=1e-8 上已 %.2e\n",
		maxSVD, 2.22e-16*kappa, errCovAt3)
	fmt.Printf("  结论校验:%v\n", maxSVD < 1e-5 && errCovAt3 > 1e-3 && errCovAt3/errSVDAt3 > 1e6 && nanCount >= 1)
	if !(maxSVD < 1e-5 && errCovAt3 > 1e-3 && errCovAt3/errSVDAt3 > 1e6 && nanCount >= 1) {
		panic("condition-number experiment unexpected")
	}
}

// exp3CenterNotScale sklearn 原文:PCA centers but does not scale the input data。
// 两个特征只有单位不同时,不缩放的第 1 主成分几乎就是大方差特征本身。
func exp3CenterNotScale() {
	rng := rand.New(rand.NewSource(11))
	n := 300
	X := make([][]float64, n)
	Z := make([][]float64, n)
	for r := 0; r < n; r++ {
		a, b := rng.NormFloat64(), rng.NormFloat64()*1000
		X[r] = []float64{a, b}
		Z[r] = []float64{a, b / 1000}
	}
	_, _, V := pcaViaSVD(center(X), 2)
	_, _, Vz := pcaViaSVD(center(Z), 2)
	fmt.Println("\n== 实验 3:只中心化、不缩放 → 主成分被大尺度特征独占 ==")
	fmt.Printf("  不缩放:第 1 主成分 %+.6f·f0 %+.6f·f1\n", V[0][0], V[0][1])
	fmt.Printf("  标准化:第 1 主成分 %+.6f·f0 %+.6f·f1\n", Vz[0][0], Vz[0][1])
	fmt.Println("  → 两特征方差 1 vs 1e6,不缩放时 f0 几乎不出现在第 1 主成分里;" +
		"whiten=True 也救不了:白化只改分量尺度,不改方向")
	if math.Abs(V[0][1]) < 0.999 || math.Abs(Vz[0][0]) < 0.4 || math.Abs(Vz[0][1]) < 0.4 {
		panic("center-not-scale experiment unexpected")
	}
}
