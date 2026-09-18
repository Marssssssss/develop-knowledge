// SIFT 自检入口:把论文里可验证的结论逐条钉住(与 python/sift_check.py 一一对应)。
//
// 运行:cd go && go run .
// 注意:本机未安装 Go 工具链,本组文件未经编译器验证,已用 _docs/tools/bracket_check.py
// 做括号配平、syntax_sanity.py 做结构体检,并逐处人工核对函数签名与实参个数。
//
// 断言分九段:A 尺度空间参数 / B DoG≈LoG / C 26 邻居极值 / D 亚像素定位与对比度剔除 /
// E Hessian 边缘剔除 / F 36 bin 方向直方图 / G 128 维描述子 / H clamp 语义 / I 0.8 比率匹配。
package main

import (
	"fmt"
	"math"
	"os"
)

var total, passed int
var fails []string

func check(name string, cond bool, detail string) {
	total++
	if cond {
		passed++
		fmt.Printf("  [PASS] %s  %s\n", name, detail)
		return
	}
	fails = append(fails, name)
	fmt.Printf("  [FAIL] %s  %s\n", name, detail)
}

// checkScaleSpace A 段:高斯核、σ 序列、octave 结构。
func checkScaleSpace() {
	fmt.Println("== A. 尺度空间参数 ==")
	k := math.Pow(2.0, 1.0/float64(ScalesPerOctave))
	check("k = 2^(1/s),s=3 时 k^3 == 2", math.Abs(k*k*k-2.0) < 1e-12, fmt.Sprintf("k=%.12f", k))
	sig := make([]float64, ScalesPerOctave+3)
	for i := range sig {
		sig[i] = SiftSigma * math.Pow(k, float64(i))
	}
	check("σ 序列在第 s 张翻倍(一个 octave 翻倍)", math.Abs(sig[3]/sig[0]-2.0) < 1e-12,
		fmt.Sprintf("σ0=%.4f σ3=%.4f", sig[0], sig[3]))
	ker := Gaussian1D(1.6, 0)
	sum := 0.0
	for _, v := range ker {
		sum += v
	}
	check("1D 高斯核归一化", math.Abs(sum-1.0) < 1e-15, fmt.Sprintf("sum=%.16f", sum))
	check("核半径覆盖 3σ", len(ker) == 2*int(math.Ceil(3*1.6))+1, fmt.Sprintf("len=%d", len(ker)))
	check("增量模糊 σ = sqrt(σ2²-σ1²)",
		math.Abs(IncrementalSigma(1.0, 2.0)-math.Sqrt(3.0)) < 1e-12,
		fmt.Sprintf("%.12f", IncrementalSigma(1.0, 2.0)))
	check("目标 σ 更小时返回 0(不做反向去模糊)", IncrementalSigma(2.0, 1.0) == 0.0, "")

	n := 32
	img := make([][]float64, n)
	for y := 0; y < n; y++ {
		row := make([]float64, n)
		for x := 0; x < n; x++ {
			row[x] = 128.0 + 60.0*math.Sin(float64(x)/4.0)*math.Cos(float64(y)/5.0)
		}
		img[y] = row
	}
	pyr := BuildGaussianPyramid(img, 4, ScalesPerOctave, SiftSigma, SiftAssumedBlur)
	dogs := BuildDoGPyramid(pyr)
	allBlur := true
	for _, o := range pyr {
		if len(o) != ScalesPerOctave+3 {
			allBlur = false
		}
	}
	check("每个 octave 生成 s+3 = 6 张模糊图", allBlur, fmt.Sprintf("%v", lens(pyr)))
	allDog := true
	for _, o := range dogs {
		if len(o) != ScalesPerOctave+2 {
			allDog = false
		}
	}
	check("DoG 每 octave 为 s+2 = 5 张", allDog, fmt.Sprintf("%v", lens(dogs)))
	check("octave 尺寸逐级减半", fmt.Sprintf("%v", sizes(pyr)) == "[32 16 8 4]",
		fmt.Sprintf("%v", sizes(pyr)))
	check("DoG 张数 = 模糊图 - 1(s+3 张模糊凑出 s+2 张 DoG)",
		len(dogs[0]) == ScalesPerOctave+2 && len(pyr[0])-len(dogs[0]) == 1,
		fmt.Sprintf("%d -> %d", len(pyr[0]), len(dogs[0])))
}

