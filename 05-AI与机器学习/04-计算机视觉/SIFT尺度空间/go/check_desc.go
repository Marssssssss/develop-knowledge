// SIFT 自检 F~I 段:方向直方图、128 维描述子、clamp 语义、0.8 比率匹配。
package main

import (
	"fmt"
	"math"
	"sort"
)

// checkOrientation F 段:36 bin 方向直方图与 80% 判据。
func checkOrientation() {
	fmt.Println("\n== F. 36 bin 方向直方图与 80% 判据 ==")
	hist := OrientationHistogram(fill(40, 1.0), fill(40, 30.0), fill(40, 1.0))
	check("直方图为 36 个 bin(每 bin 10°)", len(hist) == OrientationBins, "")
	check("30° 的梯度落入 bin 3", argmax(hist) == 3, fmt.Sprintf("peak bin %d", argmax(hist)))
	check("单峰只生成 1 个方向", len(AssignOrientations(hist, PeakRatio)) == 1, "")
	bi := append([]float64(nil), hist...)
	bi[13] = 0.85 * 40.0
	check("次峰达 85% -> 生成第 2 个关键点(同位置同尺度不同方向)",
		len(AssignOrientations(bi, PeakRatio)) == 2, "")
	tri := append([]float64(nil), hist...)
	tri[13] = 0.79 * 40.0
	check("次峰 79% < 80% -> 不生成", len(AssignOrientations(tri, PeakRatio)) == 1, "")
	check("抛物线插值朝较高的邻居偏移",
		ParabolicPeakOffset(1.0, 2.0, 1.2) > 0 && ParabolicPeakOffset(1.2, 2.0, 1.0) < 0, "")
	par := ParabolicPeakOffset(5.0-1.21, 5.0-0.01, 5.0-0.81)
	check("抛物线插值对真抛物线精确还原 0.1", math.Abs(par-0.1) < 1e-12,
		fmt.Sprintf("%.15f", par))
	gw := GaussianCircleWeights([][2]float64{{0, 0}, {1.6, 0}, {3.2, 0}, {4.8, 0}}, 1.5)
	check("σ=1.5×scale 的圆形高斯窗随距离单调衰减",
		gw[0] == 1.0 && gw[0] > gw[1] && gw[1] > gw[2] && gw[2] > gw[3], "")
}

// checkDescriptor G 段:128 维描述子结构与不变性。
func checkDescriptor() {
	fmt.Println("\n== G. 128 维描述子 ==")
	vec := desc(samples(0.0, 1.0, 16))
	norm := 0.0
	for _, v := range vec {
		norm += v * v
	}
	check("维度 == 4x4x8 = 128", len(vec) == DescWidth*DescWidth*DescBins, fmt.Sprintf("%d", len(vec)))
	check("单位长度", math.Abs(math.Sqrt(norm)-1.0) < 1e-15, "")
	v7 := desc(samples(0.0, 7.0, 16))
	check("整体幅值缩放不改变描述子(归一化抵消)", maxDiff(vec, v7) < 1e-15,
		fmt.Sprintf("max diff %.2e", maxDiff(vec, v7)))
	rot := []Sample{}
	for j := 0; j < 16; j++ {
		for i := 0; i < 16; i++ {
			u, v := float64(i)-7.5, float64(j)-7.5
			rot = append(rot, Sample{-v, u, 1.0, 120.0})
		}
	}
	vr := ComputeDescriptor(rot, 90.0, DescWidth, DescBins, DescSamples, 0.5)
	sa := append([]float64(nil), vec...)
	sb := append([]float64(nil), vr...)
	sort.Float64s(sa)
	sort.Float64s(sb)
	check("90° 旋转(位置+梯度+关键点角)后描述子取值集合一致", maxDiff(sa, sb) < 1e-15,
		fmt.Sprintf("max diff %.2e", maxDiff(sa, sb)))
	for _, t := range [][3]float64{{0, 0, 0}, {0.3, 0.7, 0.2}, {0.5, 0.5, 0.5}, {1, 1, 1}} {
		wsum := 0.0
		tw := TrilinearWeights(t[0], t[1], t[2])
		for _, x := range tw {
			wsum += x.W
		}
		check(fmt.Sprintf("三线性权重和 == 1 @(%.1f,%.1f,%.1f)", t[0], t[1], t[2]),
			len(tw) == 8 && math.Abs(wsum-1.0) < 1e-15, fmt.Sprintf("sum=%.16f", wsum))
	}
	a0 := desc([]Sample{{-7.5, -7.5, 1.0, 0.0}})
	a3 := desc([]Sample{{7.5, 7.5, 1.0, 0.0}})
	check("采样格 (-7.5,-7.5) 落到子块 (0,0) 并三线性扩散到 4 个bin组合",
		nonZero(a0) == 4 && argmax(a0) == 0, fmt.Sprintf("nz=%d argmax=%d", nonZero(a0), argmax(a0)))
	check("采样格 (7.5,7.5) 落到子块 (3,3) 的唯一 bin",
		nonZero(a3) == 1 && argmax(a3) == 120, fmt.Sprintf("argmax=%d", argmax(a3)))
	zv := NormalizeDescriptor(make([]float64, 128), DescClamp)
	zsum := 0.0
	for _, v := range zv {
		zsum += math.Abs(v)
	}
	check("全零向量不产生除零", len(zv) == 128 && zsum == 0.0, "")
}

