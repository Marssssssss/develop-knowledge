// Package hrtf 实现 SOFA(SimpleFreeFieldHRIR) 约定下的双耳渲染核心换算：
// 球坐标/笛卡尔互换、最近邻查找、libmysofa 口径的反距离插值、延迟单位换算与 ITD/ILD。
package hrtf

import (
	"errors"
	"math"
)

// FEQEps 与 libmysofa tools.h 的 fequals 一致：fabs(a-b) < 0.00001
const FEQEps = 0.00001

// HeadRadius 是 SimpleFreeFieldHRIR 默认的接收器偏移（[0 0.09 0; 0 -0.09 0]）。
const HeadRadius = 0.09

func fequals(a, b float64) bool { return math.Abs(a-b) < FEQEps }

// Distance 是欧氏距离（tools.h 的 distance 宏）。
func Distance(a, b [3]float64) float64 {
	dx, dy, dz := a[0]-b[0], a[1]-b[1], a[2]-b[2]
	return math.Sqrt(dx*dx + dy*dy + dz*dz)
}

// S2C 球坐标 (phi, theta, r) -> 笛卡尔 (x, y, z)，与 mysofa_s2c 逐式对齐。
func S2C(v [3]float64) [3]float64 {
	phi := v[0] * (math.Pi / 180)
	theta := v[1] * (math.Pi / 180)
	x := math.Cos(theta) * v[2]
	return [3]float64{math.Cos(phi) * x, math.Sin(phi) * x, math.Sin(theta) * v[2]}
}

// C2S 笛卡尔 -> 球坐标，方位角规整到 [0,360)。
func C2S(v [3]float64) [3]float64 {
	x, y, z := v[0], v[1], v[2]
	r := math.Sqrt(x*x + y*y + z*z)
	theta := math.Atan2(z, math.Sqrt(x*x+y*y))
	phi := math.Atan2(y, x)
	return [3]float64{math.Mod(phi*(180/math.Pi)+360, 360), theta * (180 / math.Pi), r}
}

// HRTF 是 SimpleFreeFieldHRIR 的最小模型。
type HRTF struct {
	M             int
	R             int
	C             int
	N             int
	SamplingRate  float64
	SourceCart    [][3]float64
	IR            [][2][]float64 // [m][0]=左耳 taps, [m][1]=右耳 taps
	Delays        [][2]float64   // 秒；长度为 M 时按测量点，长度为 R 时共享
	SourceElems   int
	DefaultDelays [2]float64
}

// NewHRTF 以球坐标测量点构造，并就地转成笛卡尔（mysofa_tocartesian）。
func NewHRTF(spherical [][3]float64, ir [][2][]float64, fs float64,
	delays [][2]float64) *HRTF {
	cart := make([][3]float64, 0, len(spherical))
	for _, p := range spherical {
		cart = append(cart, S2C(p))
	}
	return &HRTF{
		M: len(spherical), R: 2, C: 3, N: len(ir[0][0]),
		SamplingRate: fs, SourceCart: cart, IR: ir, Delays: delays,
		SourceElems: len(spherical) * 3,
	}
}

// Validate 对应 easy.c 的 SourcePosition.elements == C*M 检查。
func (h *HRTF) Validate() bool { return h.SourceElems == h.C*h.M }

// DelayPair 对应 interpolate.c：元素数 > R 取每测量点，否则取 [0]/[1]。
func (h *HRTF) DelayPair(m int) (float64, float64) {
	if len(h.Delays) > h.R {
		return h.Delays[m][0], h.Delays[m][1]
	}
	return h.Delays[0][0], h.Delays[0][1]
}

// NearestIndex 返回最近测量点下标（libmysofa 用 k-d tree，语义等价）。
func (h *HRTF) NearestIndex(c [3]float64) int {
	best, bestD := -1, 0.0
	for i := 0; i < h.M; i++ {
		d := Distance(c, h.SourceCart[i])
		if best < 0 || d < bestD {
			best, bestD = i, d
		}
	}
	return best
}

