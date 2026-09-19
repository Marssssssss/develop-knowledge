// Package main:概率校准(Platt sigmoid / isotonic / temperature)与 Brier 分数分解的 Go 版。
//
// 权威依据(与 python/calibration.py 同源,URL 见同目录 README.md):
// scikit-learn《1.16. Probability calibration》与源码 `sklearn/calibration.py`
// (`_sigmoid_calibration` 的目标平滑、AB0、缩放阈值),Murphy 1973 的 Brier 分解。
package main

import "math"

const maxAbsPredictionThreshold = 30.0 // sklearn 源码 `_sigmoid_calibration` 的默认值

// brier mean((p − y)²)。
func brier(y, p []float64) float64 {
	s := 0.0
	for i := range y {
		s += (p[i] - y[i]) * (p[i] - y[i])
	}
	return s / float64(len(y))
}

// brierDecomposition 返回 (REL, RES, UNC):BS = REL − RES + UNC。
// 该恒等式对"箱内预测为常数"精确成立;箱内有波动时另有一余项。
func brierDecomposition(y, p []float64, bins int) (float64, float64, float64) {
	n := float64(len(y))
	oBar := 0.0
	for _, v := range y {
		oBar += v
	}
	oBar /= n
	type bucket struct{ p, y float64 }
	tab := map[int]bucket{} // 注意:必须用值类型,指针类型在键缺失时是 nil,取字段会 panic
	cnt := map[int]int{}
	for i := range y {
		b := int(p[i] * float64(bins))
		if b >= bins {
			b = bins - 1
		}
		v := tab[b]
		v.p += p[i]
		v.y += y[i]
		tab[b] = v
		cnt[b]++
	}
	rel, res := 0.0, 0.0
	for b, v := range tab {
		nb := float64(cnt[b])
		pBar := v.p / nb
		oB := v.y / nb
		rel += nb * (oB - pBar) * (oB - pBar)
		res += nb * (oB - oBar) * (oB - oBar)
	}
	return rel / n, res / n, oBar * (1 - oBar)
}

// calibrationCurve 返回 (箱内平均预测, 箱内真实正例率, 箱样本数)。
func calibrationCurve(y, p []float64, bins int) [][3]float64 {
	tab := map[int][3]float64{}
	for i := range y {
		b := int(p[i] * float64(bins))
		if b >= bins {
			b = bins - 1
		}
		v := tab[b]
		v[0] += p[i]
		v[1] += y[i]
		v[2]++
		tab[b] = v
	}
	keys := []int{}
	for k := range tab {
		keys = append(keys, k)
	}
	for i := 0; i < len(keys); i++ { // 按箱号升序
		for j := i + 1; j < len(keys); j++ {
			if keys[j] < keys[i] {
				keys[i], keys[j] = keys[j], keys[i]
			}
		}
	}
	out := [][3]float64{}
	for _, k := range keys {
		v := tab[k]
		out = append(out, [3]float64{v[0] / v[2], v[1] / v[2], v[2]})
	}
	return out
}

// auc 处理并列的 ROC-AUC(秩和)。
func auc(y, s []float64) float64 {
	idx := make([]int, len(s))
	for i := range idx {
		idx[i] = i
	}
	for i := 1; i < len(idx); i++ { // 插入排序
		for j := i; j > 0 && s[idx[j]] < s[idx[j-1]]; j-- {
			idx[j], idx[j-1] = idx[j-1], idx[j]
		}
	}
	ranks := make([]float64, len(s))
	i := 0
	for i < len(idx) {
		j := i
		for j+1 < len(idx) && s[idx[j+1]] == s[idx[i]] {
			j++
		}
		avg := (float64(i) + float64(j)) / 2 + 1
		for k := i; k <= j; k++ {
			ranks[idx[k]] = avg
		}
		i = j + 1
	}
	npos, nneg := 0.0, 0.0
	for _, v := range y {
		if v == 1 {
			npos++
		} else {
			nneg++
		}
	}
	if npos == 0 || nneg == 0 {
		return math.NaN()
	}
	sum := 0.0
	for i := range y {
		if y[i] == 1 {
			sum += ranks[i]
		}
	}
	return (sum - npos*(npos+1)/2) / (npos * nneg)
}

