// Harris 角点检测的 Go 实现(与 python/harris.py 同题)。
//
// 文件划分:本文件只放实现(相关运算 / 结构张量 / 响应 / 簇合并);断言与入口在同包
// 的 main.go 里 —— 单源文件 ≤ 300 行(OPTIMIZATION.md §1.1)。
//
// 口径差异:Go 版只实现**矩形窗**(OpenCV 教程说窗口函数可为矩形窗或高斯窗);
// 高斯窗与亚像素精化的量化结论留给 Python 版。
//
// 运行:cd go && go run .
package main

import "math"

var sobelX = [3][3]float64{{-1, 0, 1}, {-2, 0, 2}, {-1, 0, 1}}
var sobelY = [3][3]float64{{-1, -2, -1}, {0, 0, 0}, {1, 2, 1}}

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// correlate3 用 3x3 核做相关运算(clamp 边界)。
func correlate3(img [][]float64, k [3][3]float64) [][]float64 {
	h, w := len(img), len(img[0])
	out := make([][]float64, h)
	for y := 0; y < h; y++ {
		out[y] = make([]float64, w)
		for x := 0; x < w; x++ {
			acc := 0.0
			for ky := 0; ky < 3; ky++ {
				row := img[clampInt(y+ky-1, 0, h-1)]
				for kx := 0; kx < 3; kx++ {
					acc += k[ky][kx] * row[clampInt(x+kx-1, 0, w-1)]
				}
			}
			out[y][x] = acc
		}
	}
	return out
}

func sobelGradients(img [][]float64) ([][]float64, [][]float64) {
	return correlate3(img, sobelX), correlate3(img, sobelY)
}

// structureTensor 返回 (Sxx, Sxy, Syy) = Σ w * (Ix², IxIy, Iy²),w 为归一化矩形窗。
func structureTensor(gx, gy [][]float64, blockSize int) ([][]float64, [][]float64, [][]float64) {
	r := blockSize
	size := 2*r + 1
	wv := 1.0 / float64(size*size)
	h, w := len(gx), len(gx[0])
	sxx := make([][]float64, h)
	sxy := make([][]float64, h)
	syy := make([][]float64, h)
	for y := 0; y < h; y++ {
		sxx[y] = make([]float64, w)
		sxy[y] = make([]float64, w)
		syy[y] = make([]float64, w)
		for x := 0; x < w; x++ {
			a, b, c := 0.0, 0.0, 0.0
			for dy := -r; dy <= r; dy++ {
				row := clampInt(y+dy, 0, h-1)
				for dx := -r; dx <= r; dx++ {
					col := clampInt(x+dx, 0, w-1)
					ix, iy := gx[row][col], gy[row][col]
					a += wv * ix * ix
					b += wv * ix * iy
					c += wv * iy * iy
				}
			}
			sxx[y][x], sxy[y][x], syy[y][x] = a, b, c
		}
	}
	return sxx, sxy, syy
}

// eigenvalues 返回对称 2x2 矩阵的 (λmax, λmin)。
func eigenvalues(sxx, sxy, syy float64) (float64, float64) {
	mid := 0.5 * (sxx + syy)
	dif := 0.5 * (sxx - syy)
	rad := math.Sqrt(dif*dif + sxy*sxy)
	return mid + rad, mid - rad
}

// harrisR = det(M) - k*trace(M)²。
func harrisR(sxx, sxy, syy, k float64) float64 {
	return sxx*syy - sxy*sxy - k*(sxx+syy)*(sxx+syy)
}

// shiTomasi = min(λ1, λ2)。
func shiTomasi(sxx, sxy, syy float64) float64 {
	_, lo := eigenvalues(sxx, sxy, syy)
	return lo
}

// responseImage 从灰度图算响应图。method 取 "harris" 或 "shi_tomasi"。
func responseImage(img [][]float64, blockSize int, k float64, method string) [][]float64 {
	gx, gy := sobelGradients(img)
	sxx, sxy, syy := structureTensor(gx, gy, blockSize)
	h, w := len(img), len(img[0])
	out := make([][]float64, h)
	for y := 0; y < h; y++ {
		out[y] = make([]float64, w)
		for x := 0; x < w; x++ {
			if method == "shi_tomasi" {
				out[y][x] = shiTomasi(sxx[y][x], sxy[y][x], syy[y][x])
			} else {
				out[y][x] = harrisR(sxx[y][x], sxy[y][x], syy[y][x], k)
			}
		}
	}
	return out
}

