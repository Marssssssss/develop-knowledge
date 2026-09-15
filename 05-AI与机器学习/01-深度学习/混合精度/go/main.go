// 混合精度训练核心机制的 Go 实现与自检(与 python/mixed_precision.py 同题)。
//
// 运行:go run .
// FP16 用**算术方式**模拟(round-to-nearest-even),不依赖硬件的 _Float16;
// 这样在没有 fp16 支持的平台上也能复现同一组数字,并且每个结论都能手算核对。
package main

import (
	"fmt"
	"math"
	"os"
)

const (
	minNormalF16 = 6.103515625e-05    // 2^-14
	stepSubF16   = 5.9604644775390625e-08 // 2^-24,非正规区的固定步长
	maxF16       = 65504.0
	infF16       = 65520.0 // >= 该值按 IEEE 舍入到 inf
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

// roundHalfEven:四舍五入到最近整数,遇到正好 .5 时取偶数(与 IEEE 一致)。
func roundHalfEven(v float64) float64 {
	f := math.Floor(v)
	d := v - f
	switch {
	case d > 0.5:
		return f + 1
	case d < 0.5:
		return f
	case math.Mod(f, 2) == 0:
		return f
	}
	return f + 1
}

// f16Round:float64 -> fp16(1 符号 + 5 指数 + 10 尾数),按 IEEE-754 最近舍入。
// 正规区步长 2^(e-10),非正规区步长固定 2^-24。
func f16Round(x float64) float64 {
	if x == 0 || math.IsNaN(x) {
		return x
	}
	s := 1.0
	if x < 0 {
		s, x = -1, -x
	}
	if x >= infF16 {
		return math.Inf(1) * s
	}
	if x > maxF16 {
		return s * maxF16
	}
	q := stepSubF16
	if x >= minNormalF16 {
		q = math.Pow(2, math.Floor(math.Log2(x))-10)
	}
	return s * roundHalfEven(x/q) * q
}

// bf16Trunc:fp32 -> bf16 只做截断(1 符号 + 8 指数 + 7 尾数),指数范围与 fp32 相同。
func bf16Trunc(x float64) float64 {
	b := math.Float32bits(float32(x))
	return float64(math.Float32frombits(b & 0xFFFF0000))
}

func main() {
	fmt.Println("== A. fp16 的可表示性(算术模拟 vs IEEE 常量) ==")
	check("最小正规数 = 2^-14", f16Round(math.Pow(2, -14)) == math.Pow(2, -14),
		fmt.Sprintf("%.6e", f16Round(math.Pow(2, -14))))
	check("最小非正规数 = 2^-24", f16Round(math.Pow(2, -24)) == math.Pow(2, -24),
		fmt.Sprintf("%.6e", f16Round(math.Pow(2, -24))))
	check("2^-25 归零(半非正规步长)", f16Round(math.Pow(2, -25)) == 0,
		fmt.Sprintf("%v", f16Round(math.Pow(2, -25))))
	check("2^-30 归零", f16Round(math.Pow(2, -30)) == 0, "0")
	check("0.1 舍入到 0.0999755859375", f16Round(0.1) == 0.0999755859375,
		fmt.Sprintf("%.13f", f16Round(0.1)))
	check("最大有限值 65504 可表示", f16Round(maxF16) == maxF16, "65504")
	check("65520 及以上溢出为 inf", math.IsInf(f16Round(65520), 1), "+Inf")

	fmt.Println("\n== B. 归零阈值扫描 ==")
	prevZero := true
	firstNonZero := 0
	for k := -30; k <= -16; k++ {
		v := f16Round(math.Pow(2, float64(k)))
		if v != 0 && prevZero {
			firstNonZero = k
			prevZero = false
		}
	}
	check("第一个可表示值是 2^-24", firstNonZero == -24, fmt.Sprintf("2^%d", firstNonZero))

	fmt.Println("\n== C. loss scaling:冲零计数 ==")
	// 复现 Python 版的对数均匀分布:g = 10^u,u ∈ [-9,-2)
	var seed uint64 = 20260915
	nextU := func() float64 {
		seed = seed*6364136223846793005 + 1442695040888963407
		return float64(seed>>11) / float64(1<<53)
	}
	const n = 20000
	grads := make([]float64, n)
	for i := range grads {
		grads[i] = math.Pow(10, -9+7*nextU())
	}
	countFlushed := func(S float64) (int, bool) {
		flushed, over := 0, false
		for _, g := range grads {
			v := f16Round(g * S)
			if math.IsInf(v, 0) {
				over = true
			} else if v == 0 {
				flushed++
			}
		}
		return flushed, over
	}
	f1, o1 := countFlushed(1.0)
	f18, o18 := countFlushed(math.Pow(2, 18))
	fmt.Printf("  S=1     冲零 %5d/%d (%.2f%%)   溢出=%v\n", f1, n, 100*float64(f1)/n, o1)
	fmt.Printf("  S=2^18  冲零 %5d/%d (%.2f%%)   溢出=%v\n", f18, n, 100*float64(f18)/n, o18)
	check("S=1 时相当比例梯度归零", float64(f1)/n > 0.2, fmt.Sprintf("%.2f%%", 100*float64(f1)/n))
	check("S=2^18 时冲零比例归 0", f18 == 0, "0.00%")
	check("本扫描未溢出(梯度 ≤ 1e-2)", !o1 && !o18, "S·g_max < 65504")

	fmt.Println("\n== D. master weights:更新落在哪一层精度上 ==")
	move := func(steps int, useMaster bool) (float64, float64) {
		if !useMaster {
			w := f16Round(1.0)
			for i := 0; i < steps; i++ {
				w = f16Round(w - 1e-7)
			}
			return w, 1.0 - w
		}
		w := 1.0
		for i := 0; i < steps; i++ {
			w = w - 1e-7 // fp32/fp64 主副本里累加
		}
		return f16Round(w), 1.0 - w
	}
	wh, dh := move(1000, false)
	wm, dm := move(1000, true)
	fmt.Printf("  纯 fp16 : 1000 步后 w = %v,下降 %.3e\n", wh, dh)
	fmt.Printf("  master  : 1000 步后 w(舍入回 fp16) = %.10f,主副本下降 %.3e\n", wm, dm)
	check("纯 fp16 权重 1000 步后完全没动", dh == 0, "下降 0.000e+00")
	check("master 版本确实在移动(量级 1e-4)", dm > 1e-5 && dm < 1.5e-4, fmt.Sprintf("%.3e", dm))
	check("单步更新比 fp16 在 1.0 处的 ulp 小 3 个数量级以上",
		math.Pow(2, -10)/1e-7 > 1e3, "ulp 9.77e-04 vs 步长 1e-7")

	fmt.Println("\n== E. 累加精度:fp16 逐项 vs fp64 ==")
	x := make([]float64, 2000)
	for i := range x {
		x[i] = 0.5 + nextU()
	}
	acc16, acc64 := 0.0, 0.0
	for _, v := range x {
		acc16 = f16Round(acc16 + v)
		acc64 += v
	}
	rel := math.Abs(acc16-acc64) / acc64
	fmt.Printf("  fp64 累加 = %.6f   fp16 逐项累加 = %.6f   相对误差 = %.3e\n", acc64, acc16, rel)
	check("fp16 逐项累加误差 > 1e-3", rel > 1e-3, fmt.Sprintf("%.3e", rel))

	fmt.Println("\n== F. bf16 的范围优势 ==")
	check("bf16 保留 fp32 指数范围(1e-30 不归零)", bf16Trunc(1e-30) != 0,
		fmt.Sprintf("%.3e", bf16Trunc(1e-30)))
	check("fp16 在 1e-30 处直接归零", f16Round(1e-30) == 0, "0")
	// 单个样本会有偶然性,改为在 [1,3) 上扫描取最大绝对误差
	var e16, ebf, worst16 float64
	for i := 1; i <= 20000; i++ {
		x := 1.0 + float64(i)*1e-4
		if d := math.Abs(f16Round(x) - x); d > e16 {
			e16, worst16 = d, x
		}
		if d := math.Abs(bf16Trunc(x) - x); d > ebf {
			ebf = d
		}
	}
	fmt.Printf("  [1,3) 扫描:fp16 最大绝对误差 = %.3e (x=%.4f);bf16 截断 = %.3e\n", e16, worst16, ebf)
	check("fp16 最大绝对误差 < 1e-3(= 2^-10 量级)", e16 < 1e-3, fmt.Sprintf("%.3e", e16))
	check("bf16(截断)误差比 fp16 大 4 倍以上", ebf > 4*e16, fmt.Sprintf("%.3e vs %.3e", ebf, e16))

	fmt.Printf("\n结果:%d/%d 通过\n", pass, total)
	if fail > 0 {
		os.Exit(1)
	}
}
