// LayerNorm / RMSNorm 的 Go 实现与自检(与 python/layernorm.py 同题同断言口径)。
//
// 运行:go run .
// 说明:Go 版只保留「最容易被写错的那部分」—— 前向统计量、解析反向、
// 以及用中心差分做交叉验证;不重复 Python 侧的深度扫描实验。
package main

import (
	"fmt"
	"math"
	"os"
)

const eps = 1e-5

// mat 是 row-major 的 n×h 矩阵。
type mat struct {
	n, h int
	v    []float64
}

func newMat(n, h int, f func(i, j int) float64) *mat {
	m := &mat{n: n, h: h, v: make([]float64, n*h)}
	for i := 0; i < n; i++ {
		for j := 0; j < h; j++ {
			m.v[i*h+j] = f(i, j)
		}
	}
	return m
}

func (m *mat) at(i, j int) float64 { return m.v[i*m.h+j] }
func (m *mat) set(i, j int, x float64) {
	m.v[i*m.h+j] = x
}

var checkTotal, checkPass, checkFail int

func check(name string, cond bool, detail string) {
	checkTotal++
	if cond {
		checkPass++
		fmt.Printf("  [PASS] %s  %s\n", name, detail)
	} else {
		checkFail++
		fmt.Printf("  [FAIL] %s  %s\n", name, detail)
	}
}

// ---------- LayerNorm:统计量在每行的 h 维特征上,与 batch 无关 ----------

func lnForward(x *mat, gamma, beta []float64) (y *mat, mu, rstd []float64) {
	y = newMat(x.n, x.h, func(i, j int) float64 { return 0 })
	mu = make([]float64, x.n)
	rstd = make([]float64, x.n)
	for i := 0; i < x.n; i++ {
		s := 0.0
		for j := 0; j < x.h; j++ {
			s += x.at(i, j)
		}
		mu[i] = s / float64(x.h)
		q := 0.0
		for j := 0; j < x.h; j++ {
			d := x.at(i, j) - mu[i]
			q += d * d
		}
		v := q/float64(x.h) + eps
		rstd[i] = 1.0 / math.Sqrt(v)
		for j := 0; j < x.h; j++ {
			y.set(i, j, gamma[j]*(x.at(i, j)-mu[i])*rstd[i]+beta[j])
		}
	}
	return
}

// lnBackward 三路:xhat 直通 / var / mu。
func lnBackward(dy, x *mat, mu, rstd, gamma []float64) (dx *mat, dgamma, dbeta []float64) {
	dx = newMat(x.n, x.h, func(i, j int) float64 { return 0 })
	dgamma = make([]float64, x.h)
	dbeta = make([]float64, x.h)
	h := float64(x.h)
	for i := 0; i < x.n; i++ {
		dxhat := make([]float64, x.h)
		dvar, dmu := 0.0, 0.0
		for j := 0; j < x.h; j++ {
			dxhat[j] = dy.at(i, j) * gamma[j]
			dvar += dxhat[j] * (x.at(i, j) - mu[i])
			dmu += dxhat[j] * -rstd[i]
		}
		dvar *= -0.5 * rstd[i] * rstd[i] * rstd[i]
		sumd := 0.0
		for j := 0; j < x.h; j++ {
			sumd += x.at(i, j) - mu[i]
		}
		dmu += dvar * (-2.0 / h) * sumd
		for j := 0; j < x.h; j++ {
			dx.set(i, j, dxhat[j]*rstd[i]+dvar*2.0*(x.at(i, j)-mu[i])/h+dmu/h)
			dgamma[j] += dy.at(i, j) * (x.at(i, j)-mu[i]) * rstd[i]
			dbeta[j] += dy.at(i, j)
		}
	}
	return
}

// ---------- RMSNorm:只除 RMS,不减均值,无 beta ----------

func rmsForward(x *mat, gamma []float64) (y *mat, rms []float64) {
	y = newMat(x.n, x.h, func(i, j int) float64 { return 0 })
	rms = make([]float64, x.n)
	for i := 0; i < x.n; i++ {
		s := 0.0
		for j := 0; j < x.h; j++ {
			s += x.at(i, j) * x.at(i, j)
		}
		rms[i] = math.Sqrt(s/float64(x.h) + eps)
		for j := 0; j < x.h; j++ {
			y.set(i, j, gamma[j]*x.at(i, j)/rms[i])
		}
	}
	return
}

// rmsBackward 注意 γ 出现在分子求和 Σ(γ_i·dy_i·x_i) 里,不是乘以 x_j。
func rmsBackward(dy, x *mat, rms, gamma []float64) (dx *mat, dgamma []float64) {
	dx = newMat(x.n, x.h, func(i, j int) float64 { return 0 })
	dgamma = make([]float64, x.h)
	h := float64(x.h)
	for i := 0; i < x.n; i++ {
		s := 0.0
		for j := 0; j < x.h; j++ {
			s += gamma[j] * dy.at(i, j) * x.at(i, j)
		}
		r3 := rms[i] * rms[i] * rms[i]
		for j := 0; j < x.h; j++ {
			dx.set(i, j, gamma[j]*dy.at(i, j)/rms[i]-x.at(i, j)*s/(h*r3))
			dgamma[j] += dy.at(i, j) * x.at(i, j) / rms[i]
		}
	}
	return
}

