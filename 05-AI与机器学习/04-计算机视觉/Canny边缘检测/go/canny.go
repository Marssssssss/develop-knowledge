// Canny 边缘检测的 Go 实现(与 python/canny.py 同题)。
//
// 文件划分:本文件只放流水线实现(高斯核 / Sobel / 量化 / NMS / 滞后阈值),
// 断言与入口在同包的 main.go 里 —— Go 单文件行数上限 300(OPTIMIZATION.md §1.1)。
//
// 运行:cd go && go run .
package main

import (
	"fmt"
	"math"
)

// sobelX / sobelY 是 OpenCV 文档给出的标准 3x3 Sobel 核。
var sobelX = [3][3]float64{{-1, 0, 1}, {-2, 0, 2}, {-1, 0, 1}}
var sobelY = [3][3]float64{{-1, -2, -1}, {0, 0, 0}, {1, 2, 1}}

// gaussian5x5Int 是 OpenCV 3.4 Canny 教程给出的整数核,除数为 159。
var gaussian5x5Int = [5][5]int{
	{2, 4, 5, 4, 2},
	{4, 9, 12, 9, 4},
	{5, 12, 15, 12, 5},
	{4, 9, 12, 9, 4},
	{2, 4, 5, 4, 2},
}

const gaussianDivisor = 159

// nmsOffsets 把量化后的 4 个梯度方向映射到该方向上要比较的两个邻居偏移。
// 索引 0/1/2/3 分别对应 0/45/90/135 度。
var nmsOffsets = [4][2][2]int{
	{{1, 0}, {-1, 0}},  // 0 度:水平梯度 -> 垂直边缘
	{{1, 1}, {-1, -1}}, // 45 度
	{{0, 1}, {0, -1}},  // 90 度:垂直梯度 -> 水平边缘
	{{-1, 1}, {1, -1}}, // 135 度
}

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

func clampInt(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// correlate 通用奇数尺寸相关运算,clamp 复制边界(与 Python 版一致)。
func correlate(img [][]float64, kernel [][]float64) [][]float64 {
	h, w := len(img), len(img[0])
	k := len(kernel)
	half := k / 2
	out := make([][]float64, h)
	for y := 0; y < h; y++ {
		out[y] = make([]float64, w)
		for x := 0; x < w; x++ {
			acc := 0.0
			for ky := 0; ky < k; ky++ {
				yy := clampInt(y+ky-half, 0, h-1)
				for kx := 0; kx < k; kx++ {
					xx := clampInt(x+kx-half, 0, w-1)
					acc += kernel[ky][kx] * img[yy][xx]
				}
			}
			out[y][x] = acc
		}
	}
	return out
}

func gaussianKernel(size int, sigma float64) [][]float64 {
	half := size / 2
	flat := make([]float64, size)
	sum := 0.0
	for i := 0; i < size; i++ {
		d := float64(i - half)
		flat[i] = math.Exp(-(d * d) / (2 * sigma * sigma))
		sum += flat[i]
	}
	for i := range flat {
		flat[i] /= sum
	}
	k := make([][]float64, size)
	for i := 0; i < size; i++ {
		k[i] = make([]float64, size)
		for j := 0; j < size; j++ {
			k[i][j] = flat[i] * flat[j]
		}
	}
	return k
}

func toFloatGrid(m [3][3]float64) [][]float64 {
	out := make([][]float64, 3)
	for i := 0; i < 3; i++ {
		out[i] = []float64{m[i][0], m[i][1], m[i][2]}
	}
	return out
}

func blur(img [][]float64) [][]float64 { return correlate(img, gaussianKernel(5, 1.4)) }

func sobel(img [][]float64) ([][]float64, [][]float64) {
	return correlate(img, toFloatGrid(sobelX)), correlate(img, toFloatGrid(sobelY))
}

func magnitude(gx, gy [][]float64, l2 bool) [][]float64 {
	h, w := len(gx), len(gx[0])
	out := make([][]float64, h)
	for y := 0; y < h; y++ {
		out[y] = make([]float64, w)
		for x := 0; x < w; x++ {
			if l2 {
				out[y][x] = math.Hypot(gx[y][x], gy[y][x])
			} else {
				out[y][x] = math.Abs(gx[y][x]) + math.Abs(gy[y][x])
			}
		}
	}
	return out
}

// angle180 返回 [0,180) 的梯度方向(度)。
func angle180(gx, gy float64) float64 {
	a := math.Atan2(gy, gx) * 180.0 / math.Pi
	if a < 0 {
		a += 180.0
	}
	if a >= 180.0 {
		a -= 180.0
	}
	return a
}

// sector 把方向量化到 0/45/90/135,返回 0..3 的档位。
func sector(deg float64) int {
	switch {
	case deg < 22.5 || deg >= 157.5:
		return 0
	case deg < 67.5:
		return 1
	case deg < 112.5:
		return 2
	default:
		return 3
	}
}

// nonMax 沿梯度方向保留局部极大。epsRatio 是浮点残差护栏,0 表示关闭。
func nonMax(mag [][]float64, sec [][]int, epsRatio float64) [][]float64 {
	h, w := len(mag), len(mag[0])
	peak := 0.0
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			if mag[y][x] > peak {
				peak = mag[y][x]
			}
		}
	}
	eps := peak * epsRatio
	out := make([][]float64, h)
	for y := 0; y < h; y++ {
		out[y] = make([]float64, w)
		for x := 0; x < w; x++ {
			m := mag[y][x]
			if m <= eps {
				continue
			}
			o := nmsOffsets[sec[y][x]]
			n1, n2 := 0.0, 0.0
			if xx, yy := x+o[0][0], y+o[0][1]; xx >= 0 && xx < w && yy >= 0 && yy < h {
				n1 = mag[yy][xx]
			}
			if xx, yy := x+o[1][0], y+o[1][1]; xx >= 0 && xx < w && yy >= 0 && yy < h {
				n2 = mag[yy][xx]
			}
			// 平局规则:先出现的邻居必须严格更小,后出现的允许相等。
			if m > n1 && m >= n2 {
				out[y][x] = m
			}
		}
	}
	return out
}

