// Package rope —— RoPE 旋转位置编码的 Go 转写，对应 python/rope.py。
// 语言差异显式落地：Go 无 ** 运算符（用 math.Pow），切片语义 None 用零值代替。
package main

import "math"

// DefaultInvFreq 对应 LlamaRotaryEmbedding.compute_default_rope_parameters。
func DefaultInvFreq(base float64, dim int) []float64 {
	out := make([]float64, 0, dim/2)
	for i := 0; i < dim; i += 2 {
		out = append(out, 1.0/math.Pow(base, float64(i)/float64(dim)))
	}
	return out
}

// LinearScalingInvFreq 对应 _compute_linear_scaling_rope_parameters。
func LinearScalingInvFreq(base float64, dim int, factor float64) []float64 {
	inv := DefaultInvFreq(base, dim)
	for i := range inv {
		inv[i] /= factor
	}
	return inv
}

// DynamicNtkBase 对应 _compute_dynamic_ntk_parameters 里的新 base。
func DynamicNtkBase(base float64, dim int, factor float64, seqLen, maxPos int) float64 {
	s := float64(maxPos)
	if float64(seqLen) > s {
		s = float64(seqLen)
	}
	pow := float64(dim) / float64(dim-2)
	return base * math.Pow((factor*s/float64(maxPos))-(factor-1), pow)
}

// GetMscale 对应 YaRN 里的 get_mscale。
func GetMscale(scale, mscale float64) float64 {
	if scale <= 1 {
		return 1.0
	}
	return 0.1*mscale*math.Log(scale) + 1.0
}

// FindCorrectionDim 对应 find_correction_dim。
func FindCorrectionDim(numRotations, dim int, base float64, maxPos int) float64 {
	return (float64(dim) * math.Log(float64(maxPos)/(float64(numRotations)*2*math.Pi))) / (2 * math.Log(base))
}

// FindCorrectionRange 对应 find_correction_range，返回 (low, high)。
func FindCorrectionRange(lowRot, highRot, dim int, base float64, maxPos int, truncate bool) (float64, float64) {
	low := FindCorrectionDim(lowRot, dim, base, maxPos)
	high := FindCorrectionDim(highRot, dim, base, maxPos)
	if truncate {
		low = math.Floor(low)
		high = math.Ceil(high)
	}
	if low < 0 {
		low = 0
	}
	if high > float64(dim-1) {
		high = float64(dim - 1)
	}
	return low, high
}

// LinearRampFactor 对应 linear_ramp_factor（含 min==max 的 +0.001 防奇点）。
func LinearRampFactor(lo, hi float64, n int) []float64 {
	if lo == hi {
		hi = hi + 0.001
	}
	out := make([]float64, n)
	for i := 0; i < n; i++ {
		v := (float64(i) - lo) / (hi - lo)
		if v < 0 {
			v = 0
		}
		if v > 1 {
			v = 1
		}
		out[i] = v
	}
	return out
}

// YarnInvFreq 对应 _compute_yarn_parameters，返回 (inv_freq, attention_factor)。
func YarnInvFreq(base float64, dim int, factor float64, origMaxPos int, mscale, mscaleAllDim float64, truncate bool) ([]float64, float64) {
	att := GetMscale(factor, 1.0)
	if mscale != 0 && mscaleAllDim != 0 {
		att = GetMscale(factor, mscale) / GetMscale(factor, mscaleAllDim)
	}
	half := dim / 2
	extrap := make([]float64, half)
	interp := make([]float64, half)
	for i := 0; i < half; i++ {
		p := math.Pow(base, float64(2*i)/float64(dim))
		extrap[i] = 1.0 / p
		interp[i] = 1.0 / (factor * p)
	}
	low, high := FindCorrectionRange(32, 1, dim, base, origMaxPos, truncate)
	ramp := LinearRampFactor(low, high, half)
	out := make([]float64, half)
	for i := 0; i < half; i++ {
		f := 1.0 - ramp[i]
		out[i] = interp[i]*(1-f) + extrap[i]*f
	}
	return out, att
}

// Llama3InvFreq 对应 _compute_llama3_parameters。
func Llama3InvFreq(base float64, dim int, factor, lowFreqFactor, highFreqFactor, oldCtx float64) []float64 {
	inv := DefaultInvFreq(base, dim)
	lowWl := oldCtx / lowFreqFactor
	highWl := oldCtx / highFreqFactor
	out := make([]float64, len(inv))
	for i, v := range inv {
		wl := 2 * math.Pi / v
		x := v
		if wl > lowWl {
			x = v / factor
		}
		s := (oldCtx/wl - lowFreqFactor) / (highFreqFactor - lowFreqFactor)
		smoothed := (1-s)*x/factor + s*x
		isMedium := !(wl < highWl) && !(wl > lowWl)
		if isMedium {
			out[i] = smoothed
		} else {
			out[i] = x
		}
	}
	return out
}

// ProportionalInvFreq 对应 _compute_proportional_rope_parameters（分母用 headDim）。
func ProportionalInvFreq(base float64, headDim int, factor, proportion float64) []float64 {
	angles := int(proportion*float64(headDim)) / 2
	out := make([]float64, 0, headDim/2)
	for i := 0; i < angles; i++ {
		out = append(out, 1.0/math.Pow(base, float64(2*i)/float64(headDim)))
	}
	for len(out) < headDim/2 {
		out = append(out, 0.0)
	}
	for i := range out {
		out[i] /= factor
	}
	return out
}

// RotaryEmb 对应 RotaryEmbedding.forward：freqs = inv_freq*pos，emb = cat(freqs, freqs)。
func RotaryEmb(inv []float64, positions []int, scaling float64) ([]float64, []float64) {
	cos := make([]float64, 0, len(positions)*2*len(inv))
	sin := make([]float64, 0, len(positions)*2*len(inv))
	for _, p := range positions {
		for _, f := range inv {
			e := f * float64(p)
			cos = append(cos, math.Cos(e)*scaling)
			sin = append(sin, math.Sin(e)*scaling)
		}
		for _, f := range inv {
			e := f * float64(p)
			cos = append(cos, math.Cos(e)*scaling)
			sin = append(sin, math.Sin(e)*scaling)
		}
	}
	return cos, sin
}

// RotateHalf 对应 rotate_half：cat(-x2, x1)。
func RotateHalf(x []float64) []float64 {
	h := len(x) / 2
	out := make([]float64, 0, len(x))
	for _, v := range x[h:] {
		out = append(out, -v)
	}
	return append(out, x[:h]...)
}

// ApplyRotary 对单个 head 向量施加旋转（cos/sin 长度为 dim）。
func ApplyRotary(x, cos, sin []float64) []float64 {
	r := RotateHalf(x)
	out := make([]float64, len(x))
	for i := range x {
		out[i] = x[i]*cos[i] + r[i]*sin[i]
	}
	return out
}

// Dot 计算内积。
func Dot(a, b []float64) float64 {
	s := 0.0
	for i := range a {
		s += a[i] * b[i]
	}
	return s
}

// RotatedScore 把 q 置于 m、k 置于 n 后的点积。
func RotatedScore(q, k, inv []float64, m, n int) float64 {
	dim := 2 * len(inv)
	cq, sq := RotaryEmb(inv, []int{m}, 1.0)
	ck, sk := RotaryEmb(inv, []int{n}, 1.0)
	rq := ApplyRotary(q, cq[:dim], sq[:dim])
	rk := ApplyRotary(k, ck[:dim], sk[:dim])
	return Dot(rq, rk)
}
