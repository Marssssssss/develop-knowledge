// log-Mel 频谱图 —— Go 实现（与 mel.py 同一套官方口径）
//
// A. librosa 官方实现（filters.py 的 mel()、core/convert.py 的 hz_to_mel/mel_to_hz），
//    Slaney 口径：f_sp=200/3、min_log_hz=1000、logstep=ln(6.4)/27，
//    norm="slaney" 时每个滤波器乘 2/(mel_f[i+2]−mel_f[i])
// B. OpenAI Whisper 官方 whisper/audio.py：sr=16000、n_fft=400、hop=160、
//    30s=480000 样本、center=True 得 3001 帧丢最后一帧得 3000 帧；
//    magnitudes=|STFT|^2；log10 后动态范围压到 8，(x+4)/4 归一
package main

import (
	"fmt"
	"math"
	"math/cmplx"
)

const (
	fSp       = 200.0 / 3
	minLogHz  = 1000.0
	minLogMel = (minLogHz - 0.0) / fSp // 15.0
	logStep   = math.Log(6.4) / 27.0
	// Whisper 硬编码常量
	sampleRate = 16000
	nFFT       = 400
	hopLength  = 160
	chunkLen   = 30
	nSamples   = chunkLen * sampleRate      // 480000
	nFrames    = nSamples / hopLength       // 3000
	sampPerTok = hopLength * 2              // 320
	framesPerS = sampleRate / hopLength     // 100
	toksPerS   = sampleRate / sampPerTok    // 50
)

func hzToMel(f float64, htk bool) float64 {
	if htk {
		return 2595.0 * math.Log10(1.0+f/700.0)
	}
	if f < minLogHz {
		return f / fSp
	}
	return minLogMel + math.Log(f/minLogHz)/logStep
}

func melToHz(m float64, htk bool) float64 {
	if htk {
		return 700.0 * (math.Pow(10, m/2595.0) - 1.0)
	}
	if m < minLogMel {
		return fSp * m
	}
	return minLogHz * math.Exp(logStep*(m-minLogMel))
}

func melFrequencies(n int, fmin, fmax float64) []float64 {
	lo, hi := hzToMel(fmin, false), hzToMel(fmax, false)
	out := make([]float64, n)
	for i := 0; i < n; i++ {
		out[i] = melToHz(lo+(hi-lo)*float64(i)/float64(n-1), false)
	}
	return out
}

func fftFrequencies(sr float64, nFft int) []float64 {
	out := make([]float64, 1+nFft/2)
	for k := range out {
		out[k] = sr * float64(k) / float64(nFft)
	}
	return out
}

// MelFilterbank 对应 librosa.filters.mel(norm="slaney")
func MelFilterbank(sr float64, nFft, nMels int) ([][]float64, []float64) {
	if nFft <= 0 {
		return nil, nil
	}
	fftf := fftFrequencies(sr, nFft)
	melF := melFrequencies(nMels+2, 0.0, sr/2.0)
	fdiff := make([]float64, len(melF)-1)
	for i := range fdiff {
		fdiff[i] = melF[i+1] - melF[i]
	}
	W := make([][]float64, nMels)
	for i := 0; i < nMels; i++ {
		row := make([]float64, len(fftf))
		for b, f := range fftf {
			lower := -(melF[i] - f) / fdiff[i]
			upper := (melF[i+2] - f) / fdiff[i+1]
			row[b] = math.Max(0, math.Min(lower, upper))
		}
		enorm := 2.0 / (melF[i+2] - melF[i])
		for b := range row {
			row[b] *= enorm
		}
		W[i] = row
	}
	return W, melF
}

func hannWindow(n int) []float64 {
	w := make([]float64, n)
	for i := range w {
		w[i] = 0.5 * (1.0 - math.Cos(2.0*math.Pi*float64(i)/float64(n)))
	}
	return w
}

// StftPower 与 torch.stft(center=true, onesided) 同口径，返回功率谱 |X|^2
func StftPower(sig []float64, nFft, hop int) [][]float64 {
	pad := nFft / 2
	nFrames := 1 + (len(sig)+2*pad-nFft)/hop
	win := hannWindow(nFft)
	out := make([][]float64, nFrames)
	for t := 0; t < nFrames; t++ {
		start := t*hop - pad
		row := make([]float64, nFft/2+1)
		for k := 0; k <= nFft/2; k++ {
			acc := complex(0.0, 0.0)
			for i := 0; i < nFft; i++ {
				j := start + i
				x := 0.0
				if j >= 0 && j < len(sig) {
					x = sig[j]
				}
				ang := -2.0 * math.Pi * float64(k) * float64(i) / float64(nFft)
				acc += complex(x*win[i], 0) * cmplx.Exp(complex(0, ang))
			}
			row[k] = cmplx.Abs(acc) * cmplx.Abs(acc)
		}
		out[t] = row
	}
	return out
}

