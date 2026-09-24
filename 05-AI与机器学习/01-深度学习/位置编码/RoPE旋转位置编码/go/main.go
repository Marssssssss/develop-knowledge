package main

import (
	"fmt"
	"math"
)

func main() {
	base, dim := 10000.0, 8
	inv := DefaultInvFreq(base, dim)
	fmt.Println("== default inv_freq (base=10000, dim=8) ==")
	for _, v := range inv {
		fmt.Printf("  %.6g (波长 %.4g)\n", v, 2*math.Pi/v)
	}

	fmt.Println("\n== rotate_half ==")
	x := []float64{1, 2, 3, 4}
	fmt.Println("  x =", x, "→ rotate_half =", RotateHalf(x), "→ 两次 =", RotateHalf(RotateHalf(x)))

	fmt.Println("\n== 相对位置：分数只依赖 (n-m) ==")
	q := []float64{0.3, -1.2, 0.7, 2.1, -0.4, 0.9, 1.5, -0.8, 0.2, 0.6, -1.1, 0.4, 1.9, -0.3, 0.5, 0.8}
	k := []float64{-0.6, 0.4, 1.3, 0.1, 2.0, -0.9, 0.3, 1.1, -1.4, 0.7, 0.2, -0.5, 0.8, 1.6, -0.2, 0.9}
	inv16 := DefaultInvFreq(base, 16)
	for _, p := range [][2]int{{0, 4}, {5, 9}, {100, 104}, {0, 5}} {
		fmt.Printf("  (m=%3d, n=%3d) score = %+.9f\n", p[0], p[1], RotatedScore(q, k, inv16, p[0], p[1]))
	}

	fmt.Println("\n== dynamic NTK base（max=8192, factor=4）==")
	for _, sl := range []int{8192, 16384, 32768, 65536} {
		fmt.Printf("  seq_len=%6d → base = %.1f\n", sl, DynamicNtkBase(base, 64, 4.0, sl, 8192))
	}

	fmt.Println("\n== YaRN（dim=64, factor=32, original_max=8192）==")
	yinv, att := YarnInvFreq(base, 64, 32.0, 8192, 0, 0, true)
	d64 := DefaultInvFreq(base, 64)
	fmt.Printf("  attention_factor = %.9f\n", att)
	for _, i := range []int{0, 12, 20, 25, 31} {
		fmt.Printf("  i=%2d default=%.6e yarn=%.6e 比值=%.4f\n", i, d64[i], yinv[i], yinv[i]/d64[i])
	}
	low, high := FindCorrectionRange(32, 1, 64, base, 8192, true)
	fmt.Printf("  correction range (low, high) = (%.0f, %.0f)\n", low, high)

	fmt.Println("\n== llama3（dim=64, factor=8, low=1, high=4, old=8192）==")
	l3 := Llama3InvFreq(base, 64, 8.0, 1.0, 4.0, 8192)
	for _, i := range []int{0, 20, 22, 24, 31} {
		fmt.Printf("  i=%2d 波长=%9.1f default=%.6e llama3=%.6e 比值=%.4f\n",
			i, 2*math.Pi/d64[i], d64[i], l3[i], l3[i]/d64[i])
	}

	fmt.Println("\n== proportional（head_dim=8, proportion=0.5）==")
	fmt.Println("  ", ProportionalInvFreq(base, 8, 1.0, 0.5))
}
