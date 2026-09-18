// 自检用的合成数据与统计小工具(仅被 self-check 使用,不参与 SIFT 主线逻辑)。
package main

import "math"

// volume 合成 DoG 体积:三维高斯隆起,center 顺序为 (s, y, x)。
func volume(sn, h int, amp float64, center, scale [3]float64) [][][]float64 {
	out := make([][][]float64, sn)
	for s := 0; s < sn; s++ {
		plane := make([][]float64, h)
		for y := 0; y < h; y++ {
			row := make([]float64, h)
			for x := 0; x < h; x++ {
				e := math.Pow((float64(x)-center[2])/scale[2], 2) +
					math.Pow((float64(y)-center[1])/scale[1], 2) +
					math.Pow((float64(s)-center[0])/scale[0], 2)
				row[x] = amp * math.Exp(-e/2.0)
			}
			plane[y] = row
		}
		out[s] = plane
	}
	return out
}

// negate 逐元素取负:用于验证极小值(负峰)同样被检出。
func negate(vol [][][]float64) [][][]float64 {
	out := make([][][]float64, len(vol))
	for s, plane := range vol {
		p := make([][]float64, len(plane))
		for y, row := range plane {
			r := make([]float64, len(row))
			for x, v := range row {
				r[x] = -v
			}
			p[y] = r
		}
		out[s] = p
	}
	return out
}

// flatVolume 全零体积,用于验证平坦区域没有极值。
func flatVolume(sn, h int) [][][]float64 {
	out := make([][][]float64, sn)
	for s := range out {
		plane := make([][]float64, h)
		for y := range plane {
			plane[y] = make([]float64, h)
		}
		out[s] = plane
	}
	return out
}

// samples 构造 grid x grid 的均匀梯度场,采样格坐标以窗口中心为原点。
func samples(angle, mag float64, grid int) []Sample {
	out := make([]Sample, 0, grid*grid)
	for j := 0; j < grid; j++ {
		for i := 0; i < grid; i++ {
			out = append(out, Sample{float64(i) - float64(grid)/2.0 + 0.5,
				float64(j) - float64(grid)/2.0 + 0.5, mag, angle})
		}
	}
	return out
}

func fill(n int, v float64) []float64 {
	out := make([]float64, n)
	for i := range out {
		out[i] = v
	}
	return out
}

// gauss 标准正态密度,用来解析地比较 DoG 与尺度归一化 LoG。
func gauss(t, s float64) float64 {
	return math.Exp(-(t * t) / (2.0 * s * s)) / (math.Sqrt(2*math.Pi) * s)
}

func argmax(v []float64) int {
	bi := 0
	for i, x := range v {
		if x > v[bi] {
			bi = i
		}
	}
	return bi
}

func nonZero(v []float64) int {
	c := 0
	for _, x := range v {
		if x > 0 {
			c++
		}
	}
	return c
}

func maxDiff(a, b []float64) float64 {
	m := 0.0
	for i := range a {
		if d := math.Abs(a[i] - b[i]); d > m {
			m = d
		}
	}
	return m
}

func lens(x [][][][]float64) []int {
	out := []int{}
	for _, o := range x {
		out = append(out, len(o))
	}
	return out
}

func sizes(x [][][][]float64) []int {
	out := []int{}
	for _, o := range x {
		out = append(out, len(o[0]))
	}
	return out
}

// desc 以固定参数算描述子(keypoint_angle = 0),供多组用例复用。
func desc(sm []Sample) []float64 {
	return ComputeDescriptor(sm, 0.0, DescWidth, DescBins, DescSamples, 0.5)
}