// Interpolate 与 mysofa_interpolate 同口径：命中直取，否则按对择优 + 1/d 加权归一化。
func (h *HRTF) Interpolate(c [3]float64, nearest int, nb [6]int) ([]float64,
	[]float64, float64, float64, error) {
	if nearest < 0 || nearest >= h.M {
		return nil, nil, 0, 0, errors.New("nearest out of range")
	}
	d := Distance(c, h.SourceCart[nearest])
	dl, dr := h.DelayPair(nearest)
	if fequals(d, 0.0) {
		return append([]float64{}, h.IR[nearest][0]...),
			append([]float64{}, h.IR[nearest][1]...), dl, dr, nil
	}
	use := [6]bool{}
	d6 := [6]float64{1, 1, 1, 1, 1, 1}
	pairs := [][2]int{{0, 1}, {2, 3}, {4, 5}}
	for _, p := range pairs {
		a, b := p[0], p[1]
		ia, ib := nb[a], nb[b]
		switch {
		case ia >= 0 && ib >= 0:
			d6[a] = Distance(c, h.SourceCart[ia])
			d6[b] = Distance(c, h.SourceCart[ib])
			if !fequals(d6[a], d6[b]) {
				if d6[a] < d6[b] {
					use[a] = true
				} else {
					use[b] = true
				}
			}
		case ia >= 0:
			d6[a] = Distance(c, h.SourceCart[ia])
			use[a] = true
		case ib >= 0:
			d6[b] = Distance(c, h.SourceCart[ib])
			use[b] = true
		}
	}
	weight := 1.0 / d
	fir := make([]float64, 2*h.N)
	for k := 0; k < h.N; k++ {
		fir[k] = h.IR[nearest][0][k] * weight
		fir[h.N+k] = h.IR[nearest][1][k] * weight
	}
	dl *= weight
	dr *= weight
	for i := 0; i < 6; i++ {
		if !use[i] {
			continue
		}
		w := 1.0 / d6[i]
		for k := 0; k < h.N; k++ {
			fir[k] += h.IR[nb[i]][0][k] * w
			fir[h.N+k] += h.IR[nb[i]][1][k] * w
		}
		dlm, drm := h.DelayPair(nb[i])
		dl += dlm * w
		dr += drm * w
		weight += w
	}
	norm := 1.0 / weight
	for k := range fir {
		fir[k] *= norm
	}
	return fir[:h.N], fir[h.N:], dl * norm, dr * norm, nil
}

// GetFilter 对应 mysofa_getfilter_float(_nointerp)，延迟以**秒**返回。
func (h *HRTF) GetFilter(c [3]float64, nb [6]int, interp bool) ([]float64,
	[]float64, float64, float64, error) {
	if !h.Validate() {
		return nil, nil, 0, 0, errors.New("MYSOFA_INVALID_FORMAT")
	}
	coord := c
	nearest := h.NearestIndex(coord)
	if !interp { // nointerp：用最近点坐标覆盖请求坐标
		coord = h.SourceCart[nearest]
	}
	return h.Interpolate(coord, nearest, nb)
}

// DelaySamples 对应 mysofa_getfilter_short：秒 * DataSamplingRate 后向零截断。
func DelaySamples(seconds, samplingRate float64) int {
	if seconds < 0 {
		return -int(-seconds * samplingRate)
	}
	return int(seconds * samplingRate)
}

// ITDSeconds 返回耳间时间差（秒），延迟以样本计：正 = 右耳更晚 = 源在左。
func ITDSeconds(leftSamples, rightSamples int, fs float64) float64 {
	return float64(rightSamples-leftSamples) / fs
}

// ILDdB 返回耳间强度差 20*log10(rms_right/rms_left)。
func ILDdB(left, right []float64) float64 {
	rl, rr := rms(left), rms(right)
	if rl == 0 {
		if rr > 0 {
			return math.Inf(1)
		}
		return 0
	}
	return 20 * math.Log10(rr/rl)
}

func rms(x []float64) float64 {
	if len(x) == 0 {
		return 0
	}
	acc := 0.0
	for _, v := range x {
		acc += v * v
	}
	return math.Sqrt(acc / float64(len(x)))
}
