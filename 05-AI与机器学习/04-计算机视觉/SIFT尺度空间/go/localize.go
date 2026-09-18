// SIFT 第 4 步:关键点亚像素定位(Taylor 展开 + 低对比度剔除)。
//
// 论文口径(Lowe IJCV 2004 §4):
//   - 对 D(x) 做二阶 Taylor 展开,令导数为零得偏移 x̂ = -H⁻¹(∂D/∂x);
//   - 若 x̂ 在任一维 > 0.5,说明真极值更靠近相邻采样点,须换点重做插值(本 demo 只做标记);
//   - 插值后的极值取值 D(x̂) = D + ½(∂D/∂x)ᵀx̂,|D(x̂)| < 0.03 的极值视为低对比度丢弃。
package main

import "math"

// solve3 3x3 线性方程组的高斯消元;奇异或接近奇异返回 false。
func solve3(a [3][3]float64, b [3]float64) ([3]float64, bool) {
	var m [3][4]float64
	for i := 0; i < 3; i++ {
		m[i][0], m[i][1], m[i][2], m[i][3] = a[i][0], a[i][1], a[i][2], b[i]
	}
	for col := 0; col < 3; col++ {
		piv := col
		for r := col + 1; r < 3; r++ {
			if math.Abs(m[r][col]) > math.Abs(m[piv][col]) {
				piv = r
			}
		}
		if math.Abs(m[piv][col]) < 1e-18 {
			return [3]float64{}, false
		}
		m[col], m[piv] = m[piv], m[col]
		for r := 0; r < 3; r++ {
			if r == col {
				continue
			}
			f := m[r][col] / m[col][col]
			for c := col; c < 4; c++ {
				m[r][c] -= f * m[col][c]
			}
		}
	}
	return [3]float64{m[0][3] / m[0][0], m[1][3] / m[1][1], m[2][3] / m[2][2]}, true
}

// LocalizeResult 亚像素定位结果。Offset 顺序为 (Δs, Δy, Δx)。
type LocalizeResult struct {
	OK           bool
	Offset       [3]float64
	NeedResample bool
	DHat         float64
	ContrastOK   bool
}

// Localize 亚像素定位 + 低对比度剔除:x̂ = -H⁻¹(∂D/∂x),D(x̂) = D + ½(∂D/∂x)ᵀx̂(论文式 (2)(3))。
//
// 导数用邻居差分近似(论文:derivatives approximated by differences of neighbours)。
func Localize(vol [][][]float64, s, y, x int, contrastThreshold float64) LocalizeResult {
	g := [3]float64{
		(vol[s+1][y][x] - vol[s-1][y][x]) * 0.5,
		(vol[s][y+1][x] - vol[s][y-1][x]) * 0.5,
		(vol[s][y][x+1] - vol[s][y][x-1]) * 0.5,
	}
	dss := vol[s+1][y][x] - 2.0*vol[s][y][x] + vol[s-1][y][x]
	dyy := vol[s][y+1][x] - 2.0*vol[s][y][x] + vol[s][y-1][x]
	dxx := vol[s][y][x+1] - 2.0*vol[s][y][x] + vol[s][y][x-1]
	dsy := (vol[s+1][y+1][x] - vol[s+1][y-1][x] - vol[s-1][y+1][x] + vol[s-1][y-1][x]) * 0.25
	dsx := (vol[s+1][y][x+1] - vol[s+1][y][x-1] - vol[s-1][y][x+1] + vol[s-1][y][x-1]) * 0.25
	dyx := (vol[s][y+1][x+1] - vol[s][y+1][x-1] - vol[s][y-1][x+1] + vol[s][y-1][x-1]) * 0.25
	h := [3][3]float64{{dss, dsy, dsx}, {dsy, dyy, dyx}, {dsx, dyx, dxx}}
	off, ok := solve3(h, [3]float64{-g[0], -g[1], -g[2]})
	if !ok {
		return LocalizeResult{OK: false, DHat: vol[s][y][x]}
	}
	need := math.Abs(off[0]) > 0.5 || math.Abs(off[1]) > 0.5 || math.Abs(off[2]) > 0.5
	dHat := vol[s][y][x] + 0.5*(g[0]*off[0]+g[1]*off[1]+g[2]*off[2])
	return LocalizeResult{OK: true, Offset: off, NeedResample: need, DHat: dHat,
		ContrastOK: math.Abs(dHat) >= contrastThreshold}
}
