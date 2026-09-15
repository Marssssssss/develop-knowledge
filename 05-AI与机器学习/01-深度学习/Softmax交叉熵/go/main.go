// Softmax + Cross-Entropy 的 Go 实现与自检(与 python/softmax_ce.py 同题)。
//
// 运行:go run .
// 只保留最核心的四件事:max-shift 的 log-sum-exp、p−y 的闭式梯度、
// class weight 下 mean 的除数、以及 label smoothing 的梯度零和性。
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

// logSumExp 两种实现:naive 会在 max logit > ~709 时溢出。
func logSumExp(x []float64, stable bool) float64 {
	m := math.Inf(-1)
	for _, v := range x {
		if v > m {
			m = v
		}
	}
	s := 0.0
	for _, v := range x {
		if stable {
			s += math.Exp(v - m)
		} else {
			s += math.Exp(v)
		}
	}
	if stable {
		return m + math.Log(s)
	}
	return math.Log(s)
}

// crossEntropyMean:targets 为类别索引。denom 为 Σ w_{y_n}(非 N),与 PyTorch 语义一致。
func crossEntropyMean(x [][]float64, targets []int, weight []float64, smooth float64) (loss float64, logp [][]float64) {
	n := len(x)
	denom := 0.0
	loss = 0.0
	logp = make([][]float64, n)
	for i := 0; i < n; i++ {
		c := len(x[i])
		lse := logSumExp(x[i], true)
		lp := make([]float64, c)
		for j := 0; j < c; j++ {
			lp[j] = x[i][j] - lse
		}
		logp[i] = lp
		w := 1.0
		if weight != nil {
			w = weight[targets[i]]
		}
		if smooth > 0 {
			// 平滑目标:(1-ε)·onehot + ε/C,对类别索引 target 的闭式写法
			s := 0.0
			for j := 0; j < c; j++ {
				y := smooth / float64(c)
				if j == targets[i] {
					y += 1 - smooth
				}
				s += -y * lp[j]
			}
			loss += w * s
			denom += w
		} else {
			loss += -w * lp[targets[i]]
			denom += w
		}
	}
	return loss / denom, logp
}

// ceGrad 返回 ∂ℓ/∂x(逐样本,未 reduction)= w_{y}·(p − y)。
func ceGrad(x [][]float64, targets []int, weight []float64, smooth float64) [][]float64 {
	n := len(x)
	g := make([][]float64, n)
	for i := 0; i < n; i++ {
		c := len(x[i])
		lse := logSumExp(x[i], true)
		w := 1.0
		if weight != nil {
			w = weight[targets[i]]
		}
		row := make([]float64, c)
		for j := 0; j < c; j++ {
			p := math.Exp(x[i][j] - lse)
			y := smooth / float64(c)
			if j == targets[i] {
				y += 1 - smooth
			}
			row[j] = w * (p - y)
		}
		g[i] = row
	}
	return g
}

func lossSum(x [][]float64, targets []int, weight []float64) float64 {
	s := 0.0
	for i := range x {
		lse := logSumExp(x[i], true)
		w := 1.0
		if weight != nil {
			w = weight[targets[i]]
		}
		s += -w * (x[i][targets[i]] - lse)
	}
	return s
}

