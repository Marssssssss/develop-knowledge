// SIFT 第 5~6 步:方向分配 + 128 维描述子 + 0.8 比率匹配。
//
// 论文口径(Lowe IJCV 2004 §5、§6),与 python/descriptor.py 一一对应:
//   - 方向直方图 36 个 bin 覆盖 360°(每 bin 10°),样本按梯度幅值加权,
//     再乘 σ = 1.5 × 关键点尺度 的高斯圆形窗;
//   - 取最高峰,任何达到最高峰 80% 的局部峰也各生成一个关键点(同位置同尺度、不同方向);
//     论文实测仅约 15% 的点被赋多个方向,但它们显著提升匹配稳定性;
//   - 峰附近 3 个直方图值拟合抛物线做亚 bin 插值;
//   - 描述子:16x16 采样 → 4x4 子块 × 8 方向 = 128 维,子块内三线性插值(权重 1-d);
//   - 高斯窗 σ 取描述子窗口宽度的一半;
//   - 光照不变:先单位化(抵消对比度线性变化),再把分量截到 0.2,再重新单位化;
//   - 匹配:最近邻/次近邻距离比 > 0.8 拒绝,论文称可剔除约 90% 错误匹配、只丢不到 5% 正确匹配。
package main

import (
	"math"
	"sort"
)

const (
	// OrientationBins 360° / 10°。
	OrientationBins = 36
	// PeakRatio 达到最高峰 80% 的局部峰也生成关键点。
	PeakRatio = 0.8
	// DescWidth 4x4 子块。
	DescWidth = 4
	// DescBins 每块 8 个方向。
	DescBins = 8
	// DescSamples 16x16 采样。
	DescSamples = 16
	// DescClamp 截断阈值。
	DescClamp = 0.2
	// MatchRatio 最近邻/次近邻距离比阈值。
	MatchRatio = 0.8
)

// OrientationHistogram 统计 36 个 bin 的方向直方图;angs 为梯度方向(度)。
func OrientationHistogram(mags, angs, weights []float64) []float64 {
	hist := make([]float64, OrientationBins)
	for i := range mags {
		a := math.Mod(angs[i], 360.0)
		if a < 0 {
			a += 360.0
		}
		idx := int(a/(360.0/float64(OrientationBins))) % OrientationBins
		hist[idx] += mags[i] * weights[i]
	}
	return hist
}

// GaussianCircleWeights σ = 1.5 × scale 的圆形高斯窗(论文 §5)。
func GaussianCircleWeights(offsets [][2]float64, sigma float64) []float64 {
	out := make([]float64, len(offsets))
	for i, o := range offsets {
		out[i] = math.Exp(-(o[0]*o[0] + o[1]*o[1]) / (2.0 * sigma * sigma))
	}
	return out
}

// ParabolicPeakOffset 对峰值附近 3 个直方图值拟合抛物线求亚 bin 偏移(论文 §5 末段)。
func ParabolicPeakOffset(left, center, right float64) float64 {
	den := left - 2.0*center + right
	if den == 0.0 {
		return 0.0
	}
	return 0.5 * (left - right) / den
}

// Orientation 一个被分配的方向。
type Orientation struct {
	Bin    int
	Deg    float64
	Height float64
}

// AssignOrientations 返回所有入选方向;最高峰必在,其余需 >= peak * peakRatio 且为局部峰。
func AssignOrientations(hist []float64, peakRatio float64) []Orientation {
	n := len(hist)
	peak := 0.0
	for _, v := range hist {
		if v > peak {
			peak = v
		}
	}
	if peak <= 0.0 {
		return []Orientation{}
	}
	out := []Orientation{}
	for i := 0; i < n; i++ {
		if hist[i] < peak*peakRatio || hist[i] <= 0.0 {
			continue
		}
		if hist[i] < hist[(i-1+n)%n] || hist[i] < hist[(i+1)%n] {
			continue // 必须是局部峰
		}
		off := ParabolicPeakOffset(hist[(i-1+n)%n], hist[i], hist[(i+1)%n])
		deg := math.Mod((float64(i)+off)*(360.0/float64(n)), 360.0)
		out = append(out, Orientation{i, deg, hist[i]})
	}
	sort.Slice(out, func(a, b int) bool { return out[a].Height > out[b].Height })
	return out
}

// Trilinear 一个样本对某个 4x4x8 bin 的三线性权重。
type Trilinear struct {
	Di, Dj, Dk int
	W          float64
}

