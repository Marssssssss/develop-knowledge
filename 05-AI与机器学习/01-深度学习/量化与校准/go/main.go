package main

import "fmt"

func main() {
	fmt.Println("== 对称 vs 仿射（quint8, 数据 0..1）==")
	for _, qs := range []string{"per_tensor_affine", "per_tensor_symmetric"} {
		s, z := CalculateQparams(0.0, 1.0, qs, 0, 255, "quint8", EPS)
		fmt.Printf("  %-28s scale=%.9f zp=%.0f\n", qs, s, z)
	}

	fmt.Println("\n== dtype 档位表（reduce_range 折半）==")
	for _, dt := range []string{"quint8", "qint8", "qint32", "uint16", "int16", "int4"} {
		a0, a1 := CalculateQminQmax(false, 0, 0, dt, false)
		b0, b1 := CalculateQminQmax(false, 0, 0, dt, true)
		fmt.Printf("  %-8s 默认 (%d,%d) / reduce_range (%d,%d)\n", dt, a0, a1, b0, b1)
	}

	fmt.Println("\n== 伪量化误差（scale=1/255）==")
	xs := make([]float64, 64)
	for i := range xs {
		xs[i] = float64(i) * 0.0037
	}
	fq := FakeQuantize(xs, 1.0/255.0, 0, 0, 255)
	worst := 0.0
	for i := range xs {
		d := xs[i] - fq[i]
		if d < 0 {
			d = -d
		}
		if d > worst {
			worst = d
		}
	}
	fmt.Printf("  最大误差 %.9f，理论上限 scale/2 = %.9f\n", worst, 0.5/255.0)

	fmt.Println("\n== observer 的统计口径（-1..1 后接 -3..3）==")
	mm := NewMinMaxObserver()
	mm.Forward([]float64{-1, 1})
	mm.Forward([]float64{-3, 3})
	fmt.Printf("  MinMax          : (%.4f, %.4f)\n", mm.MinVal, mm.MaxVal)
	ma := &MovingAvgObserver{MinMaxObserver: NewMinMaxObserver(), C: 0.5}
	ma.Forward([]float64{-1, 1})
	ma.Forward([]float64{-3, 3})
	fmt.Printf("  MovingAvg c=0.5 : (%.4f, %.4f)\n", ma.MinVal, ma.MaxVal)

	fmt.Println("\n== 逐通道量化（通道量程差 5 倍）==")
	for _, ch := range [][]float64{{-1, 1}, {-0.2, 0.2}} {
		s, z := CalculateQparams(MinOf(ch), MaxOf(ch), "per_channel_affine", 0, 255, "quint8", EPS)
		fmt.Printf("  scale=%.9f zp=%.0f\n", s, z)
	}

	fmt.Println("\n== GetNorm：density·(end³−begin³)/3 ==")
	fmt.Printf("  (−1,1,2.5) = %.9f\n", GetNorm(-1, 1, 2.5))
	fmt.Printf("  (0,1,2.5)  = %.9f（前者是其 2 倍）\n", GetNorm(0, 1, 2.5))

	fmt.Println("\n== 直方图非线性搜索（bins=64, dst=256）==")
	h := &HistogramObserver{Bins: 64, DstNBins: 256, MinVal: 0, MaxVal: 1}
	vals := make([]float64, 0, 4200)
	for i := 0; i < 4000; i++ {
		vals = append(vals, 0.5+0.05*gaussLike(i))
	}
	for i := 0; i < 200; i++ {
		vals = append(vals, 1.0)
	}
	h.Fill(vals)
	nmin, nmax, sb, eb := h.NonLinearParamSearch()
	fmt.Printf("  右端离群簇 → new_min=%.6f new_max=%.6f (start=%d, end=%d)\n", nmin, nmax, sb, eb)

	hu := &HistogramObserver{Bins: 32, DstNBins: 256, MinVal: 0, MaxVal: 1}
	uni := make([]float64, 1001)
	for i := range uni {
		uni[i] = float64(i) / 1000.0
	}
	hu.Fill(uni)
	umin, umax, usb, ueb := hu.NonLinearParamSearch()
	fmt.Printf("  均匀分布   → new_min=%.6f new_max=%.6f (start=%d, end=%d)\n", umin, umax, usb, ueb)
}

// gaussLike 用确定性三角函数合成一组近似正态的样本（避免依赖随机源）。
func gaussLike(i int) float64 {
	a := float64((i*2654435761)%1000000) / 1000000.0
	b := float64((i*40503)%1000000) / 1000000.0
	return (a + b + float64((i*1103515245)%1000000)/1000000.0 - 1.5) * 1.2
}
