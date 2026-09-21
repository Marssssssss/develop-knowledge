package main

// demo 516 Go 侧实验台,与 python/main.py 的 E1/E3/E5/E6 同构。
// 运行:`go run .`(同包多文件必须用 `go run .`,不能 `go run main.go`)
//
// 本机无 Go 工具链,数值输出以 python 侧(已与 pandas/sklearn 对拍)为准;
// Go 侧另用 python 转写对拍脚本逐函数核对内核结果。

import (
	"fmt"
	"math"
	"sort"
)

func f9(v float64) string { return fmt.Sprintf("%9.4f", v) }

// ------------------------------------------------------------------ 工具

func pickRows(X [][]float64, idx []int) [][]float64 {
	out := make([][]float64, len(idx))
	for k, i := range idx {
		out[k] = X[i]
	}
	return out
}

func pickVals(v []float64, idx []int) []float64 {
	out := make([]float64, len(idx))
	for k, i := range idx {
		out[k] = v[i]
	}
	return out
}

func alignOne(col, y []float64) ([][]float64, []float64) {
	X := [][]float64{}
	yv := []float64{}
	for i := range col {
		if math.IsNaN(col[i]) || math.IsNaN(y[i]) {
			continue
		}
		X = append(X, []float64{1.0, col[i]})
		yv = append(yv, y[i])
	}
	return X, yv
}

func cvR2(X [][]float64, yv []float64, folds []Fold) []float64 {
	out := make([]float64, 0, len(folds))
	for _, f := range folds {
		w := FitOLS(pickRows(X, f.Train), pickVals(yv, f.Train), 1e-8)
		out = append(out, R2(pickVals(yv, f.Test), Predict(pickRows(X, f.Test), w)))
	}
	return out
}

func holdoutR2(X [][]float64, yv []float64, frac float64) (float64, float64) {
	h := int(float64(len(X)) * frac)
	w := FitOLS(X[:h], yv[:h], 1e-8)
	return R2(yv[:h], Predict(X[:h], w)), R2(yv[h:], Predict(X[h:], w))
}

// kfoldRows 随机 KFold:与 python 侧同用 LCG 的 Fisher-Yates,故洗牌结果一致。
func kfoldRows(n, k int, seed uint32) []Fold {
	r := &Rng{S: seed}
	idx := make([]int, n)
	for i := range idx {
		idx[i] = i
	}
	for i := n - 1; i > 0; i-- {
		j := int(r.Uniform() * float64(i+1))
		idx[i], idx[j] = idx[j], idx[i]
	}
	size := n / k
	out := make([]Fold, 0, k)
	for f := 0; f < k; f++ {
		te := append([]int(nil), idx[f*size:(f+1)*size]...)
		sort.Ints(te)
		inTest := map[int]bool{}
		for _, v := range te {
			inTest[v] = true
		}
		tr := []int{}
		for i := 0; i < n; i++ {
			if !inTest[i] {
				tr = append(tr, i)
			}
		}
		out = append(out, Fold{Train: tr, Test: te})
	}
	return out
}

// 三条对比构造:与 python/features.py 的四个函数同源。
var variants = []struct {
	Name string
	Fn   func([]float64) []float64
}{
	{"因果  mean(x[t-5..t-1])", func(v []float64) []float64 { return CausalRollingMean(v, 5, 1) }},
	{"错位  mean(x[t-4..t])  ", func(v []float64) []float64 { return ShiftedRollingMean(v, 5) }},
	{"泄漏  mean(x[t-2..t+2])", func(v []float64) []float64 { return LeakyRollingMeanCenter(v, 5) }},
}

// ------------------------------------------------------------------ E1