// plattTargets Platt 平滑目标:T[y>0]=(N_pos+1)/(N_pos+2),T[y≤0]=1/(N_neg+2)。
func plattTargets(y []float64) []float64 {
	npos, nneg := 0.0, 0.0
	for _, v := range y {
		if v > 0 {
			npos++
		} else {
			nneg++
		}
	}
	out := make([]float64, len(y))
	for i, v := range y {
		if v > 0 {
			out[i] = (npos + 1) / (npos + 2)
		} else {
			out[i] = 1 / (nneg + 2)
		}
	}
	return out
}

// plattInit AB0 = [0, log((N_neg+1)/(N_pos+1))]。
func plattInit(y []float64) (float64, float64) {
	npos, nneg := 0.0, 0.0
	for _, v := range y {
		if v > 0 {
			npos++
		} else {
			nneg++
		}
	}
	return 0.0, math.Log((nneg + 1) / (npos + 1))
}

// sigmoidScale |f| 超阈值时返回最大值作为缩放常数,否则返回 1。
func sigmoidScale(f []float64) float64 {
	m := 0.0
	for _, v := range f {
		if math.Abs(v) > m {
			m = math.Abs(v)
		}
	}
	if m < maxAbsPredictionThreshold {
		return 1.0
	}
	return m
}

// SigmoidCalibration Platt scaling:p = 1/(1+exp(A·f+B))。
type SigmoidCalibration struct {
	A, B float64
	lr_  float64
	iter int
}

func (c *SigmoidCalibration) Fit(f, y []float64) {
	T := plattTargets(y)
	A, B := plattInit(y)
	scale := sigmoidScale(f)
	F := make([]float64, len(f))
	for i := range f {
		F[i] = f[i] / scale
	}
	for it := 0; it < 2000; it++ {
		gA, gB := 0.0, 0.0
		for i := range F {
			p := 1 / (1 + math.Exp(-(A*F[i] + B)))
			d := p - T[i]
			gA += d * F[i]
			gB += d
		}
		A -= 0.1 * gA / float64(len(F))
		B -= 0.1 * gB / float64(len(F))
	}
	c.A, c.B = A/scale, B
}

func (c *SigmoidCalibration) Predict(f []float64) []float64 {
	out := make([]float64, len(f))
	for i := range f {
		out[i] = 1 / (1 + math.Exp(-(c.A*f[i] + c.B)))
	}
	return out
}

// temperatureScale p = σ(z / T)。
func temperatureScale(z []float64, T float64) []float64 {
	out := make([]float64, len(z))
	for i := range z {
		out[i] = 1 / (1 + math.Exp(-(z[i] / T)))
	}
	return out
}

// fitTemperature 三分法搜 T,最小化 log loss。
func fitTemperature(z, y []float64) float64 {
	loss := func(T float64) float64 {
		s := 0.0
		for i := range z {
			p := 1 / (1 + math.Exp(-(z[i] / T)))
			if p < 1e-12 {
				p = 1e-12
			}
			if p > 1-1e-12 {
				p = 1 - 1e-12
			}
			s -= y[i]*math.Log(p) + (1-y[i])*math.Log(1-p)
		}
		return s / float64(len(z))
	}
	lo, hi := 0.05, 20.0
	for it := 0; it < 200; it++ {
		m1 := lo + (hi-lo)/3
		m2 := hi - (hi-lo)/3
		if loss(m1) < loss(m2) {
			hi = m2
		} else {
			lo = m1
		}
	}
	return (lo + hi) / 2
}
