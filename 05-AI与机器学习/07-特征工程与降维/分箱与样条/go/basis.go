package main

// B-spline 基的求值:与 python/spline_core.py 前半部分同口径。
// 对齐对象:scipy `BSpline`(含 extrapolate=True 的端外多项式延拓)。

// CoxDeBoor 是 B-spline 基的 Cox-de Boor 递推。
// leftClosed=true 时 degree-0 基用 t[i] < x <= t[i+1](取左极限);
// 分母为 0 的项取 0(重结情形)。
func CoxDeBoor(t []float64, degree int, x float64, leftClosed bool) []float64 {
	n := len(t) - degree - 1
	out := make([]float64, n)
	if degree == 0 {
		for i := 0; i < n; i++ {
			if leftClosed {
				if t[i] < x && x <= t[i+1] {
					out[i] = 1
				}
			} else if t[i] <= x && x < t[i+1] {
				out[i] = 1
			}
		}
		return out
	}
	prev := CoxDeBoor(t, degree-1, x, leftClosed)
	for i := 0; i < n; i++ {
		d1 := t[i+degree] - t[i]
		d2 := t[i+degree+1] - t[i+1]
		a, b := 0.0, 0.0
		if d1 > 0 {
			a = (x - t[i]) / d1 * prev[i]
		}
		if d2 > 0 {
			b = (t[i+degree+1] - x) / d2 * prev[i+1]
		}
		out[i] = a + b
	}
	return out
}

func lagrange(nodes, values []float64, x float64) float64 {
	total := 0.0
	for i, xi := range nodes {
		term := values[i]
		for j, xj := range nodes {
			if j != i {
				term *= (x - xj) / (xi - xj)
			}
		}
		total += term
	}
	return total
}

// BsplineValues 给出长度 len(t)-degree-1 的基函数值向量。
// 区间外且 extrapolate=true 时,在端 span 内取 degree+1 个采样点做拉格朗日延拓
// (该 span 上每个基都是次数 <= degree 的多项式,故 degree+1 个值足以唯一确定)。
// 注意两端所用的 span **不对称**(与 scipy find_interval 一致):
// 下端 x < t[k] 用 [t[k], t[k+1]],上端 x > t[n] 用 [t[n-1], t[n]]。
func BsplineValues(t []float64, degree int, x float64, extrapolate bool) []float64 {
	n := len(t) - degree - 1
	if extrapolate && degree >= 1 && (x < t[degree] || x > t[n]) {
		var lo, hi float64
		if x < t[degree] {
			lo, hi = t[degree], t[degree+1]
		} else {
			lo, hi = t[n-1], t[n]
		}
		if hi <= lo {
			return make([]float64, n)
		}
		span := hi - lo
		nodes := make([]float64, degree+1)
		for k := range nodes {
			nodes[k] = lo + span*float64(k+1)/float64(degree+2)
		}
		cols := make([][]float64, len(nodes))
		for i, nd := range nodes {
			cols[i] = CoxDeBoor(t, degree, nd, false)
		}
		out := make([]float64, n)
		for i := 0; i < n; i++ {
			vals := make([]float64, len(nodes))
			for k := range cols {
				vals[k] = cols[k][i]
			}
			out[i] = lagrange(nodes, vals, x)
		}
		return out
	}
	return CoxDeBoor(t, degree, x, false)
}

// BsplineDerivative 是一阶导:
// d/dx B_{i,k} = k*(B_{i,k-1}/(t_{i+k}-t_i) - B_{i+1,k-1}/(t_{i+k+1}-t_{i+1}))。
// 在最右结 t[n] 上 scipy 取左单侧导;k>=2 时一阶导连续取哪侧都一样,
// 只有 k=1 的基在结处是 C^0,方向会改变 extrapolation='linear' 的斜率。
func BsplineDerivative(t []float64, degree int, x float64) []float64 {
	n := len(t) - degree - 1
	if degree == 0 {
		return make([]float64, n)
	}
	low := CoxDeBoor(t, degree-1, x, x == t[n])
	out := make([]float64, n)
	for i := 0; i < n; i++ {
		d1 := t[i+degree] - t[i]
		d2 := t[i+degree+1] - t[i+1]
		a, b := 0.0, 0.0
		if d1 > 0 {
			a = low[i] / d1
		}
		if d2 > 0 {
			b = low[i+1] / d2
		}
		out[i] = float64(degree) * (a - b)
	}
	return out
}