func e1Alignment() {
	fmt.Println("E1 对齐:滚动窗口默认右闭,第 t 行**含 x[t]**")
	x := []float64{1, 2, 3, 4, 5, 6, 7, 8}
	rm3 := RollingMean(x, 3, mpDefault, false, "")
	fmt.Printf("   %-24s %.2f\n", "rolling_mean(3) 第 6 行", rm3[6])
	fmt.Printf("   %-24s %.2f\n", "同上 .shift(1) 第 6 行", Shift(rm3, 1)[6])
	fmt.Printf("   %-24s %.2f\n", "rolling_mean(3,center)[6]", RollingMean(x, 3, mpDefault, true, "")[6])
	fmt.Println("   -> t=6(x=7) 这一行:默认版 6.0(含 x[t]),shift(1) 版 5.0(只到 x[t-1]),")
	fmt.Println("      center 版 7.0 —— 它含 x[7]=8,把下一期搬到了本期")
}

// ------------------------------------------------------------------ E2 / E3 / E4 / E5

func e2WithinRow(x []float64) {
	y := Shift(x, -1)
	fmt.Println("E2 行内泄漏:三种滚动特征在时序 CV 与真·留出下的表现(目标 y_t = x_{t+1})")
	fmt.Println("   特征                    时序CV(5x100) R²   最后 20% 留出 R²")
	for _, v := range variants {
		X, yv := alignOne(v.Fn(x), y)
		folds := TimeSeriesSplit{NSplits: 5, TestSize: 100}.Split(len(yv))
		cv := cvR2(X, yv, folds)
		_, ho := holdoutR2(X, yv, 0.8)
		fmt.Printf("   %-22s %s          %s\n", v.Name, f9(meanF(cv)), f9(ho))
	}
	fmt.Println("   -> 泄漏版在**时序 CV 下照样拿到 0.83**:时序 CV 只能挡住「跨切分」的泄漏,")
	fmt.Println("      挡不住「行内」的泄漏。这个漂亮的数字不是好消息,是作弊成功的证据。")
}

func e3KfoldVsTSS(x []float64) {
	y := Shift(x, -1)
	X, yv := alignOne(variants[0].Fn(x), y)
	tss := cvR2(X, yv, TimeSeriesSplit{NSplits: 5}.Split(len(yv)))
	kf := cvR2(X, yv, kfoldRows(len(yv), 5, 3))
	fmt.Print("   TimeSeriesSplit(5) 折内 R² =")
	for _, v := range tss {
		fmt.Printf(" %.3f", v)
	}
	fmt.Println()
	fmt.Print("   随机 KFold(5)      折内 R² =")
	for _, v := range kf {
		fmt.Printf(" %.3f", v)
	}
	fmt.Println()
	fmt.Printf("   均值:时序 CV =%s   随机 KFold =%s\n", f9(meanF(tss)), f9(meanF(kf)))
	fmt.Println("   -> 相邻行的特征窗口高度重叠,KFold 把「邻居」放进训练集,等于让模型抄答案")
}

func e4Gap(x []float64, h int, label string) {
	y := Shift(x, -h)
	X, yv := alignOne(variants[0].Fn(x), y)
	fmt.Printf("E4%s gap 与重叠标签:标签跨 h=%d 期时训练集末端会伸进测试期\n", label, h)
	fmt.Println("   gap  时序CV R²      首折:训练末行 到 测试首行   标签伸进测试期的训练行数")
	for _, gap := range []int{0, 1, 2, 3, 5} {
		folds := TimeSeriesSplit{NSplits: 4, TestSize: 100, Gap: gap}.Split(len(yv))
		cv := cvR2(X, yv, folds)
		tr0, te0 := folds[0].Train, folds[0].Test
		over := 0
		for r := tr0[len(tr0)-1] - h + 1; r <= tr0[len(tr0)-1]; r++ {
			if r+h >= te0[0] {
				over++
			}
		}
		fmt.Printf("   %-4d %s        %4d -> %4d            %d\n",
			gap, f9(meanF(cv)), tr0[len(tr0)-1], te0[0], over)
	}
	fmt.Println("   -> gap 的作用是**结构性**的:把训练集末端整块剔掉,使训练标签不再触碰测试期。")
}

