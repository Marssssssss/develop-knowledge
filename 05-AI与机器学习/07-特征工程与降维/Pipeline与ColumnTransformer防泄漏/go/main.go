// Pipeline 防交叉验证泄漏 —— Go 版:泄漏量随样本量 n 的变化(对照 python/main.py 实验 1/2)。
//
// 编译运行:go run .
//
// 数据由本文件的 RNG 生成,与 Python 版的样本不同,故数值不会逐位一致,
// 但两个版本量的是同一件事:SelectKBest 在折内还是折外。
package main

import (
	"fmt"
	"math"
	"math/rand"
)

const (
	pFeatures = 10000
	kBest     = 25
	nInfo     = 25
	folds     = 5
)

func makeData(n, p, nInf int, coef float64, seed int64) ([][]float64, []int) {
	rng := rand.New(rand.NewSource(seed))
	X := make([][]float64, n)
	for i := range X {
		X[i] = make([]float64, p)
		for j := range X[i] {
			X[i][j] = rng.NormFloat64()
		}
	}
	y := make([]int, n)
	for i := 0; i < n; i++ {
		var z float64
		for j := 0; j < nInf; j++ {
			z += X[i][j]
		}
		if rng.Float64() < sigmoid(coef*z) {
			y[i] = 1
		}
	}
	return X, y
}

func mean(xs []float64) float64 {
	var s float64
	for _, v := range xs {
		s += v
	}
	return s / float64(len(xs))
}

func std(xs []float64) float64 {
	mu := mean(xs)
	var s float64
	for _, v := range xs {
		s += (v - mu) * (v - mu)
	}
	return math.Sqrt(s / float64(len(xs)))
}

func newClf() Estimator { return &LogReg{Epochs: 1000, Lr: 1.0, L2: 0.01} }

// exp1 泄漏量随 n 缩小;(n, coef, 种子数)
func exp1() {
	fmt.Println("==============================================================")
	fmt.Println("实验 1 —— 特征选择的泄漏值多少分:随样本量 n 缩小")
	fmt.Println("==============================================================")
	fmt.Printf("设定:p=%d 个特征、SelectKBest 选 top-%d、%d 折 CV;前 %d 个特征等权系数=coef\n\n",
		pFeatures, kBest, folds, nInfo)
	fmt.Printf("%5s %6s %12s %12s %8s\n", "n", "coef", "泄漏式CV", "正确式CV", "Δ")
	fmt.Println("--------------------------------------------------------------")

	type cfg struct {
		n, nseed int
		coef     float64
	}
	cfgs := []cfg{{100, 0.30, 2}, {200, 0.30, 2}, {400, 0.30, 2}, {100, 0.00, 2}}
	var d100, d400 float64
	for _, c := range cfgs {
		var leaky, safe []float64
		for s := 0; s < c.nseed; s++ {
			X, y := makeData(c.n, pFeatures, nInfo, c.coef, int64(1+s))
			// 泄漏式:先在**全量**数据上选特征,再交给 crossValScore
			sel := &SelectKBest{K: kBest}
			sel.Fit(X, y)
			Xs := sel.Transform(X)
			leaky = append(leaky, crossValScore(newClf, Xs, y, folds, 0)...)
			// 正确式:选择器作为 Pipeline 的第一级,每折内部重新 Fit
			safe = append(safe, crossValScore(func() Estimator {
				return NewPipeline(&SelectKBest{K: kBest}, newClf())
			}, X, y, folds, 0)...)
		}
		lmu, smu := mean(leaky), mean(safe)
		fmt.Printf("%5d %6.2f %12.3f %12.3f %+8.3f\n", c.n, c.coef, lmu, smu, lmu-smu)
		if c.coef > 0 && c.n == 100 {
			d100 = lmu - smu
		}
		if c.coef > 0 && c.n == 400 {
			d400 = lmu - smu
		}
	}
	fmt.Printf(`
读法:
  * coef=0.00 那一行的 25 个"特征"全是噪声、标签也是抛硬币生成的,理论上限就是 0.5,
    但泄漏式依然报出高于 0.5 的分数 —— 差值纯粹是"选择阶段偷看验证折"换来的。
  * 机制:ANOVA 分数在全量 y 上计算,噪声特征要挤进 top-%d 必须把**全部 n 个样本**
    分开,其中包含后面充当验证集的那部分。分类器在训练折上给它定的符号,
    到验证折上照样管用 —— 看起来泛化,其实只是把偷看过的信息兑现了一次。
  * 泄漏量随 n 稀释:n=100 时 Δ=%+.3f,n=400 时 Δ=%+.3f。样本越少、特征越多,越致命。
`, kBest, d100, d400)
}