func main() {
	fmt.Println("== A. max-shift 是必需的 ==")
	big := []float64{1062.1, 1000.0, -50.0}
	naive := logSumExp(big, false)
	stable := logSumExp(big, true)
	check("朴素 Σexp 溢出为 +Inf", math.IsInf(naive, 1), fmt.Sprintf("naive=%v", naive))
	check("max-shift 结果有限", !math.IsInf(stable, 0) && !math.IsNaN(stable), fmt.Sprintf("stable=%.6f", stable))

	fmt.Println("\n== B. 闭式梯度 p−y vs 中心差分 ==")
	x := [][]float64{{0.5, -1.2, 2.0}, {-0.3, 0.8, 1.1}}
	t := []int{2, 0}
	g := ceGrad(x, t, nil, 0)
	const step = 1e-6
	worst := 0.0
	for i := range x {
		for j := range x[i] {
			old := x[i][j]
			x[i][j] = old + step
			fp := lossSum(x, t, nil)
			x[i][j] = old - step
			fm := lossSum(x, t, nil)
			x[i][j] = old
			num := (fp - fm) / (2 * step)
			d := math.Abs(num - g[i][j])
			if rel := d / math.Max(1e-12, math.Abs(num)+math.Abs(g[i][j])); rel > worst {
				worst = rel
			}
		}
	}
	check("梯度与差分一致(相对误差 < 1e-6)", worst < 1e-6, fmt.Sprintf("%.3e", worst))
	bounded := true
	for i := range g {
		for _, v := range g[i] {
			if math.Abs(v) > 1.0+1e-12 {
				bounded = false
			}
		}
	}
	check("梯度有界 |p−y| ≤ 1", bounded, "所有分量满足")

	fmt.Println("\n== C. mean 的除数是 Σw_{y_n} ==")
	x2 := [][]float64{{2.0, 0.0}, {0.0, 2.0}}
	t2 := []int{0, 1}
	w2 := []float64{1.0, 3.0}
	meanV, _ := crossEntropyMean(x2, t2, w2, 0)
	sumV := lossSum(x2, t2, w2)
	check("mean = sum/Σw", math.Abs(meanV-sumV/4.0) < 1e-12, fmt.Sprintf("%.6f", meanV))
	check("若按 N 除会差一倍", math.Abs(meanV-sumV/2.0) > 0.1, fmt.Sprintf("差 %.6f", math.Abs(meanV-sumV/2.0)))

	fmt.Println("\n== D. log_softmax vs log(softmax) ==")
	lse := logSumExp([]float64{0.0, -800.0}, true)
	logSoftmax1 := -800.0 - lse  // log_softmax 的第二个分量
	sepLog := math.Log(math.Exp(-800.0 - lse)) // 先 softmax 再取 log
	check("log_softmax 差 800 时有限", math.Abs(logSoftmax1+800.0) < 1e-9, fmt.Sprintf("%.6f", logSoftmax1))
	check("log(softmax) 下溢为 −Inf", math.IsInf(sepLog, -1), fmt.Sprintf("%v", sepLog))

	fmt.Println("\n== E. label smoothing ==")
	x4 := [][]float64{{2.0, 1.0, 0.0}}
	t4 := []int{0}
	b, _ := crossEntropyMean(x4, t4, nil, 0)
	s, _ := crossEntropyMean(x4, t4, nil, 0.1)
	check("平滑使 loss 变大", s > b, fmt.Sprintf("%.6f → %.6f", b, s))
	gs := ceGrad(x4, t4, nil, 0.1)
	zeroSum := math.Abs(gs[0][0] + gs[0][1] + gs[0][2])
	check("平滑后梯度仍零和", zeroSum < 1e-12, fmt.Sprintf("Σ=%.3e", zeroSum))
	lower := 0.1 * math.Log(3)
	check("loss ≥ ε·log C", s >= lower-1e-12, fmt.Sprintf("%.6f ≥ %.6f", s, lower))

	fmt.Println("\n== F. 平移不变 ==")
	xs := [][]float64{{0.4, -1.1, 0.9, 2.0}, {1.0, 0.2, -0.5, 0.3}}
	ts := []int{1, 2}
	a, _ := crossEntropyMean(xs, ts, nil, 0)
	sh := make([][]float64, len(xs))
	for i := range xs {
		sh[i] = make([]float64, len(xs[i]))
		for j := range xs[i] {
			sh[i][j] = xs[i][j] + 1000.0
		}
	}
	c, _ := crossEntropyMean(sh, ts, nil, 0)
	check("全体 +1000 不改变 loss", math.Abs(a-c) < 1e-12, fmt.Sprintf("Δ=%.3e", math.Abs(a-c)))

	fmt.Printf("\n结果:%d/%d 通过\n", pass, total)
	if fail > 0 {
		os.Exit(1)
	}
}
