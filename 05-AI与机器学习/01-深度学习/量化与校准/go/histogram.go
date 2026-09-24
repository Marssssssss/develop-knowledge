package main

import "math"

// GetNorm 对应 _get_norm：density·(end³−begin³)/3。
func GetNorm(begin, end, density float64) float64 {
	return density * (end*end*end - begin*begin*begin) / 3
}

// CumSum 前缀和。
func CumSum(xs []float64) []float64 {
	out := make([]float64, len(xs))
	acc := 0.0
	for i, v := range xs {
		acc += v
		out[i] = acc
	}
	return out
}

// HistogramObserver 的非线性参数搜索（bins/dstNBins 可调）。
type HistogramObserver struct {
	Bins                    int
	DstNBins                int
	MinVal, MaxVal          float64
	Histogram               []float64
}

// Fill 按 [min,max] 均匀分桶。
func (h *HistogramObserver) Fill(values []float64) {
	h.Histogram = make([]float64, h.Bins)
	span := h.MaxVal - h.MinVal
	for _, v := range values {
		idx := int((v - h.MinVal) / span * float64(h.Bins))
		if idx < 0 {
			idx = 0
		}
		if idx >= h.Bins {
			idx = h.Bins - 1
		}
		h.Histogram[idx]++
	}
}

// ComputeQuantizationError 对应 _compute_quantization_error：被排除的桶会被 clamp 到边界档。
func (h *HistogramObserver) ComputeQuantizationError(startBin, endBin int) float64 {
	binWidth := (h.MaxVal - h.MinVal) / float64(h.Bins)
	dstBinWidth := binWidth * float64(endBin-startBin+1) / float64(h.DstNBins)
	if dstBinWidth == 0.0 {
		return 0.0
	}
	norm := 0.0
	for srcBin := 0; srcBin < h.Bins; srcBin++ {
		begin := float64(srcBin-startBin) * binWidth
		end := begin + binWidth
		b0 := ClampF(math.Floor(begin/dstBinWidth), 0, float64(h.DstNBins-1))
		b1 := ClampF(math.Floor(end/dstBinWidth), 0, float64(h.DstNBins-1))
		c0 := (b0 + 0.5) * dstBinWidth
		density := h.Histogram[srcBin] / binWidth
		norm += GetNorm(begin-c0, dstBinWidth/2, density)
		norm += (b1 - b0 - 1) * GetNorm(-dstBinWidth/2, dstBinWidth/2, density)
		c1 := b1*dstBinWidth + dstBinWidth/2
		norm += GetNorm(-dstBinWidth/2, end-c1, density)
	}
	return norm
}

// ClampF 浮点区间裁剪。
func ClampF(v, lo, hi float64) float64 {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

// NonLinearParamSearch 对应 _non_linear_param_search，返回 (newMin, newMax, startBin, endBin)。
func (h *HistogramObserver) NonLinearParamSearch() (float64, float64, int, int) {
	binWidth := (h.MaxVal - h.MinVal) / float64(h.Bins)
	total := 0.0
	for _, v := range h.Histogram {
		total += v
	}
	csum := CumSum(h.Histogram)
	stepsize, alpha, beta := 1e-5, 0.0, 1.0
	startBin, endBin := 0, h.Bins-1
	normMin := math.Inf(1)
	for alpha < beta {
		nextAlpha, nextBeta := alpha+stepsize, beta-stepsize
		l, r := startBin, endBin
		for l < endBin && csum[l] < nextAlpha*total {
			l++
		}
		for r > startBin && csum[r] > nextBeta*total {
			r--
		}
		nextStart, nextEnd := startBin, endBin
		if (l - startBin) > (endBin - r) {
			nextStart = l
			alpha = nextAlpha
		} else {
			nextEnd = r
			beta = nextBeta
		}
		if nextStart == startBin && nextEnd == endBin {
			continue
		}
		norm := h.ComputeQuantizationError(nextStart, nextEnd)
		if norm > normMin {
			break
		}
		normMin = norm
		startBin, endBin = nextStart, nextEnd
	}
	return h.MinVal + binWidth*float64(startBin), h.MinVal + binWidth*float64(endBin+1), startBin, endBin
}