// ---------- 中心差分交叉验证 ----------

func lossLN(x, dy *mat, gamma, beta []float64) float64 {
	y, _, _ := lnForward(x, gamma, beta)
	s := 0.0
	for k := range y.v {
		s += y.v[k] * dy.v[k]
	}
	return s
}

func lossRMS(x, dy *mat, gamma []float64) float64 {
	y, _ := rmsForward(x, gamma)
	s := 0.0
	for k := range y.v {
		s += y.v[k] * dy.v[k]
	}
	return s
}

func numGradMat(f func() float64, x *mat) *mat {
	g := newMat(x.n, x.h, func(i, j int) float64 { return 0 })
	const step = 1e-6
	for i := 0; i < x.n; i++ {
		for j := 0; j < x.h; j++ {
			old := x.at(i, j)
			x.set(i, j, old+step)
			fp := f()
			x.set(i, j, old-step)
			fm := f()
			x.set(i, j, old)
			g.set(i, j, (fp-fm)/(2*step))
		}
	}
	return g
}

func relErr(a, b *mat) float64 {
	num, den := 0.0, 1e-12
	for k := range a.v {
		d := math.Abs(a.v[k] - b.v[k])
		if d > num {
			num = d
		}
		den += math.Abs(a.v[k]) + math.Abs(b.v[k])
	}
	return num / den
}

func maxAbsDiff(a, b *mat) float64 {
	m := 0.0
	for k := range a.v {
		if d := math.Abs(a.v[k] - b.v[k]); d > m {
			m = d
		}
	}
	return m
}

func main() {
	const n, h = 4, 9
	x := newMat(n, h, func(i, j int) float64 {
		return math.Sin(float64(i*7+j*3)) * 1.7
	})
	dy := newMat(n, h, func(i, j int) float64 {
		return math.Cos(float64(i*5+j*2)) * 0.9
	})
	gamma := make([]float64, h)
	beta := make([]float64, h)
	for j := 0; j < h; j++ {
		gamma[j] = 1.0 + 0.3*math.Sin(float64(j))
		beta[j] = 0.1 * math.Cos(float64(j))
	}

	fmt.Println("== A. 解析反向 vs 中心差分 ==")
	_, mu, rstd := lnForward(x, gamma, beta)
	dxa, _, _ := lnBackward(dy, x, mu, rstd, gamma)
	dxn := numGradMat(func() float64 { return lossLN(x, dy, gamma, beta) }, x)
	e1 := relErr(dxa, dxn)
	check("LayerNorm dx 相对误差 < 1e-6", e1 < 1e-6, fmt.Sprintf("err=%.3e", e1))

	_, rms := rmsForward(x, gamma)
	dxr, _ := rmsBackward(dy, x, rms, gamma)
	dxrn := numGradMat(func() float64 { return lossRMS(x, dy, gamma) }, x)
	e2 := relErr(dxr, dxrn)
	check("RMSNorm dx 相对误差 < 1e-6", e2 < 1e-6, fmt.Sprintf("err=%.3e", e2))

	fmt.Println("\n== B. 不变量 ==")
	shift := newMat(n, h, func(i, j int) float64 { return x.at(i, j) + 5.0 })
	dy0, _, _ := lnForward(x, gamma, beta)
	dy1, _, _ := lnForward(shift, gamma, beta)
	check("LN 平移不变", maxAbsDiff(dy0, dy1) < 1e-12, fmt.Sprintf("max|dy|=%.3e", maxAbsDiff(dy0, dy1)))
	r0, _ := rmsForward(x, gamma)
	r1, _ := rmsForward(shift, gamma)
	check("RMSNorm 平移不变**不**成立", maxAbsDiff(r0, r1) > 1.0, fmt.Sprintf("max|dy|=%.3e", maxAbsDiff(r0, r1)))

	scaled := newMat(n, h, func(i, j int) float64 { return x.at(i, j) * 1000.0 })
	sy0, _, _ := lnForward(scaled, gamma, beta)
	s0 := maxAbsDiff(dy0, sy0)
	check("LN 缩放不变(残差仅来自 eps)", s0 < 1e-3, fmt.Sprintf("max|dy|=%.3e", s0))

	// batch 独立性:同一行单独跑 vs 放在 batch 尾部
	solo := newMat(1, h, func(i, j int) float64 { return x.at(0, j) })
	sy, _, _ := lnForward(solo, gamma, beta)
	big := newMat(n, h, func(i, j int) float64 {
		if i == n-1 {
			return x.at(0, j)
		}
		return 50.0 * math.Sin(float64(i+j))
	})
	by, _, _ := lnForward(big, gamma, beta)
	gap := 0.0
	for j := 0; j < h; j++ {
		if d := math.Abs(sy.at(0, j) - by.at(n-1, j)); d > gap {
			gap = d
		}
	}
	check("LN 输出与 batch 组成无关", gap == 0.0, fmt.Sprintf("max|dy|=%.3e", gap))

	fmt.Printf("\n结果:%d/%d 通过\n", checkPass, checkTotal)
	if checkFail > 0 {
		os.Exit(1)
	}
}
