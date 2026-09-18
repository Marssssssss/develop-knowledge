// SIFT 第 1~3 步:构建高斯/DoG 金字塔 → 尺度空间极值检测。
//
// 权威口径:D.Lowe《Distinctive Image Features from Scale-Invariant Keypoints》
// (IJCV 2004)+ OpenCV 4.x tutorial_py_sift_intro,与 python/sift.py 一一对应。
//
// 论文中的可验证结论(见 main.go 的断言):
//   - 尺度空间核只能是高斯(Koenderink 1984 / Lindeberg 1994);L(x,y,σ) = G(x,y,σ) * I(x,y)
//   - DoG 近似尺度归一化 LoG:D = L(kσ) - L(σ) ≈ (k-1)σ²∇²G * I(由热方程 ∂G/∂σ = σ∇²G 推出);
//     因子 (k-1) 与尺度无关,不影响极值位置
//   - k = 2^(1/s);每个 octave 必须生成 s+3 张模糊图才能覆盖完整 octave
//   - 极值判据:同层 8 邻居 + 上下层各 9 邻居 = 26 个
//   - 经验参数:每 octave 采样 3 个尺度时重复性最高;初始 σ = 1.6;octaves = 4
//
// 本文件只放金字塔与极值检测;亚像素定位见 localize.go,边缘剔除见 keypoint.go。
package main

import "math"

const (
	// SiftSigma 论文:把输入图预模糊到该尺度。
	SiftSigma = 1.6
	// SiftAssumedBlur 论文:假设输入图本身已被 σ=0.5 的高斯模糊。
	SiftAssumedBlur = 0.5
	// ScalesPerOctave 论文实验:每 octave 采样 3 个尺度时重复性最高。
	ScalesPerOctave = 3
	// ContrastThreshold |D(x̂)| 低于此值的极值丢弃。
	ContrastThreshold = 0.03
	// EdgeRatio 论文:主曲率比 r 超过此值的关键点丢弃。
	EdgeRatio = 10.0
	// DefaultOctaves 论文给出的经验值。
	DefaultOctaves = 4
)

// Gaussian1D 返回归一化的一维高斯核;radius <= 0 时取 3σ(截断到 0.3% 能量之外)。
func Gaussian1D(sigma float64, radius int) []float64 {
	r := radius
	if r <= 0 {
		r = int(math.Ceil(3.0 * sigma))
	}
	k := make([]float64, 2*r+1)
	total := 0.0
	for i := -r; i <= r; i++ {
		v := math.Exp(-(float64(i) * float64(i)) / (2.0 * sigma * sigma))
		k[i+r] = v
		total += v
	}
	for i := range k {
		k[i] /= total
	}
	return k
}

// Blur1D 沿 axis(0=行/y,1=列/x)做一维卷积,边界 clamp。
func Blur1D(img [][]float64, kernel []float64, axis int) [][]float64 {
	h, w := len(img), len(img[0])
	r := len(kernel) / 2
	out := make([][]float64, h)
	for y := 0; y < h; y++ {
		row := make([]float64, w)
		for x := 0; x < w; x++ {
			acc := 0.0
			for i, kv := range kernel {
				if axis == 0 {
					yy := y + i - r
					if yy < 0 {
						yy = 0
					} else if yy > h-1 {
						yy = h - 1
					}
					acc += kv * img[yy][x]
				} else {
					xx := x + i - r
					if xx < 0 {
						xx = 0
					} else if xx > w-1 {
						xx = w - 1
					}
					acc += kv * img[y][xx]
				}
			}
			row[x] = acc
		}
		out[y] = row
	}
	return out
}

// GaussianBlur 可分离二维高斯模糊(先列后行);σ <= 0 时原样返回。
func GaussianBlur(img [][]float64, sigma float64) [][]float64 {
	if sigma <= 0.0 {
		out := make([][]float64, len(img))
		for i := range img {
			out[i] = append([]float64(nil), img[i]...)
		}
		return out
	}
	k := Gaussian1D(sigma, 0)
	return Blur1D(Blur1D(img, k, 1), k, 0)
}