// hysteresis 双阈值 + 滞后,返回 0/1 边缘图。8 连通。
func hysteresis(mag [][]float64, low, high float64) [][]int {
	h, w := len(mag), len(mag[0])
	const weak, strong = 1, 2
	state := make([][]int, h)
	stack := make([][2]int, 0)
	for y := 0; y < h; y++ {
		state[y] = make([]int, w)
		for x := 0; x < w; x++ {
			if mag[y][x] >= high {
				state[y][x] = strong
				stack = append(stack, [2]int{x, y})
			} else if mag[y][x] >= low {
				state[y][x] = weak
			}
		}
	}
	for len(stack) > 0 {
		p := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		for dy := -1; dy <= 1; dy++ {
			for dx := -1; dx <= 1; dx++ {
				if dx == 0 && dy == 0 {
					continue
				}
				xx, yy := p[0]+dx, p[1]+dy
				if xx >= 0 && xx < w && yy >= 0 && yy < h && state[yy][xx] == weak {
					state[yy][xx] = strong
					stack = append(stack, [2]int{xx, yy})
				}
			}
		}
	}
	out := make([][]int, h)
	for y := 0; y < h; y++ {
		out[y] = make([]int, w)
		for x := 0; x < w; x++ {
			if state[y][x] == strong {
				out[y][x] = 1
			}
		}
	}
	return out
}

func canny(img [][]float64, low, high float64) [][]int {
	gx, gy := sobel(blur(img))
	mag := magnitude(gx, gy, false)
	h, w := len(gx), len(gx[0])
	sec := make([][]int, h)
	for y := 0; y < h; y++ {
		sec[y] = make([]int, w)
		for x := 0; x < w; x++ {
			sec[y][x] = sector(angle180(gx[y][x], gy[y][x]))
		}
	}
	return hysteresis(nonMax(mag, sec, 1e-9), low, high)
}

func stepImage(h, w, x0 int) [][]float64 {
	img := make([][]float64, h)
	for y := 0; y < h; y++ {
		img[y] = make([]float64, w)
		for x := 0; x < w; x++ {
			if x >= x0 {
				img[y][x] = 200
			}
		}
	}
	return img
}

func countEdges(e [][]int) int {
	n := 0
	for _, row := range e {
		for _, v := range row {
			n += v
		}
	}
	return n
}
