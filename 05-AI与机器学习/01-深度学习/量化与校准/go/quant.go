// Package quant —— torch/ao/quantization 的 observer.py / fake_quantize.py / utils.py 的 Go 转写。
package main

import (
	"errors"
	"math"
)

// EPS = torch.finfo(torch.float32).eps
const EPS = 2.220446049250313e-16

// CheckMinMaxValid 对应 utils.check_min_max_valid。
func CheckMinMaxValid(minVal, maxVal float64, empty bool) (bool, error) {
	if empty {
		return false, nil
	}
	if math.IsInf(minVal, 1) && math.IsInf(maxVal, -1) {
		return false, nil
	}
	if minVal > maxVal {
		return false, errors.New("min should be less than max")
	}
	return true, nil
}

// CalculateQminQmax 对应 utils.calculate_qmin_qmax 的默认档位表。
func CalculateQminQmax(hasCustom bool, qmin, qmax int, dtype string, reduceRange bool) (int, int) {
	if hasCustom {
		if reduceRange {
			return qmin / 2, qmax / 2
		}
		return qmin, qmax
	}
	switch dtype {
	case "qint8", "int8":
		if reduceRange {
			return -64, 63
		}
		return -128, 127
	case "quint8", "uint8":
		if reduceRange {
			return 0, 127
		}
		return 0, 255
	case "qint32", "int32":
		return -(1 << 31), 1<<31 - 1
	case "uint16":
		return 0, 1<<16 - 1
	case "int16":
		return -(1 << 15), 1<<15 - 1
	}
	return 0, 15
}

// CalculateQparams 对应 _calculate_qparams 的三分支。
func CalculateQparams(minVal, maxVal, qscheme string, qmin, qmax int, dtype string, eps float64) (float64, float64) {
	minNeg := math.Min(minVal, 0)
	maxPos := math.Max(maxVal, 0)
	switch qscheme {
	case "per_tensor_symmetric", "per_channel_symmetric":
		maxPos = math.Max(-minNeg, maxPos)
		scale := maxPos / (float64(qmax-qmin) / 2)
		scale = math.Max(scale, eps)
		zp := 0.0
		if dtype == "quint8" || dtype == "uint8" {
			zp = 128
		} else if dtype == "uint16" {
			zp = 1 << 15
		}
		return scale, zp
	case "per_channel_affine_float_qparams":
		scale := (maxVal - minVal) / float64(qmax-qmin)
		if !(scale > eps) {
			scale = 1.0
		}
		return scale, -1 * minVal / scale
	}
	scale := (maxPos - minNeg) / float64(qmax-qmin)
	scale = math.Max(scale, eps)
	zp := float64(qmin) - float64(RoundHalfEven(minNeg/scale))
	if zp < float64(qmin) {
		zp = float64(qmin)
	}
	if zp > float64(qmax) {
		zp = float64(qmax)
	}
	return scale, zp
}

// QuantizePerTensor x_q = clamp(round(x/scale)+zp, qmin, qmax)。
// Go 的 math.Round 是“四舍五入、半数远离零”，与 torch.round（半数取偶）不同，
// 这里显式实现半数取偶以对齐 PyTorch。
func QuantizePerTensor(x []float64, scale float64, zp, qmin, qmax int) []int {
	out := make([]int, len(x))
	for i, v := range x {
		out[i] = Clamp(RoundHalfEven(v/scale)+zp, qmin, qmax)
	}
	return out
}

// RoundHalfEven 半数取偶（与 torch.round 一致）。
func RoundHalfEven(v float64) int {
	f := math.Floor(v)
	diff := v - f
	if diff > 0.5 {
		return int(f) + 1
	}
	if diff < 0.5 {
		return int(f)
	}
	if int(f)%2 == 0 {
		return int(f)
	}
	return int(f) + 1
}

// Clamp 区间裁剪。
func Clamp(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// Dequantize 反量化 (x_q − zp)·scale。
func Dequantize(xq []int, scale float64, zp int) []float64 {
	out := make([]float64, len(xq))
	for i, q := range xq {
		out[i] = float64(q-zp) * scale
	}
	return out
}

// FakeQuantize 量化后立即反量化。
func FakeQuantize(x []float64, scale float64, zp, qmin, qmax int) []float64 {
	return Dequantize(QuantizePerTensor(x, scale, zp, qmin, qmax), scale, zp)
}

// MinMaxObserver running min/max。
type MinMaxObserver struct {
	MinVal, MaxVal float64
	Started        bool
}

// NewMinMaxObserver 初值 ±inf。
func NewMinMaxObserver() *MinMaxObserver {
	return &MinMaxObserver{MinVal: math.Inf(1), MaxVal: math.Inf(-1)}
}

// Forward 累积 min/max。
func (o *MinMaxObserver) Forward(x []float64) {
	if len(x) == 0 {
		return
	}
	o.MinVal = math.Min(o.MinVal, MinOf(x))
	o.MaxVal = math.Max(o.MaxVal, MaxOf(x))
	o.Started = true
}

// MinOf / MaxOf 求极值。
func MinOf(x []float64) float64 {
	m := x[0]
	for _, v := range x[1:] {
		if v < m {
			m = v
		}
	}
	return m
}

// MaxOf 求最大值。
func MaxOf(x []float64) float64 {
	m := x[0]
	for _, v := range x[1:] {
		if v > m {
			m = v
		}
	}
	return m
}

// MovingAvgObserver 对应 MovingAverageMinMaxObserver：首帧直接取，之后 EMA。
type MovingAvgObserver struct {
	*MinMaxObserver
	C float64
}

// Forward EMA 更新。
func (o *MovingAvgObserver) Forward(x []float64) {
	if len(x) == 0 {
		return
	}
	curMin, curMax := MinOf(x), MaxOf(x)
	if math.IsInf(o.MinVal, 1) && math.IsInf(o.MaxVal, -1) {
		o.MinVal, o.MaxVal = curMin, curMax
		return
	}
	o.MinVal = o.MinVal + o.C*(curMin-o.MinVal)
	o.MaxVal = o.MaxVal + o.C*(curMax-o.MaxVal)
}