// checkClamp H 段:论文"先单位化 → 截到 0.2 → 再单位化"两步的真实语义。
func checkClamp() {
	fmt.Println("\n== H. clamp 0.2 的真实语义 ==")
	before := make([]float64, 128)
	before[0], before[1] = 1.0, 0.9
	n0 := 0.0
	for _, v := range before {
		n0 += v * v
	}
	n0 = math.Sqrt(n0)
	un := make([]float64, 128)
	cl := make([]float64, 128)
	for i, v := range before {
		un[i] = v / n0
		cl[i] = math.Min(v/n0, 0.2)
	}
	n1 := 0.0
	for _, v := range cl {
		n1 += v * v
	}
	n1 = math.Sqrt(n1)
	af := make([]float64, 128)
	for i, v := range cl {
		af[i] = v / n1
	}
	check("截断发生在最终归一化之前:饱和分量被抹平",
		math.Abs(un[0]/un[1]-1.111111) < 1e-6 && math.Abs(af[0]/af[1]-1.0) < 1e-12,
		fmt.Sprintf("比值 %.6f -> %.6f", un[0]/un[1], af[0]/af[1]))
	mx := af[0]
	for _, v := range af {
		if v > mx {
			mx = v
		}
	}
	check("坑:重归一化后分量可以 > 0.2(实测 0.7071)", math.Abs(mx-math.Sqrt(0.5)) < 1e-12,
		fmt.Sprintf("max=%.6f", mx))
}

// checkMatching I 段:最近邻/次近邻距离比筛选。
func checkMatching() {
	fmt.Println("\n== I. 0.8 比率匹配 ==")
	q := desc(samples(20.0, 1.0, 16))
	c1 := desc(samples(19.0, 1.0, 16))
	c2 := desc(samples(21.0, 1.0, 16))
	cf := desc(samples(140.0, 1.0, 16))
	idx, r := RatioTest(q, [][]float64{c1, cf}, MatchRatio)
	check("最近邻明显更近 -> 接受(比值 < 0.8)", idx == 0 && r < MatchRatio,
		fmt.Sprintf("ratio=%.6f", r))
	_, r2 := RatioTest(q, [][]float64{c1, c2, cf}, MatchRatio)
	check("两个几乎等距的候选 -> 拒绝(比值 > 0.8)", r2 > MatchRatio,
		fmt.Sprintf("ratio=%.6f", r2))
	i3, _ := RatioTest(q, [][]float64{c1}, MatchRatio)
	i4, _ := RatioTest(q, [][]float64{}, MatchRatio)
	check("候选不足 2 个时不崩", i3 == 0 && i4 == -1, "")
}
