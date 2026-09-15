// Target 编码的泄漏机理(Go 版):平滑公式、K 折交叉拟合、CatBoost 有序目标统计。
// 与 ../python/main.py 同题。权威依据:
//
//	Micci-Barreca 2001 / sklearn TargetEncoder:S_i = λ_i·(n_iY/n_i) + (1-λ_i)·(n_Y/n),
//	  λ_i = n_i/(m + n_i);smooth="auto" 时 m = σ_i²/τ²(经验贝叶斯)
//	sklearn:`fit_transform` 用 cross fitting("each fold is encoded using the encodings
//	  learnt using the other k-1 folds"),因此 fit(X,y).transform(X) != fit_transform(X,y);
//	  官方明确 discourages 直接 fit("can introduce data leakage")
//	CatBoost:有序目标统计 x̂_i = (Σ_{j≺i,c_j=c_i} y_j + a·P)/(n_{i,c} + a),只用"过去"的标签
package main

import (
	"math"
	"math/rand"
	"sort"
)

func sigmoid(z float64) float64 {
	if z < -35 {
		return 0
	}
	if z > 35 {
		return 1
	}
	return 1 / (1 + math.Exp(-z))
}

func mean(v []float64) float64 {
	s := 0.0
	for _, x := range v {
		s += x
	}
	return s / float64(len(v))
}

// auc 秩和法:AUC = P(正样本得分 > 负样本得分),并列取平均秩。
func auc(scores []float64, labels []int) float64 {
	n := len(scores)
	order := make([]int, n)
	for i := range order {
		order[i] = i
	}
	sort.Slice(order, func(a, b int) bool { return scores[order[a]] < scores[order[b]] })
	ranks := make([]float64, n)
	for i := 0; i < n; {
		j := i
		for j+1 < n && scores[order[j+1]] == scores[order[i]] {
			j++
		}
		avg := float64(i+j)/2 + 1
		for k := i; k <= j; k++ {
			ranks[order[k]] = avg
		}
		i = j + 1
	}
	sp, sn, np, nn := 0.0, 0.0, 0.0, 0.0
	for i, l := range labels {
		if l == 1 {
			sp += ranks[i]
			np++
		} else {
			sn += ranks[i]
			nn++
		}
	}
	_, _ = sn, nn
	return (sp - np*(np+1)/2) / (np * nn)
}

// targetEncoding S_i = λ_i·mean_i + (1-λ_i)·globalMean,λ_i = n_i/(smooth+n_i)。
func targetEncoding(cats []string, y []float64, smooth float64) (map[string]float64, float64) {
	gm := mean(y)
	sums := map[string]float64{}
	cnts := map[string]int{}
	for i, c := range cats {
		sums[c] += y[i]
		cnts[c]++
	}
	out := make(map[string]float64, len(sums))
	for c, n := range cnts {
		lam := float64(n) / (smooth + float64(n))
		out[c] = lam*(sums[c]/float64(n)) + (1-lam)*gm
	}
	return out, gm
}

// autoSmooth smooth="auto" 的 m = σ_i²/τ²(逐类别求比后取平均)。
func autoSmooth(cats []string, y []float64) float64 {
	gm := mean(y)
	tau2 := 0.0
	for _, v := range y {
		tau2 += (v - gm) * (v - gm)
	}
	tau2 /= float64(len(y))
	byCat := map[string][]float64{}
	for i, c := range cats {
		byCat[c] = append(byCat[c], y[i])
	}
	s, k := 0.0, 0
	for _, ys := range byCat {
		if len(ys) < 2 {
			continue
		}
		mc := mean(ys)
		s2 := 0.0
		for _, v := range ys {
			s2 += (v - mc) * (v - mc)
		}
		s2 /= float64(len(ys))
		if tau2 > 0 {
			s += s2 / tau2
			k++
		}
	}
	if k == 0 {
		return 1
	}
	return s / float64(k)
}

// crossFittedEncoding 每一折的编码只用其余 K-1 折学习(sklearn fit_transform 的做法)。
func crossFittedEncoding(cats []string, y []float64, folds int, smooth float64, seed int64) ([]float64, map[string]float64, float64) {
	n := len(y)
	rng := rand.New(rand.NewSource(seed))
	idx := rng.Perm(n)
	enc := make([]float64, n)
	for k := 0; k < folds; k++ {
		lo, hi := k*n/folds, (k+1)*n/folds
		inVal := map[int]bool{}
		for _, p := range idx[lo:hi] {
			inVal[p] = true
		}
		var tc []string
		var ty []float64
		for i := 0; i < n; i++ {
			if !inVal[i] {
				tc = append(tc, cats[i])
				ty = append(ty, y[i])
			}
		}
		table, gm := targetEncoding(tc, ty, smooth)
		for p := lo; p < hi; p++ {
			i := idx[p]
			if v, ok := table[cats[i]]; ok {
				enc[i] = v
			} else {
				enc[i] = gm // 该折没见过这个类别 → 用全局均值
			}
		}
	}
	full, gm := targetEncoding(cats, y, smooth) // 供 transform 测试集使用
	return enc, full, gm
}

// orderedTargetStatistics 多个随机排列上取平均;每个样本只用排列中排在它前面的标签。
func orderedTargetStatistics(cats []string, y []float64, priorWeight float64, nPerm int, seed int64) []float64 {
	n := len(y)
	gm := mean(y)
	uniq := map[string]int{}
	for _, c := range cats {
		if _, ok := uniq[c]; !ok {
			uniq[c] = len(uniq)
		}
	}
	acc := make([][]float64, len(uniq))
	cnt := make([][]int, len(uniq))
	for i := range acc {
		acc[i] = make([]float64, nPerm)
		cnt[i] = make([]int, nPerm)
	}
	out := make([]float64, n)
	for r := 0; r < nPerm; r++ {
		order := rand.New(rand.NewSource(seed + int64(r))).Perm(n)
		for _, i := range order {
			ci := uniq[cats[i]]
			out[i] += (acc[ci][r] + priorWeight*gm) / (float64(cnt[ci][r]) + priorWeight)
			acc[ci][r] += y[i]
			cnt[ci][r]++
		}
	}
	for i := range out {
		out[i] /= float64(nPerm)
	}
	return out
}

// logregFit 全批量梯度下降的 L2 逻辑回归(够用来暴露编码差异)。
func logregFit(X [][]float64, y []float64, epochs int, lr float64) []float64 {
	n, p := len(X), len(X[0])+1
	w := make([]float64, p)
	for e := 0; e < epochs; e++ {
		g := make([]float64, p)
		for i := 0; i < n; i++ {
			z := w[0]
			for j := range X[i] {
				z += w[j+1] * X[i][j]
			}
			err := sigmoid(z) - y[i]
			g[0] += err
			for j := range X[i] {
				g[j+1] += err * X[i][j]
			}
		}
		for j := 0; j < p; j++ {
			w[j] -= lr * g[j] / float64(n)
		}
	}
	return w
}

func logregScore(w []float64, X [][]float64) []float64 {
	out := make([]float64, len(X))
	for i := range X {
		z := w[0]
		for j := range X[i] {
			z += w[j+1] * X[i][j]
		}
		out[i] = z
	}
	return out
}

func row(enc, signal float64) []float64 { return []float64{enc, signal} }

func pick[T any](v []T, idx []int) []T {
	out := make([]T, len(idx))
	for k, i := range idx {
		out[k] = v[i]
	}
	return out
}