// exp2 留出集上的形状:重复切分,看训练/测试精度
func exp2() {
	fmt.Println("\n==============================================================")
	fmt.Println("实验 2 —— 先选择再切分 vs 先切分再选择:8 次随机重复")
	fmt.Println("==============================================================")
	const n, reps = 100, 8
	fmt.Printf("设定:n=%d, p=%d, k=%d, coef=0.30;训练/测试 = 50/50\n\n", n, pFeatures, kBest)
	fmt.Printf("%4s | %21s | %21s | %8s\n", "重复", "泄漏式 训练/测试", "正确式 训练/测试", "测试差")
	fmt.Println("--------------------------------------------------------------")

	var leaky, safe []float64
	for r := 0; r < reps; r++ {
		X, y := makeData(n, pFeatures, nInfo, 0.30, int64(500+r))
		perm := rand.New(rand.NewSource(int64(r))).Perm(n)
		var Xtr, Xte [][]float64
		var ytr, yte []int
		for i, v := range perm {
			if i < n/2 {
				Xtr, ytr = append(Xtr, X[v]), append(ytr, y[v])
			} else {
				Xte, yte = append(Xte, X[v]), append(yte, y[v])
			}
		}
		// (A) 泄漏:选择器看全量 y,随后才切分
		sa := &SelectKBest{K: kBest}
		sa.Fit(X, y)
		ma := &LogReg{Epochs: 1000, Lr: 1.0, L2: 0.01}
		ma.Fit(sa.Transform(Xtr), ytr)
		atr, ate := accuracy(ma, sa.Transform(Xtr), ytr), accuracy(ma, sa.Transform(Xte), yte)
		// (B) 正确:选择器只看训练部分
		sb := &SelectKBest{K: kBest}
		sb.Fit(Xtr, ytr)
		mb := &LogReg{Epochs: 1000, Lr: 1.0, L2: 0.01}
		mb.Fit(sb.Transform(Xtr), ytr)
		btr, bte := accuracy(mb, sb.Transform(Xtr), ytr), accuracy(mb, sb.Transform(Xte), yte)
		leaky, safe = append(leaky, ate), append(safe, bte)
		fmt.Printf("%4d | %9.3f / %-9.3f | %9.3f / %-9.3f | %+8.3f\n",
			r, atr, ate, btr, bte, ate-bte)
	}
	fmt.Println("--------------------------------------------------------------")
	fmt.Printf("泄漏式测试 %.3f ± %.3f   正确式测试 %.3f ± %.3f   平均差 %+.3f\n\n",
		mean(leaky), std(leaky), mean(safe), std(safe), mean(leaky)-mean(safe))
	fmt.Println(`读法:
  * 泄漏式的**训练**精度与正确式相当,但测试精度系统性偏高;而单次实验的波动
    远大于这个差值,所以只跑一次根本看不出来 —— 那个更好看的数字会被当成真实收益。
  * 这两组数据用的是同一份 X、同一份 y、同一个分类器,唯一差别是
    SelectKBest.Fit 看见的是全量还是折内。这就是把变换器关进 Pipeline 的全部意义。`)
}

func main() {
	exp1()
	exp2()
	fmt.Println("\n==============================================================")
	fmt.Println("结论:任何用 y 学参数的步骤都必须关进 Pipeline(或在折内重建);")
	fmt.Println("      否则被污染的那个分数,恰好是你拿去汇报的那个。")
	fmt.Println("==============================================================")
}