// LogMelSpectrogram 照抄 whisper/audio.py 的三步后处理
func LogMelSpectrogram(power [][]float64, filters [][]float64) [][]float64 {
	mel := make([][]float64, len(power))
	for t, row := range power {
		mel[t] = make([]float64, len(filters))
		for m := range filters {
			s := 0.0
			for b := range row {
				s += filters[m][b] * row[b]
			}
			mel[t][m] = s
		}
	}
	gmax := math.Inf(-1)
	for t := range mel {
		for m := range mel[t] {
			v := math.Log10(math.Max(mel[t][m], 1e-10))
			mel[t][m] = v
			if v > gmax {
				gmax = v
			}
		}
	}
	out := make([][]float64, len(mel))
	for t := range mel {
		out[t] = make([]float64, len(mel[t]))
		for m, v := range mel[t] {
			out[t][m] = (math.Max(v, gmax-8.0) + 4.0) / 4.0
		}
	}
	return out
}

var nOK, nBad int

func check(label string, cond bool, detail string) {
	nOK++
	if !cond {
		nBad++
		fmt.Printf("  FAIL %s %s\n", label, detail)
	}
}

func closeF(a, b float64, eps float64) bool { return math.Abs(a-b) <= eps }

func main() {
	// Slaney 标度：librosa docstring 给的例子
	check("hz_to_mel(60)=0.9", closeF(hzToMel(60, false), 0.9, 1e-9),
		fmt.Sprintf("%g", hzToMel(60, false)))
	check("hz_to_mel(440)=6.6", closeF(hzToMel(440, false), 6.6, 1e-9),
		fmt.Sprintf("%g", hzToMel(440, false)))
	check("hz_to_mel(1000)=15", closeF(hzToMel(1000, false), 15.0, 1e-9), "")
	bad := 0
	for _, f := range []float64{50, 200, 999, 1000, 1001, 3000, 8000} {
		if !closeF(melToHz(hzToMel(f, false), false), f, 1e-6) {
			bad++
		}
	}
	check("mel 往返一致", bad == 0, fmt.Sprint(bad))

	// 滤波器组
	W, melF := MelFilterbank(float64(sampleRate), nFFT, 80)
	check("形状 80x201", len(W) == 80 && len(W[0]) == 201, fmt.Sprint(len(W[0])))
	fftf := fftFrequencies(float64(sampleRate), nFFT)
	check("频率分辨率 40Hz", closeF(fftf[1]-fftf[0], 40.0, 1e-9), "")
	viol := 0
	for i := range W {
		for b, v := range W[i] {
			if v > 0 && (fftf[b] < melF[i]-1e-9 || fftf[b] > melF[i+2]+1e-9) {
				viol++
			}
		}
	}
	check("三角支撑不越界", viol == 0, fmt.Sprint(viol))
	check("高频 mel 带更宽", melF[79]-melF[78] > (melF[3]-melF[2])*2, "")

	// Whisper 常量与帧数
	check("N_SAMPLES 480000", nSamples == 480000, "")
	check("N_FRAMES 3000", nFrames == 3000, "")
	check("TOKENS_PER_SECOND 50", toksPerS == 50, "")
	check("FRAMES_PER_SECOND 100", framesPerS == 100, "")
	check("center=true 帧数 3001",
		1+(nSamples+2*(nFFT/2)-nFFT)/hopLength == 3001, "")

	// 1kHz 正弦
	sr := 16000.0
	sig := make([]float64, 1600)
	for t := range sig {
		sig[t] = math.Sin(2 * math.Pi * 1000 * float64(t) / sr)
	}
	P := StftPower(sig, nFFT, hopLength)
	peak := 0
	pv := -1.0
	for b, v := range P[len(P)/2] {
		if v > pv {
			pv = v
			peak = b
		}
	}
	check("1kHz 峰值 bin=25", peak == 25, fmt.Sprint(peak))
	lm := LogMelSpectrogram(P, W)
	rng := 0.0
	mn := math.Inf(1)
	for _, r := range lm {
		for _, v := range r {
			if v > rng {
				rng = v
			}
			if v < mn {
				mn = v
			}
		}
	}
	check("动态范围 ≤ 2(即 8 个 log10 单位 /4)", rng-mn <= 2.0+1e-9,
		fmt.Sprintf("%g", rng-mn))
	fmt.Printf("断言 %d 条，失败 %d\n", nOK, nBad)
}