func e5Holdout(x []float64) {
	y := Shift(x, -1)
	fmt.Println("E5 真·未来留出:训练/留出的落差**不是**判据")
	fmt.Println("   特征                    训练集 R²   留出集 R²   落差")
	for _, v := range variants {
		X, yv := alignOne(v.Fn(x), y)
		tr, ho := holdoutR2(X, yv, 0.8)
		fmt.Printf("   %-22s %s   %s   %s\n", v.Name, f9(tr), f9(ho), f9(tr-ho))
	}
	fmt.Println("   -> 实测结论与直觉相反:**泄漏特征的落差最小,因果特征最大**。")
	fmt.Println("      所以「落差小 = 可信」是错的判据;唯一的判据是 E6 那种机械检验。")
}

// ------------------------------------------------------------------ E6

func e6Probe() {
	n := 48
	seeds := []uint32{11, 12, 13}
	fmt.Println("E6 机械因果探针(3 个随机种子 × n=48,扰动幅度 1.0;分母是各自的有效行数)")
	fmt.Println("   特征                     依赖未来 x[j](j>t)   扰动 x[t]→第t行   扰动 x[t+1]→第t行")
	for _, v := range variants {
		st := Audit(v.Fn, n, seeds, 1e-9)
		fmt.Printf("   %-24s %6d/%-6d %9d/%-6d %9d/%-6d\n", v.Name,
			st.FutureBad, st.FutureTotal, st.CurHit, st.CurTotal, st.NextHit, st.NextTotal)
	}
	st := Audit(func(v []float64) []float64 { return LeakyRollingMeanShiftNeg(v, 5) }, n, seeds, 1e-9)
	fmt.Printf("   %-24s %6d/%-6d %9d/%-6d %9d/%-6d\n", "泄漏  shift(-1) 变体",
		st.FutureBad, st.FutureTotal, st.CurHit, st.CurTotal, st.NextHit, st.NextTotal)

	fp := FuturePairs(variants[2].Fn, MakeAR1(n, 0.9, 1.0, 11), 1e-9)
	fmt.Printf("   泄漏-center 首种子未来依赖对数 = %d(闭式:每点贡献 t=j-1 与 t=j-2)\n", len(fp))

	fmt.Println("   -> 因果版:三列分子全 0,干净。")
	fmt.Println("      错位版:**未来依赖 0**,但「扰动 x[t] 会动到第 t 行」满格 —— 含当期、不含未来。")
	fmt.Println("        在 y_t = x_{t+1} 的设定下 x[t] 是决策时刻已知量,所以它**不是泄漏**,")
	fmt.Println("        而是对齐差 1 格:特征的注释若写「过去 5 期均值」,就与实现不符。")
	fmt.Println("      泄漏版:未来依赖列不为 0 —— 每行都被 x[t+1] 与 x[t+2] 牵动,这是真泄漏。")
	fmt.Println("      「错位」与「泄漏」被这张表彻底切开:前者只碰当期,后者碰未来。")

	// 支持集:把"读了哪些下标"直接打出来
	fmt.Println("   支持集探针(第 10 行读到哪些下标,i=10):")
	for _, v := range variants {
		s := SupportSet(v.Fn, 10, 24, 1000.0, 1e-9)
		fmt.Printf("      %-24s %v   未来下标数=%d\n", v.Name, s, FutureDepCount(s, 10))
	}
}

func main() {
	x := MakeAR1(1200, 0.9, 1.0, 7)
	e1Alignment()
	fmt.Println()
	e2WithinRow(x)
	fmt.Println()
	e3KfoldVsTSS(x)
	fmt.Println()
	e4Gap(x, 3, "")
	fmt.Println()
	e5Holdout(x)
	fmt.Println()
	e6Probe()
}
