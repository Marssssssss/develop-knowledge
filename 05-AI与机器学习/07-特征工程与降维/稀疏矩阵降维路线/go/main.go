package main

// demo 515 Go 侧实验台。运行:`go run .`(同包多文件必须用 `go run .`)
// 本机无 Go 工具链,输出数值由 python 侧同构实现给出,这里只做结构与静态审查。

import (
	"fmt"
	"math"
)

func f6(x float64) string { return fmt.Sprintf("%10.6f", x) }

func frob(A Mat) float64 {
	s := 0.0
	for i := range A {
		for j := range A[i] {
			s += A[i][j] * A[i][j]
		}
	}
	return math.Sqrt(s)
}

func meanVec(X Mat) []float64 {
	n, p := len(X), len(X[0])
	out := make([]float64, p)
	for j := 0; j < p; j++ {
		s := 0.0
		for i := 0; i < n; i++ {
			s += X[i][j]
		}
		out[j] = s / float64(n)
	}
	return out
}

// blockCounts 造一个"全列共享大基线"的计数矩阵。
func blockCounts(rows, cols int, baseline float64, r *Rng) Mat {
	X := zeros(rows, cols)
	for i := 0; i < rows; i++ {
		for j := 0; j < cols; j++ {
			X[i][j] = baseline
		}
		for j := 0; j < 5; j++ {
			X[i][(i%5)*5+j] += 4 + 2*absF(r.normal())
		}
		for j := 0; j < cols; j++ {
			if r.uniform() < 0.12 {
				X[i][j] += 3 * absF(r.normal())
			}
		}
	}
	return X
}

func absF(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}

func cosAngle(u, v []float64) float64 {
	nu, nv, s := 0.0, 0.0, 0.0
	for i := range u {
		s += u[i] * v[i]
		nu += u[i] * u[i]
		nv += v[i] * v[i]
	}
	return s / (math.Sqrt(nu) * math.Sqrt(nv))
}

func e1(r *Rng) {
	fmt.Println("E1 不中心化的代价:comp0 就是均值方向(Go 侧同构)")
	X := blockCounts(120, 25, 30.0, r)
	mu := meanVec(X)
	t := &TruncatedSVD{NComponents: 4, NIter: 7, NOversamples: 10,
		PowerIterationNormalizer: "auto"}
	t.FitTransform(X, newRng(0))
	fmt.Printf("   |cos(components_[0], 均值)| = %.10f\n", absF(cosAngle(t.Components[0], mu)))
	fmt.Printf("   不中心化 ratio = %s\n", fmtRatios(t.ExplainedVarianceRatio))

	Xc := zeros(len(X), len(X[0]))
	for i := range X {
		for j := range X[i] {
			Xc[i][j] = X[i][j] - mu[j]
		}
	}
	tc := &TruncatedSVD{NComponents: 4, NIter: 7, NOversamples: 10,
		PowerIterationNormalizer: "auto"}
	tc.FitTransform(Xc, newRng(0))
	fmt.Printf("   中心化   ratio = %s\n", fmtRatios(tc.ExplainedVarianceRatio))
}

func fmtRatios(v []float64) string {
	s := "["
	for i, x := range v {
		if i > 0 {
			s += " "
		}
		s += fmt.Sprintf("%.5f", x)
	}
	return s + "]"
}

func e4(data Mat) {
	fmt.Println("E4 batch_size 会改变结果(近似精度 vs 内存)")
	ref := &IncrementalPCA{NComponents: 3, BatchSize: len(data)}
	ref.Fit(data)
	fmt.Printf("   单块 S = [%.6f %.6f %.6f]\n", ref.SingularValues[0],
		ref.SingularValues[1], ref.SingularValues[2])
	for _, bs := range []int{4, 7, 13, 20, 37} {
		m := &IncrementalPCA{NComponents: 3, BatchSize: bs}
		m.Fit(data)
		dS := 0.0
		for i := range ref.SingularValues {
			d := m.SingularValues[i] - ref.SingularValues[i]
			if absF(d) > dS {
				dS = absF(d)
			}
		}
		dMean := 0.0
		for j := range ref.Mean {
			if absF(m.Mean[j]-ref.Mean[j]) > dMean {
				dMean = absF(m.Mean[j] - ref.Mean[j])
			}
		}
		fmt.Printf("   batch_size=%-4d max|Δmean|=%.3e  max|ΔS|=%.3e\n", bs, dMean, dS)
	}
}

func e5() {
	fmt.Println("E5 增量方差更新在 1e8 偏移下的稳定性")
	r := newRng(9)
	n := 400
	X := zeros(n, 3)
	for i := range X {
		for j := range X[i] {
			X[i][j] = 1.0e8 + r.normal()
		}
	}
	two := make([]float64, 3)
	one := make([]float64, 3)
	for j := 0; j < 3; j++ {
		col := make([]float64, n)
		for i := 0; i < n; i++ {
			col[i] = X[i][j]
		}
		two[j] = variance(col)
		mu := 0.0
		for _, v := range col {
			mu += v
		}
		mu /= float64(n)
		sq := 0.0
		for _, v := range col {
			sq += v * v
		}
		one[j] = sq/float64(n) - mu*mu
	}
	mean := []float64{0, 0, 0}
	varr := []float64{0, 0, 0}
	cnt := 0.0
	for i := 0; i < n; i += 25 {
		mean, varr, _ = incrementalMeanAndVar(copyMat(X[i:i+25]), mean, varr, repeat(cnt, 3))
		cnt += 25
	}
	for j := 0; j < 3; j++ {
		fmt.Printf("   列%d  一趟=%-14.6f 两趟=%-14.6f 增量=%s\n", j, one[j], two[j], f6(varr[j]))
	}
}

func main() {
	data := zeros(60, 6)
	r := newRng(21)
	for i := range data {
		for j := range data[i] {
			data[i][j] = 2.0 + r.normal()*(1.0+0.3*float64(j))
		}
	}
	e1(newRng(11))
	fmt.Println()
	e4(copyMat(data))
	fmt.Println()
	e5()
	fmt.Println()
	fmt.Printf("genBatches(7,3,2) = %v\n", genBatches(7, 3, 2))
	fmt.Printf("genBatches(100,7,3) 块数 = %d\n", len(genBatches(100, 7, 3)))
}