func peakOf(resp [][]float64) float64 {
	peak := 0.0
	for _, row := range resp {
		for _, v := range row {
			if v > peak {
				peak = v
			}
		}
	}
	return peak
}

// findCorners 相对阈值 + 邻域非极大值抑制,返回 [(x, y, 响应)]。
func findCorners(resp [][]float64, ratio float64, minDistance int) [][3]float64 {
	thr := ratio * peakOf(resp)
	h, w := len(resp), len(resp[0])
	res := make([][3]float64, 0)
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			v := resp[y][x]
			if v <= thr {
				continue
			}
			local := true
			for dy := -minDistance; dy <= minDistance && local; dy++ {
				yy := y + dy
				if yy < 0 || yy >= h {
					continue
				}
				for dx := -minDistance; dx <= minDistance; dx++ {
					xx := x + dx
					if xx < 0 || xx >= w {
						continue
					}
					if resp[yy][xx] > v {
						local = false
						break
					}
				}
			}
			if local {
				res = append(res, [3]float64{float64(x), float64(y), v})
			}
		}
	}
	return res
}

// clusterCandidates 把候选像素按 8 连通合并成簇,返回 [(质心x, 质心y, 峰值, 像素数)]。
func clusterCandidates(resp [][]float64, ratio float64) [][4]float64 {
	thr := ratio * peakOf(resp)
	h, w := len(resp), len(resp[0])
	seen := make([][]bool, h)
	for y := 0; y < h; y++ {
		seen[y] = make([]bool, w)
	}
	res := make([][4]float64, 0)
	for sy := 0; sy < h; sy++ {
		for sx := 0; sx < w; sx++ {
			if seen[sy][sx] || resp[sy][sx] <= thr {
				continue
			}
			stack := [][2]int{{sx, sy}}
			seen[sy][sx] = true
			sxSum, sySum, n := 0, 0, 0
			peak := 0.0
			for len(stack) > 0 {
				p := stack[len(stack)-1]
				stack = stack[:len(stack)-1]
				sxSum += p[0]
				sySum += p[1]
				n++
				if resp[p[1]][p[0]] > peak {
					peak = resp[p[1]][p[0]]
				}
				for dy := -1; dy <= 1; dy++ {
					for dx := -1; dx <= 1; dx++ {
						xx, yy := p[0]+dx, p[1]+dy
						if xx >= 0 && xx < w && yy >= 0 && yy < h && !seen[yy][xx] && resp[yy][xx] > thr {
							seen[yy][xx] = true
							stack = append(stack, [2]int{xx, yy})
						}
					}
				}
			}
			res = append(res, [4]float64{float64(sxSum) / float64(n), float64(sySum) / float64(n),
				peak, float64(n)})
		}
	}
	return res
}

// minLambda 返回全图最小的 λmin,用于验证结构张量的半正定性(λmin 恒 >= 0)。
func minLambda(img [][]float64, blockSize int) float64 {
	gx, gy := sobelGradients(img)
	sxx, sxy, syy := structureTensor(gx, gy, blockSize)
	best := math.MaxFloat64
	for y := range sxx {
		for x := range sxx[y] {
			_, lo := eigenvalues(sxx[y][x], sxy[y][x], syy[y][x])
			if lo < best {
				best = lo
			}
		}
	}
	return best
}

func checkerboard(n int, v float64) [][]float64 {
	c := n / 2
	img := make([][]float64, n)
	for y := 0; y < n; y++ {
		img[y] = make([]float64, n)
		for x := 0; x < n; x++ {
			if (x >= c) != (y >= c) {
				img[y][x] = v
			}
		}
	}
	return img
}

func lCorner(n, px int, v float64) [][]float64 {
	img := make([][]float64, n)
	for y := 0; y < n; y++ {
		img[y] = make([]float64, n)
	}
	for k := 0; k < 3; k++ {
		img[px][px+k] = v
		img[px+k][px] = v
	}
	return img
}

func upscale(m [][]float64, f int) [][]float64 {
	n := len(m) * f
	out := make([][]float64, n)
	for y := 0; y < n; y++ {
		out[y] = make([]float64, n)
		for x := 0; x < n; x++ {
			out[y][x] = m[y/f][x/f]
		}
	}
	return out
}

// rot90 逆时针 90 度:new[i][j] = old[n-1-j][i]。
func rot90(m [][]float64) [][]float64 {
	n := len(m)
	out := make([][]float64, n)
	for i := 0; i < n; i++ {
		out[i] = make([]float64, n)
		for j := 0; j < n; j++ {
			out[i][j] = m[n-1-j][i]
		}
	}
	return out
}