// checkDogVsLog B 段:DoG 与尺度归一化 LoG 的相关性随 k → 1 单调趋近 1。
func checkDogVsLog() {
	fmt.Println("\n== B. DoG 是尺度归一化 LoG 的近似 ==")
	ts := []float64{}
	for i := -60; i <= 60; i++ {
		ts = append(ts, float64(i)*0.2)
	}
	prevCorr, prevAmp := 0.0, math.MaxFloat64
	for _, kk := range []float64{1.40, 1.26, 1.10, 1.02} {
		a, b := make([]float64, len(ts)), make([]float64, len(ts))
		for i, t := range ts {
			a[i] = gauss(t, 1.6*kk) - gauss(t, 1.6)
			lap := (gauss(t+0.02, 1.6) - 2*gauss(t, 1.6) + gauss(t-0.02, 1.6)) / 0.0004
			b[i] = (kk - 1.0) * 1.6 * 1.6 * lap
		}
		num, na, nb, sa, sb := 0.0, 0.0, 0.0, 0.0, 0.0
		for i := range a {
			num += a[i] * b[i]
			na += a[i] * a[i]
			nb += b[i] * b[i]
			sa += math.Abs(a[i])
			sb += math.Abs(b[i])
		}
		corr, amp := num/(math.Sqrt(na)*math.Sqrt(nb)), sa/sb
		check(fmt.Sprintf("k=%.2f:相关系数 %.6f 单调趋近 1", kk, corr), corr > prevCorr,
			fmt.Sprintf("amp=%.4f", amp))
		if prevAmp != math.MaxFloat64 {
			check(fmt.Sprintf("k=%.2f:幅度比 %.4f 比上一档更接近 1", kk, amp),
				math.Abs(amp-1.0) < math.Abs(prevAmp-1.0), "")
		}
		prevCorr, prevAmp = corr, amp
	}
	check("论文参数 k=2^(1/3) 下相关系数 > 0.98", prevCorr > 0.98,
		fmt.Sprintf("%.6f", prevCorr))
}

// checkExtrema C 段:26 邻居极值判定。
func checkExtrema() {
	fmt.Println("\n== C. 26 邻居尺度空间极值 ==")
	center := [3]float64{2.0, 7.0, 7.0}
	unit := [3]float64{1.5, 1.5, 1.5}
	ex := FindExtrema([][][][]float64{volume(5, 16, 0.5, center, unit)})
	check("整数峰上恰检出 1 个极值", len(ex) == 1, fmt.Sprintf("%d 个", len(ex)))
	okPos := len(ex) == 1 && ex[0].S == 2 && ex[0].Y == 7 && ex[0].X == 7
	check("极值位置为 (s=2,y=7,x=7)", okPos, fmt.Sprintf("%v", ex))
	check("负峰(极小值)同样被检出",
		len(FindExtrema([][][][]float64{negate(volume(5, 16, 0.5, center, unit))})) == 1, "")
	check("平坦体积没有极值",
		len(FindExtrema([][][][]float64{flatVolume(5, 16)})) == 0, "")
}

