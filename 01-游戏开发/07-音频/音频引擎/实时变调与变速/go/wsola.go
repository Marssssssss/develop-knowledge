package pitchshift

import "math"

// ResampleLinear 按 ratio 改变播放速率：时长 /ratio，音高 ×ratio。
func ResampleLinear(x []float64, ratio float64) []float64 {
	n := len(x)
	m := int(math.Round(float64(n) / ratio))
	if m < 1 {
		m = 1
	}
	out := make([]float64, 0, m)
	for i := 0; i < m; i++ {
		pos := float64(i) * ratio
		i0 := int(math.Floor(pos))
		frac := pos - math.Floor(pos)
		a, b := 0.0, 0.0
		if i0 < n {
			a = x[i0]
		}
		if i0+1 < n {
			b = x[i0+1]
		}
		out = append(out, a*(1-frac)+b*frac)
	}
	return out
}

// WSOLAStretch 时长 ×ratio、音高不变的波形相似重叠相加。
// 与朴素 OLA 的差别是每帧先按相似度对齐再拼接，否则帧边界的相位跳变
// 会形成可听的周期性调制（本 demo 首版即因此把 440 Hz 估成 101 Hz）。
func WSOLAStretch(x []float64, ratio float64) []float64 {
	const frame, hop, search = 2048, 1024, 512
	if len(x) < frame {
		out := make([]float64, len(x))
		copy(out, x)
		return out
	}
	outLen := int(math.Round(float64(len(x)) * ratio))
	out := make([]float64, outLen)
	norm := make([]float64, outLen)
	synHop := int(math.Round(hop * ratio))
	if synHop < 1 {
		synHop = 1
	}
	win := make([]float64, frame)
	for i := range win {
		win[i] = 0.5 - 0.5*math.Cos(2*math.Pi*float64(i)/frame)
	}
	var prev []float64
	read, pos := 0, 0
	for read+frame <= len(x) && pos+frame <= outLen {
		offset := read
		if prev != nil {
			lo, hi := read-search, read+search
			if lo < 0 {
				lo = 0
			}
			if hi > len(x)-frame {
				hi = len(x) - frame
			}
			best, bestOff := 0.0, read
			first := true
			for off := lo; off <= hi; off += 8 {
				acc := 0.0
				for k := 0; k < frame; k += 4 {
					acc += x[off+k] * prev[k]
				}
				if first || acc > best {
					best, bestOff, first = acc, off, false
				}
			}
			offset = bestOff
		}
		block := x[offset : offset+frame]
		for i, v := range block {
			out[pos+i] += v * win[i]
			norm[pos+i] += win[i]
		}
		prev = block
		pos += synHop
		read += hop
	}
	for i := range out {
		if norm[i] > 1e-9 {
			out[i] /= norm[i]
		}
	}
	return out
}

// TimeStretch 变速不变调。
func TimeStretch(x []float64, timeRatio float64) []float64 {
	return WSOLAStretch(x, timeRatio)
}

// PitchShift 变调不变速：先按 1/p 重采样，再按 p 拉伸还原时长。
func PitchShift(x []float64, semitones float64) []float64 {
	p := SemitonesToPitchScale(semitones)
	return WSOLAStretch(ResampleLinear(x, p), p)
}

// EstimateFrequency 用自相关估计基频（对拼接瑕疵比过零率稳健）。
func EstimateFrequency(x []float64, sampleRate float64) float64 {
	segLen := len(x) / 2
	if segLen > 4096 {
		segLen = 4096
	}
	if segLen < 64 {
		segLen = len(x)
	}
	if segLen < 64 {
		return 0
	}
	start := (len(x) - segLen) / 2
	seg := make([]float64, segLen)
	copy(seg, x[start:start+segLen])
	mean := 0.0
	for _, v := range seg {
		mean += v
	}
	mean /= float64(segLen)
	for i := range seg {
		seg[i] -= mean
	}
	minLag := int(sampleRate / 2000.0)
	maxLag := int(sampleRate / 40.0)
	if maxLag > segLen/2 {
		maxLag = segLen / 2
	}
	best, bestLag := 0.0, minLag
	for lag := minLag; lag <= maxLag; lag++ {
		acc := 0.0
		for i := 0; i+lag < segLen; i++ {
			acc += seg[i] * seg[i+lag]
		}
		if acc > best {
			best, bestLag = acc, lag
		}
	}
	return sampleRate / float64(bestLag)
}
