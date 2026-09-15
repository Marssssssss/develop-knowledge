// 学习率调度与梯度裁剪的 Go 实现与自检(与 python/schedule.py 同题)。
//
// 运行:go run .
// 只覆盖闭式性质与优化器语义(它们与训练规模无关),warmup 收敛性实验留给 Python 版。
package main

import (
	"fmt"
	"math"
	"os"
)

var total, pass, fail int

func check(name string, cond bool, detail string) {
	total++
	if cond {
		pass++
		fmt.Printf("  [PASS] %s  %s\n", name, detail)
	} else {
		fail++
		fmt.Printf("  [FAIL] %s  %s\n", name, detail)
	}
}

// transformerLR:lrate = d^-0.5 · min(step^-0.5, step·warmup^-1.5) —— 原文公式 (3)。
func transformerLR(step, dModel, warmup int) float64 {
	s := float64(step)
	if s < 1 {
		s = 1
	}
	a := math.Pow(s, -0.5)
	b := s * math.Pow(float64(warmup), -1.5)
	return math.Pow(float64(dModel), -0.5) * math.Min(a, b)
}

// sgdrLR:第 i 个周期长度 T_i = t0·mult^i,每个周期起点把 lr 拉回 etaMax。
func sgdrLR(step, t0, mult int, etaMax, etaMin float64) float64 {
	start, T := 0, t0
	for step >= start+T {
		start += T
		T *= mult
	}
	tCur := float64(step - start)
	return etaMin + 0.5*(etaMax-etaMin)*(1+math.Cos(math.Pi*tCur/float64(T)))
}

// clipByNorm 返回裁剪后的梯度与**裁剪前**的范数(便于打日志)。
func clipByNorm(g []float64, maxNorm float64) ([]float64, float64) {
	n := 0.0
	for _, v := range g {
		n += v * v
	}
	n = math.Sqrt(n)
	if n <= maxNorm {
		return g, n
	}
	out := make([]float64, len(g))
	for i, v := range g {
		out[i] = v * maxNorm / n
	}
	return out, n
}

func clipByValue(g []float64, v float64) []float64 {
	out := make([]float64, len(g))
	for i, x := range g {
		out[i] = math.Max(-v, math.Min(v, x))
	}
	return out
}

func cosine(a, b []float64) float64 {
	na, nb, d := 0.0, 0.0, 0.0
	for i := range a {
		d += a[i] * b[i]
		na += a[i] * a[i]
		nb += b[i] * b[i]
	}
	return d / (math.Sqrt(na)*math.Sqrt(nb) + 1e-30)
}

func norm(a []float64) float64 {
	s := 0.0
	for _, v := range a {
		s += v * v
	}
	return math.Sqrt(s)
}

// adamDecayRatio 跑固定梯度下的 Adam+L2(耦合)/ AdamW(解耦),返回两参数的 θ/θ0。
// 两个参数初值相同、梯度量级差 100 倍 —— 用来暴露耦合衰减的"不等强度"。
func adamDecayRatio(decoupled bool, steps int) (float64, float64) {
	grads := []float64{0.01, 1.0}
	lr, wd, eps := 1e-3, 0.1, 1e-8
	b1, b2 := 0.9, 0.999
	theta := []float64{1.0, 1.0}
	m := []float64{0, 0}
	v := []float64{0, 0}
	for t := 1; t <= steps; t++ {
		for k := 0; k < 2; k++ {
			g := grads[k]
			if !decoupled {
				g += wd * theta[k] // 耦合:衰减混进梯度,随后被 1/sqrt(v) 缩放
			}
			m[k] = b1*m[k] + (1-b1)*g
			v[k] = b2*v[k] + (1-b2)*g*g
			mh := m[k] / (1 - math.Pow(b1, float64(t)))
			vh := v[k] / (1 - math.Pow(b2, float64(t)))
			theta[k] -= lr * mh / (math.Sqrt(vh) + eps)
			if decoupled {
				theta[k] -= lr * wd * theta[k]
			}
		}
	}
	return theta[0], theta[1]
}

func main() {
	fmt.Println("== A. Transformer inverse-sqrt warmup(d=512, warmup=4000) ==")
	peak := math.Pow(512, -0.5) * math.Pow(4000, -0.5)
	got := transformerLR(4000, 512, 4000)
	check("峰值 = d^-0.5·warmup^-0.5", math.Abs(got-peak)/peak < 1e-12, fmt.Sprintf("%.10e", got))
	lhs := math.Pow(4000, -0.5)
	rhs := 4000 * math.Pow(4000, -1.5)
	check("两分支在 step=warmup 处相等", math.Abs(lhs-rhs) < 1e-15, fmt.Sprintf("%.10f", lhs))
	mono := true
	prev := 0.0
	for s := 1; s <= 4000; s += 97 {
		cur := transformerLR(s, 512, 4000)
		if cur <= prev {
			mono = false
		}
		prev = cur
	}
	check("warmup 段单调上升", mono, "采样 42 点")
	check("step=0 被保护为 step=1", transformerLR(0, 512, 4000) == transformerLR(1, 512, 4000), "相等")

	fmt.Println("\n== B. SGDR warm restart ==")
	ok := true
	for _, s := range []int{0, 10, 30, 70} {
		if math.Abs(sgdrLR(s, 10, 2, 0.1, 0) - 0.1) > 1e-15 {
			ok = false
		}
	}
	check("周期起点回到 η_max = 0.1", ok, "0/10/30/70")
	check("周期中点 = η_max/2", math.Abs(sgdrLR(5, 10, 2, 0.1, 0)-0.05) < 1e-15, "0.050000")
	desc := true
	for s := 0; s < 9; s++ {
		if sgdrLR(s+1, 10, 2, 0.1, 0) >= sgdrLR(s, 10, 2, 0.1, 0) {
			desc = false
		}
	}
	check("单周期内单调下降", desc, "10 个点")

	fmt.Println("\n== C. 梯度裁剪 ==")
	g := []float64{10.0, 0.1, -0.2, 0.05}
	gn, pre := clipByNorm(g, 1.0)
	gv := clipByValue(g, 1.0)
	check("按范数裁剪:方向严格不变", math.Abs(cosine(g, gn)-1.0) < 1e-12, fmt.Sprintf("cos=%.12f", cosine(g, gn)))
	check("按范数裁剪:长度 = 阈值", math.Abs(norm(gn)-1.0) < 1e-12, fmt.Sprintf("%.12f", norm(gn)))
	check("按元素裁剪:方向被改变", cosine(g, gv) < 0.999, fmt.Sprintf("cos=%.6f", cosine(g, gv)))
	check("返回值是裁剪前的范数", math.Abs(pre-10.002625) < 1e-5, fmt.Sprintf("%.6f", pre))
	small := []float64{0.3, 0.1}
	keep, _ := clipByNorm(small, 1.0)
	check("范数未超阈值时不动梯度", math.Abs(keep[0]-0.3) < 1e-15, "恒等")

	fmt.Println("\n== D. AdamW vs Adam+L2 ==")
	s0, s1 := adamDecayRatio(true, 500)
	l0, l1 := adamDecayRatio(false, 500)
	check("AdamW 两参数衰减强度相同(比值 = 1)", math.Abs(s0/s1-1.0) < 1e-4, fmt.Sprintf("%.6f", s0/s1))
	check("Adam+L2 被 1/sqrt(v) 差异化", math.Abs(l0/l1-1.0) > 0.05, fmt.Sprintf("%.6f", l0/l1))

	fmt.Printf("\n结果:%d/%d 通过\n", pass, total)
	if fail > 0 {
		os.Exit(1)
	}
}