// HalfSize 按行列隔点重采样(论文:after each octave, down-sample by 2)。
func HalfSize(img [][]float64) [][]float64 {
	h, w := len(img), len(img[0])
	out := make([][]float64, h/2)
	for y := 0; y < h/2; y++ {
		row := make([]float64, w/2)
		for x := 0; x < w/2; x++ {
			row[x] = img[2*y][2*x]
		}
		out[y] = row
	}
	return out
}

// IncrementalSigma 把已模糊到 base 的图再模糊到 target 所需的增量 σ = sqrt(target² - base²)。
func IncrementalSigma(baseOctaveSigma, targetSigma float64) float64 {
	d := targetSigma*targetSigma - baseOctaveSigma*baseOctaveSigma
	if d > 0.0 {
		return math.Sqrt(d)
	}
	return 0.0
}

// BuildGaussianPyramid 返回 octaves 个 octave,每个含 s+3 张模糊图。
//
// 每个 octave 的 σ 序列为 σ, σk, σk², …, σk^(s+2);降采样取第 s 张(σ 恰为 2σ),
// 降采样后有效 σ 回到 σ,于是下一个 octave 保持相同的采样精度而计算量减半。
func BuildGaussianPyramid(img [][]float64, octaves, s int, sigma, assumedBlur float64) [][][][]float64 {
	k := math.Pow(2.0, 1.0/float64(s))
	sigmas := make([]float64, s+3)
	for i := range sigmas {
		sigmas[i] = sigma * math.Pow(k, float64(i))
	}
	current := GaussianBlur(img, IncrementalSigma(assumedBlur, sigma))
	pyramid := make([][][][]float64, 0, octaves)
	for o := 0; o < octaves; o++ {
		stack := make([][][]float64, 0, s+3)
		stack = append(stack, current)
		for i := 1; i < s+3; i++ {
			stack = append(stack, GaussianBlur(stack[i-1], IncrementalSigma(sigmas[i-1], sigmas[i])))
		}
		pyramid = append(pyramid, stack)
		current = HalfSize(stack[s])
	}
	return pyramid
}

// BuildDoGPyramid 同 octave 内相邻两张模糊图相减,得 s+2 张 DoG。
func BuildDoGPyramid(pyramid [][][][]float64) [][][][]float64 {
	dogs := make([][][][]float64, 0, len(pyramid))
	for _, stack := range pyramid {
		h, w := len(stack[0]), len(stack[0][0])
		octave := make([][][]float64, 0, len(stack)-1)
		for i := 0; i+1 < len(stack); i++ {
			a, b := stack[i], stack[i+1]
			plane := make([][]float64, h)
			for y := 0; y < h; y++ {
				row := make([]float64, w)
				for x := 0; x < w; x++ {
					row[x] = b[y][x] - a[y][x]
				}
				plane[y] = row
			}
			octave = append(octave, plane)
		}
		dogs = append(dogs, octave)
	}
	return dogs
}

// Extremum 尺度空间极值点(采样点坐标)。
type Extremum struct {
	Octave, S, Y, X int
	Value           float64
}

// FindExtrema 与 3x3x3 的 26 个邻居比较;首尾层天然凑不齐 26 邻居,故被排除。
func FindExtrema(dogs [][][][]float64) []Extremum {
	found := []Extremum{}
	for oi, octave := range dogs {
		n := len(octave)
		h, w := len(octave[0]), len(octave[0][0])
		for s := 1; s < n-1; s++ {
			for y := 1; y < h-1; y++ {
				for x := 1; x < w-1; x++ {
					v := octave[s][y][x]
					isMax, isMin := true, true
					for ds := -1; ds <= 1; ds++ {
						for dy := -1; dy <= 1; dy++ {
							for dx := -1; dx <= 1; dx++ {
								if ds == 0 && dy == 0 && dx == 0 {
									continue
								}
								nv := octave[s+ds][y+dy][x+dx]
								if nv >= v {
									isMax = false
								}
								if nv <= v {
									isMin = false
								}
							}
						}
					}
					if isMax || isMin {
						found = append(found, Extremum{oi, s, y, x, v})
					}
				}
			}
		}
	}
	return found
}
