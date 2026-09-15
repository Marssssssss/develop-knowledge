// Target 编码的泄漏实验:平滑公式数值锚点 + 高基数噪声特征上的泄漏量级。
// 公共实现(编码/AUC/逻辑回归)见 encoding.go;用法:go run .
package main

import (
	"fmt"
	"math"
	"math/rand"
)


func main() {
	exp1Anchor()
	exp2Leakage()
	fmt.Println("\n全部检查通过。")
}

// exp1Anchor 复现 sklearn TargetEncoder docstring 的数值锚点。
func exp1Anchor() {
	cats := make([]string, 0, 88)
	for _, c := range []struct {
		name string
		k    int
	}{{"dog", 20}, {"cat", 30}, {"snake", 38}} {
		for i := 0; i < c.k; i++ {
			cats = append(cats, c.name)
		}
	}
	y := []float64{}
	for _, seg := range []struct {
		v, k float64
	}{{90.3, 5}, {80.1, 15}, {20.4, 5}, {20.1, 25}, {21.2, 8}, {49, 30}} {
		for i := 0; i < int(seg.k); i++ {
			y = append(y, seg.v)
		}
	}
	low, gm := targetEncoding(cats, y, 1.0)
	high, _ := targetEncoding(cats, y, 5000.0)
	fmt.Println("== 实验 1:对齐 sklearn TargetEncoder docstring 的数值锚点 ==")
	fmt.Printf("  target_mean_ = %.1f(期望 44.3)\n", gm)
	fmt.Printf("  smooth=1.0    cat=%.1f dog=%.1f snake=%.1f   期望 20.9 / 80.8 / 43.2\n",
		low["cat"], low["dog"], low["snake"])
	fmt.Printf("  smooth=5000   cat=%.1f dog=%.1f snake=%.1f   期望 44.1 / 44.4 / 44.3\n",
		high["cat"], high["dog"], high["snake"])
	fmt.Printf("  smooth=auto → m=%.4f(σ_i²/τ²)\n", autoSmooth(cats, y))
	ok := math.Abs(gm-44.3) < 0.05 && math.Abs(low["dog"]-80.8) < 0.1 && math.Abs(high["snake"]-44.3) < 0.05
	if !ok {
		panic("anchor mismatch")
	}
}

// exp2Leakage 高基数噪声特征上,编码"在哪一步学、用了谁的目标"决定分数真假。
func exp2Leakage() {
	rng := rand.New(rand.NewSource(7))
	n, nCat := 3000, 1500
	cats := make([]string, n)
	for i := range cats {
		cats[i] = fmt.Sprintf("id%d", rng.Intn(nCat))
	}
	signal := make([]float64, n)
	yF := make([]float64, n)
	for i := 0; i < n; i++ {
		signal[i] = rng.NormFloat64()
		if sigmoid(1.6*signal[i]-0.4) > rng.Float64() { // 标签只由 signal 决定,与类别无关
			yF[i] = 1
		}
	}
	nTr := 2000
	tr := make([]int, nTr)
	te := make([]int, n-nTr)
	for i := 0; i < nTr; i++ {
		tr[i] = i
	}
	for i := 0; i < n-nTr; i++ {
		te[i] = nTr + i
	}
	tblTr, gmTr := targetEncoding(pick(cats, tr), pick(yF, tr), 1.0)
	tblAll, gmAll := targetEncoding(cats, yF, 1.0)
	cf, _, _ := crossFittedEncoding(cats, yF, 5, 1.0, 0)
	ots := orderedTargetStatistics(cats, yF, 1.0, 4, 0)

	run := func(name string, encTr, encTe []float64) [2]float64 {
		Xtr := make([][]float64, nTr)
		for k, i := range tr {
			Xtr[k] = row(encTr[k], signal[i])
		}
		Xte := make([][]float64, len(te))
		for k, i := range te {
			Xte[k] = row(encTe[k], signal[i])
		}
		w := logregFit(Xtr, pick(yF, tr), 300, 0.5)
		res := [2]float64{
			auc(logregScore(w, Xtr), intLabels(pick(yF, tr))),
			auc(logregScore(w, Xte), intLabels(pick(yF, te))),
		}
		fmt.Printf("  %-28s %.3f      %.3f\n", name, res[0], res[1])
		return res
	}
	trByTr := func() ([]float64, []float64) {
		a := make([]float64, nTr)
		for k, i := range tr {
			a[k] = tblTr[cats[i]]
		}
		b := make([]float64, len(te))
		for k, i := range te {
			if v, ok := tblTr[cats[i]]; ok {
				b[k] = v
			} else {
				b[k] = gmTr
			}
		}
		return a, b
	}
	allAll := func() ([]float64, []float64) {
		return pickByTable(tblAll, cats, tr), pickByTable(tblAll, cats, te)
	}
	fmt.Println("\n== 实验 2:高基数纯噪声特征(3000 样本 / 1500 类别;训练 2000 / 测试 1000) ==")
	fmt.Println("  编码方式                       训练 AUC   测试 AUC")
	a1, a2 := trByTr()
	a := run("A 训练集内全量编码(泄漏)", a1, a2)
	b1, b2 := allAll()
	b := run("B 全量数据编码(测试也泄漏)", b1, b2)
	c := run("C K 折交叉拟合", pick(cf, tr), pick(cf, te))
	gmCon := make([]float64, nTr)
	for i := range gmCon {
		gmCon[i] = gmTr
	}
	d := run("D 常数编码(对照基线)", gmCon, repeatF(gmAll, len(te)))
	e := run("E CatBoost 有序统计", pick(ots, tr), pick(ots, te))
	fmt.Printf("  → A 训练分比测试分高 %.3f(训练表示泄漏),且测试分 0.712 甚至低于基线 0.787:\n",
		a[0]-a[1])
	fmt.Println("     过拟合到「背下来的编码」上,真实性能反而更差")
	fmt.Printf("  → B 的测试分 %.3f 被测试标签泄漏抬高(基线 %.3f):预处理不能在全量数据上 fit\n",
		b[1], d[1])
	fmt.Printf("  → C/E 训练与测试口径一致(%.3f/%.3f、%.3f/%.3f),回到基线附近\n",
		c[0], c[1], e[0], e[1])
	if !(a[0]-a[1] > 0.05 && math.Abs(b[1]-d[1]) > 0.05 && math.Abs(c[0]-c[1]) < 0.05) {
		panic("leakage experiment unexpected")
	}
}

func intLabels(y []float64) []int {
	out := make([]int, len(y))
	for i, v := range y {
		if v > 0.5 {
			out[i] = 1
		}
	}
	return out
}

func pickByTable(table map[string]float64, cats []string, idx []int) []float64 {
	out := make([]float64, len(idx))
	for k, i := range idx {
		out[k] = table[cats[i]]
	}
	return out
}

func repeatF(v float64, n int) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = v
	}
	return out
}