// TrilinearWeights 论文:"each entry into a bin is multiplied by a weight of 1-d for each
// dimension, where d is the distance of the sample from the central value of the bin"。
func TrilinearWeights(fx, fy, fa float64) []Trilinear {
	out := make([]Trilinear, 0, 8)
	for di := 0; di <= 1; di++ {
		wi := 1.0 - fx
		if di == 1 {
			wi = fx
		}
		for dj := 0; dj <= 1; dj++ {
			wj := 1.0 - fy
			if dj == 1 {
				wj = fy
			}
			for dk := 0; dk <= 1; dk++ {
				wk := 1.0 - fa
				if dk == 1 {
					wk = fa
				}
				out = append(out, Trilinear{di, dj, dk, wi * wj * wk})
			}
		}
	}
	return out
}

// NormalizeDescriptor 单位化 → 截断到 clamp → 再单位化(论文 §6 的光照不变两步)。
func NormalizeDescriptor(vec []float64, clamp float64) []float64 {
	n := 0.0
	for _, v := range vec {
		n += v * v
	}
	n = math.Sqrt(n)
	if n == 0.0 {
		return append([]float64(nil), vec...)
	}
	clipped := make([]float64, len(vec))
	for i, v := range vec {
		u := v / n
		if u > clamp {
			u = clamp
		}
		clipped[i] = u
	}
	n2 := 0.0
	for _, v := range clipped {
		n2 += v * v
	}
	n2 = math.Sqrt(n2)
	if n2 > 0.0 {
		for i := range clipped {
			clipped[i] /= n2
		}
	}
	return clipped
}

// Sample 旋转对齐后的一个梯度样本;U/V 为以窗口中心为原点的采样格坐标。
type Sample struct {
	U, V, Mag, Ang float64
}

// ComputeDescriptor 从 16x16 旋转对齐后的梯度样本算 128 维描述子。
func ComputeDescriptor(samples []Sample, keypointAngleDeg float64, width, bins, sampleGrid int,
	windowSigmaRatio float64) []float64 {
	sigma := windowSigmaRatio * float64(sampleGrid)
	vec := make([]float64, width*width*bins)
	half := float64(sampleGrid) / 2.0
	per := float64(sampleGrid) / float64(width)
	binw := 360.0 / float64(bins)
	for _, sp := range samples {
		w := math.Exp(-(sp.U*sp.U + sp.V*sp.V) / (2.0 * sigma * sigma))
		rel := math.Mod(sp.Ang-keypointAngleDeg, 360.0)
		if rel < 0 {
			rel += 360.0
		}
		// 采样格坐标 ∈ [-half, half] 映射到子块网格:第 0 个子块覆盖采样格 0..3,
		// 故 g = (u + half) / per ∈ (0, width),不做半格平移
		gi := (sp.U + half) / per
		gj := (sp.V + half) / per
		ga := rel / binw
		i0, j0, a0 := int(math.Floor(gi)), int(math.Floor(gj)), int(math.Floor(ga))
		for _, tw := range TrilinearWeights(gi-float64(i0), gj-float64(j0), ga-float64(a0)) {
			ii, jj := i0+tw.Di, j0+tw.Dj
			kk := (a0 + tw.Dk) % bins
			if ii >= 0 && ii < width && jj >= 0 && jj < width {
				vec[(jj*width+ii)*bins+kk] += sp.Mag * w * tw.W
			}
		}
	}
	return NormalizeDescriptor(vec, DescClamp)
}

// DescriptorDistance 欧氏距离(论文用最近邻/次近邻距离比做匹配筛选)。
func DescriptorDistance(a, b []float64) float64 {
	s := 0.0
	for i := range a {
		d := a[i] - b[i]
		s += d * d
	}
	return math.Sqrt(s)
}

// RatioTest 返回 (最近邻索引, 距离比);距离比 > ratio 视为无匹配(论文 §7.1)。
// 候选不足 2 个时返回 (0, 0) 或 (-1, +Inf)。
func RatioTest(query []float64, candidates [][]float64, ratio float64) (int, float64) {
	type pair struct {
		dist float64
		idx  int
	}
	ds := make([]pair, 0, len(candidates))
	for i, c := range candidates {
		ds = append(ds, pair{DescriptorDistance(query, c), i})
	}
	sort.Slice(ds, func(a, b int) bool { return ds[a].dist < ds[b].dist })
	if len(ds) < 2 {
		if len(ds) == 0 {
			return -1, math.Inf(1)
		}
		return ds[0].idx, 0.0
	}
	if ds[1].dist == 0.0 {
		return ds[0].idx, math.Inf(1)
	}
	return ds[0].idx, ds[0].dist / ds[1].dist
}
