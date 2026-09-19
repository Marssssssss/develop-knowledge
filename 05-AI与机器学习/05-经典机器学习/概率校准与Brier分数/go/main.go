// 概率校准自检(Go 侧):与 python/calibration_check.py 同一组结论。
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
	} else {
		fails = append(fails, name)
		fmt.Printf("  [FAIL] %s  %s\n", name, detail)
	}
}

type rng struct{ st uint64 }

func newRNG(seed uint64) *rng { return &rng{st: seed} }

func (r *rng) next() float64 {
	r.st = r.st*6364136223846793005 + 1442695040888963407
	return float64(r.st>>11) / float64(1<<53)
}

func repeat(v float64, n int) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = v
	}
	return out
}

func main() {
	fmt.Println("概率校准与 Brier 分数 自检(Go)")
	fmt.Println("==========================================================================")

	// A. Brier 分解恒等
	r := newRNG(1)
	okID := true
	for t := 0; t < 200; t++ {
		y := make([]float64, 40)
		p := make([]float64, 40)
		for i := 0; i < 40; i++ {
			if r.next() < 0.4 {
				y[i] = 1
			}
			lvl := float64(int(r.next()*10)) / 10
			p[i] = lvl + 0.05 // 箱内常数
		}
		rel, res, unc := brierDecomposition(y, p, 10)
		if math.Abs((rel-res+unc)-brier(y, p)) > 1e-12 {
			okID = false
		}
	}
	check("A1 箱内预测为常数时分解恒等式 200 组成立", okID, "")

	// B. 校准的定义
	yb, pb := []float64{}, []float64{}
	for _, spec := range [][2]float64{{0.1, 40}, {0.8, 40}, {0.5, 40}} {
		frac, cnt := spec[0], int(spec[1])
		for i := 0; i < cnt; i++ {
			if i < int(frac*float64(cnt)) {
				yb = append(yb, 1)
			} else {
				yb = append(yb, 0)
			}
			pb = append(pb, frac)
		}
	}
	relB, resB, _ := brierDecomposition(yb, pb, 10)
	check("B1 分组常量预测等于组内正例率时 REL ≈ 0", relB < 1e-12, fmt.Sprintf("%.3e", relB))
	check("B2 此时 RES > 0", resB > 0.05, fmt.Sprintf("%.6f", resB))
	okCurve := true
	for _, pt := range calibrationCurve(yb, pb, 10) {
		if math.Abs(pt[0]-pt[1]) > 1e-12 {
			okCurve = false
		}
	}
	check("B3 校准曲线落在 y = x 上", okCurve, "")

	// C. 低 Brier ≠ 校准好
	yA := append(append(repeat(1, 140), repeat(0, 60)...), append(repeat(1, 60), repeat(0, 140)...)...)
	pA := append(repeat(0.80, 200), repeat(0.20, 200)...)
	yB := append(append(repeat(1, 110), repeat(0, 90)...), append(repeat(1, 90), repeat(0, 110)...)...)
	pB := append(repeat(0.55, 200), repeat(0.45, 200)...)
	bA, bB := brier(yA, pA), brier(yB, pB)
	relA, resA, _ := brierDecomposition(yA, pA, 10)
	relB2, resB2, _ := brierDecomposition(yB, pB, 10)
	check("C1 过度自信但分辨力强的模型 Brier 更低", bA < bB, fmt.Sprintf("%.5f vs %.5f", bA, bB))
	check("C2 但它的 REL 更差", relA > relB2, fmt.Sprintf("REL %.5f vs %.5f", relA, relB2))
	check("C3 便宜占在 RES 上(A 的 RES 是 B 的 10 倍以上)", resA > 10*resB2,
		fmt.Sprintf("RES %.5f vs %.5f", resA, resB2))

	// D. Platt 平滑目标与初值
	yd := append(repeat(1, 30), repeat(0, 70)...)
	T := plattTargets(yd)
	check("D1 T[y>0] == 31/32", math.Abs(T[0]-31.0/32.0) < 1e-15, fmt.Sprintf("%.9f", T[0]))
	check("D2 T[y≤0] == 1/72", math.Abs(T[len(T)-1]-1.0/72.0) < 1e-15, "")
	A0, B0 := plattInit(yd)
	check("D3 AB0 = [0, log(71/31)]", A0 == 0 && math.Abs(B0-math.Log(71.0/31.0)) < 1e-15,
		fmt.Sprintf("%.9f", B0))
	check("D4 未超阈值不缩放", sigmoidScale([]float64{1, 2}) == 1.0, "")
	check("D5 超阈值 30 按最大值缩放", math.Abs(sigmoidScale([]float64{1, 50000})-50000) < 1e-9, "")
	check("D6 阈值边界是 >=30:29.9 不缩放、30.0 缩放",
		sigmoidScale([]float64{29.9}) == 1.0 && math.Abs(sigmoidScale([]float64{30.0})-30.0) < 1e-9, "")

	// E. sigmoid 保序 / isotonic 产生并列
	r3 := newRNG(9)
	ye, fe := []float64{}, []float64{}
	for i := 0; i < 120; i++ {
		yi := 0.0
		if r3.next() < 0.5 {
			yi = 1
		}
		base := -1.2
		if yi == 1 {
			base = 1.2
		}
		ye = append(ye, yi)
		fe = append(fe, base+r3.next()*1.5)
	}
	pe := make([]float64, len(fe))
	for i := range fe {
		pe[i] = 1 / (1 + math.Exp(-fe[i]))
	}
	sc := &SigmoidCalibration{}
	sc.Fit(fe, ye)
	check("E1 sigmoid 校准后 AUC 完全不变", math.Abs(auc(ye, pe)-auc(ye, sc.Predict(fe))) < 1e-12,
		fmt.Sprintf("%.6f vs %.6f", auc(ye, pe), auc(ye, sc.Predict(fe))))
	fs := append([]float64(nil), fe...)
	for i := 1; i < len(fs); i++ {
		for j := i; j > 0 && fs[j] < fs[j-1]; j-- {
			fs[j], fs[j-1] = fs[j-1], fs[j]
		}
	}
	iso := &IsotonicRegression{}
	iso.Fit(fe, ye)
	pis := iso.Predict(fs)
	uniq := map[float64]bool{}
	for _, v := range pis {
		uniq[math.Round(v*1e12) / 1e12] = true
	}
	check("E2 isotonic 输出出现并列", len(pis)-len(uniq) > 0,
		fmt.Sprintf("并列数=%d", len(pis)-len(uniq)))
	mono := true
	for i := 0; i+1 < len(pis); i++ {
		if pis[i] > pis[i+1]+1e-12 {
			mono = false
		}
	}
	check("E3 输出单调不降", mono, "")

	// F/G. temperature scaling 与校准曲线
	zf := append(repeat(2.0, 100), repeat(-2.0, 100)...)
	yf := append(append(repeat(1, 80), repeat(0, 20)...), append(repeat(1, 20), repeat(0, 80)...)...)
	pRaw := temperatureScale(zf, 1.0)
	check("F0 原始概率高于真实正例率(过度自信)", pRaw[0] > 0.85,
		fmt.Sprintf("σ(2)=%.4f vs 真实率 0.80", pRaw[0]))
	hardRaw := make([]int, len(zf))
	for i := range zf {
		if zf[i] > 0 {
			hardRaw[i] = 1
		}
	}
	Th := fitTemperature(zf, yf)
	pT := temperatureScale(zf, Th)
	same := true
	for i := range pT {
		h := 0
		if pT[i] > 0.5 {
			h = 1
		}
		if h != hardRaw[i] {
			same = false
		}
	}
	check("F1 温度缩放不改变硬预测(accuracy 不变)", same, "")
	check("F2 过度自信模型上学出的 T > 1", Th > 1.0, fmt.Sprintf("T=%.4f", Th))
	relRaw, _, _ := brierDecomposition(yf, pRaw, 5)
	relT, _, _ := brierDecomposition(yf, pT, 5)
	check("F3 校准后 REL 下降", relT[0] < relRaw[0], fmt.Sprintf("%.6f → %.6f", relRaw[0], relT[0]))

	cg := calibrationCurve(yf, pRaw, 5)
	okG := true
	for _, pt := range cg {
		if pt[0] > 0.5 && !(pt[1] < pt[0]) {
			okG = false
		}
		if pt[0] <= 0.5 && !(pt[1] > pt[0]) {
			okG = false
		}
	}
	check("G1 高分段过度自信 / 低分段不够自信(sigmoid 形曲线)", okG,
		fmt.Sprintf("%v", cg))

	fmt.Println("--------------------------------------------------------------------------")
	fmt.Printf("断言 %d/%d 通过\n", passed, total)
	if len(fails) > 0 {
		fmt.Println("失败项:", fails)
		os.Exit(1)
	}
	fmt.Println("全部通过")
}
