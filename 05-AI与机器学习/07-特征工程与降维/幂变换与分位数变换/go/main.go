// 幂变换与分位数变换:与 Python 版同题(共用同一套 LCG,数值应一致)。
//
//	go run .
package main

import (
	"fmt"
	"math"
)

// Rng 与 Python 版同一条 LCG,保证两语言数值可对拍。
type Rng struct{ state uint32 }

// NewRng 建一个种子为 seed 的发生器。
func NewRng(seed uint32) *Rng { return &Rng{state: seed} }

// U32 返回下一个 32 位状态。
func (r *Rng) U32() uint32 {
	r.state = 1664525*r.state + 1013904223
	return r.state
}

// Uniform 返回 [0,1) 均匀数。
func (r *Rng) Uniform() float64 { return float64(r.U32()) / 4294967296.0 }

// Normal 用 Box-Muller 生成正态数,一次消耗两个均匀数。
func (r *Rng) Normal() float64 {
	u1 := r.Uniform()
	if u1 == 0 {
		u1 = 1e-12
	}
	u2 := r.Uniform()
	return math.Sqrt(-2*math.Log(u1)) * math.Cos(2*math.Pi*u2)
}

func normalSample(n int, mu, sigma float64, seed uint32) []float64 {
	r := NewRng(seed)
	out := make([]float64, n)
	for i := range out {
		out[i] = mu + sigma*r.Normal()
	}
	return out
}

func lognormalSample(n int, seed uint32) []float64 {
	v := normalSample(n, 0.9, 0.8, seed)
	out := make([]float64, n)
	for i, x := range v {
		out[i] = math.Exp(x) + 0.05
	}
	return out
}

func main() {
	x := lognormalSample(300, 3)
	lamMLE := ArgmaxLLF(x, BoxCoxLLF, -3, 3)
	lamYJ := ArgmaxLLF(x, YeoJohnsonLLF, -5, 5)
	fmt.Println("[E1] 300 个右偏样本")
	fmt.Printf("     Box-Cox MLE        λ = %+.9f(scipy −0.053531489)\n", lamMLE)
	fmt.Printf("     Yeo-Johnson MLE    λ = %+.9f(scipy −0.469904379)\n", lamYJ)
	bc := make([]float64, len(x))
	yj := make([]float64, len(x))
	for i, v := range x {
		bc[i] = BoxCox(v, lamMLE)
		yj[i] = YeoJohnson(v, lamYJ)
	}
	fmt.Printf("     偏度:原 %.4f → Box-Cox %.4f → Yeo-Johnson %.4f\n",
		Skewness(x), Skewness(bc), Skewness(yj))

	// sklearn 文档例子
	data := [][]float64{{1, 2}, {3, 2}, {4, 5}}
	lams := make([]float64, 2)
	for j := 0; j < 2; j++ {
		col := []float64{data[0][j], data[1][j], data[2][j]}
		lams[j] = ArgmaxLLF(col, YeoJohnsonLLF, -5, 5)
	}
	fmt.Printf("[E2] sklearn 文档例 λ:mine=[%.8f, %.8f] sk=[1.38668182, -3.10053331]\n",
		lams[0], lams[1])

	// 负数的处理
	fmt.Printf("[E3] YJ(−3, 0.5) = %.6f(公式 −14/3);Box-Cox 在这里不合法\n",
		YeoJohnson(-3, 0.5))

	// 分位数变换:两条边界规则与两种口径
	col := lognormalSample(60, 7)
	sortedCol := append([]float64{}, col...)
	sortFloats(sortedCol)
	for _, dist := range []string{"uniform", "normal"} {
		qt := &QuantileTransformer{NQuantiles: 20, OutputDist: dist}
		qt.Fit(col)
		probe := []float64{sortedCol[0] / 2, sortedCol[0], sortedCol[len(sortedCol)/2],
			sortedCol[len(sortedCol)-1], sortedCol[len(sortedCol)-1] * 10}
		got := qt.TransformCol(probe)
		fmt.Printf("[E4] %-8s 探针 → ", dist)
		for _, v := range got {
			fmt.Printf("%.6f ", v)
		}
		fmt.Println()
	}
	qt5 := &QuantileTransformer{NQuantiles: 5}
	qt5.Fit(col)
	lm := qt5.TransformCol(qt5.Quantiles)
	fmt.Printf("[E4] n_quantiles=5 的地标映射 = ")
	for _, v := range lm {
		fmt.Printf("%.6f ", v)
	}
	fmt.Printf("\n     q=10 两口径:linear=%.6f  averaged_inverted_cdf=%.6f\n",
		PercentileLinear(sortedCol, 10), PercentileAvgInvertedCDF(sortedCol, 10))

	// 线性相关:normal 输出会压低相关
	a := normalSample(400, 0, 1, 11)
	nz := normalSample(400, 0, 0.5, 12)
	b := make([]float64, 400)
	for i := range a {
		b[i] = 0.9*a[i] + nz[i]
	}
	fmt.Printf("[E5] 原始相关 %.4f\n", Pearson(a, b))
	for _, nq := range []int{200, 50, 10, 5} {
		qu := &QuantileTransformer{NQuantiles: nq, OutputDist: "uniform"}
		qu.Fit(a)
		qub := &QuantileTransformer{NQuantiles: nq, OutputDist: "uniform"}
		qub.Fit(b)
		qn := &QuantileTransformer{NQuantiles: nq, OutputDist: "normal"}
		qn.Fit(a)
		qnb := &QuantileTransformer{NQuantiles: nq, OutputDist: "normal"}
		qnb.Fit(b)
		fmt.Printf("     nq=%4d uniform %.4f  normal %.4f\n", nq,
			Pearson(qu.TransformCol(a), qub.TransformCol(b)),
			Pearson(qn.TransformCol(a), qnb.TransformCol(b)))
	}

	// 平台(重复值)在两个方向取平均后才落到中点
	tied := make([]float64, 30)
	for i := range tied {
		tied[i] = float64(1 + i/10)
	}
	qtt := &QuantileTransformer{NQuantiles: 12, QuantileMode: "averaged_inverted_cdf"}
	qtt.Fit(tied)
	gt := qtt.TransformCol(tied)
	fmt.Printf("[E6] 三台阶映射:%.6f %.6f %.6f\n", gt[0], gt[15], gt[25])
	fmt.Println("done")
}