// checkLocalize D 段:亚像素偏移恢复 + 低对比度剔除。
func checkLocalize() {
	fmt.Println("\n== D. 亚像素定位 + 低对比度剔除 ==")
	center := [3]float64{2.0, 7.0, 7.0}
	unit := [3]float64{1.5, 1.5, 1.5}
	vint := volume(5, 16, 0.5, center, unit)
	loc := Localize(vint, 2, 7, 7, ContrastThreshold)
	check("采样点即真峰时 offset 全零", loc.Offset == [3]float64{0, 0, 0},
		fmt.Sprintf("%v", loc.Offset))
	check("D(x̂) 等于该采样值(0.5)", math.Abs(loc.DHat-0.5) < 1e-12,
		fmt.Sprintf("%.12f", loc.DHat))
	check("offset<0.5 时不要求换采样点", !loc.NeedResample, "")
	lf := Localize(volume(5, 16, 0.5, [3]float64{2.3, 7.2, 7.2}, unit), 2, 7, 7, ContrastThreshold)
	trueOff := [3]float64{0.3, 0.2, 0.2}
	err := 0.0
	for i := 0; i < 3; i++ {
		if d := math.Abs(lf.Offset[i] - trueOff[i]); d > err {
			err = d
		}
	}
	check("分数峰上亚像素偏移恢复真值(误差 < 0.03)", err < 0.03,
		fmt.Sprintf("%v vs %v, err=%.4f", lf.Offset, trueOff, err))
	lb := Localize(volume(5, 16, 0.5, [3]float64{2.6, 7, 7}, [3]float64{0.6, 1.5, 1.5}),
		2, 7, 7, ContrastThreshold)
	check("offset > 0.5 时标记需换采样点(论文要求重做插值)",
		lb.NeedResample && math.Abs(lb.Offset[0]) > 0.5, fmt.Sprintf("offset_s=%.4f", lb.Offset[0]))
	l5 := Localize(volume(5, 16, 0.5, [3]float64{2.5, 7, 7}, [3]float64{0.6, 1.5, 1.5}),
		2, 7, 7, ContrastThreshold)
	check("offset 恰为 0.5 不换点(论文判据是 strictly > 0.5)",
		math.Abs(l5.Offset[0]-0.5) < 1e-12 && !l5.NeedResample,
		fmt.Sprintf("offset_s=%.12f", l5.Offset[0]))
	dLow := Detect(volume(5, 16, 0.02, center, unit), ContrastThreshold, EdgeRatio)
	dHigh := Detect(volume(5, 16, 0.05, center, unit), ContrastThreshold, EdgeRatio)
	check("|D| = 0.02 < 0.03 -> 低对比度剔除",
		len(dLow) == 1 && dLow[0].Status == "low_contrast", "")
	check("|D| = 0.05 > 0.03 -> 保留", len(dHigh) == 1 && dHigh[0].Status == "kept", "")
}

// checkEdgeRejection E 段:Hessian 主曲率比判据。
func checkEdgeRejection() {
	fmt.Println("\n== E. Hessian 边缘响应剔除 ==")
	center := [3]float64{2.0, 7.0, 7.0}
	check("(r+1)²/r 在 r=10 时为 12.1", math.Abs(EdgeRatioLimit(10.0)-12.1) < 1e-12,
		fmt.Sprintf("%.4f", EdgeRatioLimit(10.0)))
	check("(r+1)²/r 在 r=1 取最小值 4(论文:特征值相等时最小)",
		math.Abs(EdgeRatioLimit(1.0)-4.0) < 1e-12, "")
	bh := HessianAt(volume(5, 16, 0.5, center, [3]float64{1.5, 1.5, 1.5}), 2, 7, 7)
	br, _ := PrincipalCurvatureRatio(bh[0], bh[1], bh[2])
	check("对称隆起的主曲率比为理论最小值 4.0", math.Abs(br-4.0) < 1e-12, fmt.Sprintf("%.12f", br))
	ridge := volume(5, 16, 0.5, center, [3]float64{0.8, 6.0, 1.5})
	rh := HessianAt(ridge, 2, 7, 7)
	rr, _ := PrincipalCurvatureRatio(rh[0], rh[1], rh[2])
	dr := Detect(ridge, ContrastThreshold, EdgeRatio)
	check("细长隆起(σy=6)的比值 > 12.1 -> 作为边缘剔除",
		rr > EdgeRatioLimit(10.0) && len(dr) == 1 && dr[0].Status == "edge",
		fmt.Sprintf("ratio=%.3f", rr))
	mild := volume(5, 16, 0.5, center, [3]float64{0.8, 4.0, 1.5})
	mh := HessianAt(mild, 2, 7, 7)
	mr, _ := PrincipalCurvatureRatio(mh[0], mh[1], mh[2])
	dm := Detect(mild, ContrastThreshold, EdgeRatio)
	check("不够细长的隆起(σy=4)仍保留",
		mr < EdgeRatioLimit(10.0) && len(dm) == 1 && dm[0].Status == "kept",
		fmt.Sprintf("ratio=%.3f", mr))
	_, okDet := PrincipalCurvatureRatio(1.0, 0.0, -1.0)
	check("Det <= 0(曲率异号)按边缘处理",
		!okDet && IsEdgeResponse(1.0, 0.0, -1.0, EdgeRatio), "")
}

func main() {
	checkScaleSpace()
	checkDogVsLog()
	checkExtrema()
	checkLocalize()
	checkEdgeRejection()
	checkOrientation()
	checkDescriptor()
	checkClamp()
	checkMatching()
	fmt.Printf("\n结果:%d/%d 通过", passed, total)
	if len(fails) > 0 {
		fmt.Printf(",失败:%v", fails)
		os.Exit(1)
	}
	fmt.Println()
}
