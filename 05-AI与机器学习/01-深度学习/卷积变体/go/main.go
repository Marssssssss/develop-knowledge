package main

import "fmt"

func main() {
	fmt.Println("== Conv2d 输出尺寸（H_in=32, k=3）==")
	for _, c := range []struct{ pad, dil, st int }{{0, 1, 1}, {1, 1, 1}, {0, 2, 1}, {1, 2, 2}, {2, 3, 3}} {
		fmt.Printf("  pad=%d dil=%d stride=%d → H_out=%d (k_eff=%d)\n",
			c.pad, c.dil, c.st, Conv2dOutput(32, c.pad, c.dil, 3, c.st), EffectiveKernel(3, c.dil))
	}

	fmt.Println("\n== 参数量：标准 vs 深度可分离（C_in=3, C_out=16, k=3）==")
	o, ip, kh, kw, _ := WeightShape(3, 16, 3, 3, 1)
	std := ParamCount(o, ip, kh, kw, true)
	dw, pw := DepthwiseSeparableParams(3, 16, 3, true)
	fmt.Printf("  标准 Conv2d   : %d\n  Depthwise     : %d\n  Pointwise 1×1 : %d\n  DW+PW         : %d（= 标准的 %.1f%%）\n",
		std, dw, pw, dw+pw, 100.0*float64(dw+pw)/float64(std))

	fmt.Println("\n== groups 的连接图（C_in=C_out=4）==")
	for _, g := range []int{1, 2, 4} {
		o, ip, kh, kw, err := WeightShape(4, 4, 3, 3, g)
		if err != nil {
			fmt.Println("  ", err)
			continue
		}
		fmt.Printf("  groups=%d → weight (%d,%d,%d,%d)，每组 %d 进 %d 出，参数 %d\n",
			g, o, ip, kh, kw, 4/g, 4/g, ParamCount(o, ip, kh, kw, true))
	}

	fmt.Println("\n== 转置卷积 output_size 的合法区间（H_in=4, k=3, stride=2, pad=1）==")
	lo := MinOutputFor(4, 2, 1, 1, 3)
	fmt.Printf("  合法区间 [%d, %d]\n", lo, lo+1)
	for size := lo - 1; size <= lo+2; size++ {
		op, err := ResolveOutputPadding(4, size, 2, 1, 1, 3)
		if err != nil {
			fmt.Printf("  output_size=%d → 拒绝：%v\n", size, err)
		} else {
			fmt.Printf("  output_size=%d → output_padding=%d，实际输出=%d\n",
				size, op, ConvTransposeOutput(4, 2, 1, 1, 3, op))
		}
	}

	fmt.Println("\n== 前向手算对照（1×3×3 配 2×2 核 [[1,2],[3,4]]）==")
	x := [][][]float64{{{1, 2, 3}, {4, 5, 6}, {7, 8, 9}}}
	w := [][][][]float64{{{{1, 2}, {3, 4}}}}
	res := Conv2d(x, w, nil, 1, 0, 1, 1, "zeros")
	fmt.Println("  ", res[0][0])
	fmt.Println("  ", res[0][1])

	fmt.Println("\n== groups 支持集探针（C_in=C_out=4, groups=2）==")
	xg := [][][]float64{{{1, 2}, {3, 4}}, {{1, 2}, {3, 4}}, {{1, 2}, {3, 4}}, {{1, 2}, {3, 4}}}
	wg := make([][][][]float64, 4)
	for i := range wg {
		wg[i] = [][][]float64{{{1, 0}, {0, 1}}, {{1, 0}, {0, 1}}}
	}
	lit := SupportChannels(xg, wg, 1, 0, 1, 2, "zeros")
	for c, v := range lit {
		fmt.Printf("  in[%d] → out%v\n", c, v)
	}
}
