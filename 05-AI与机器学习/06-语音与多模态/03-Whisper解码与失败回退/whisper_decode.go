// Whisper 解码策略与温度回退 —— Go 实现（与 whisper_decode.py 同一套官方口径）
//
// 来源（均按 jsDelivr 取 main 分支原文后逐行照抄）：
//   whisper/decoding.py   : GreedyDecoder / BeamSearchDecoder(patience,max_candidates)
//                           MaximumLikelihoodRanker / SuppressBlank / SuppressTokens
//                           ApplyTimestampRules / avg_logprob = lp/(len+1)
//   whisper/transcribe.py : 默认温度 (0.0,0.2,0.4,0.6,0.8,1.0)、
//                           compression_ratio_threshold=2.4、logprob_threshold=-1.0、
//                           no_speech_threshold=0.6、decode_with_fallback 的静音豁免
//   whisper/utils.py      : compression_ratio = len(bytes)/len(zlib.compress(bytes))
package main
import (
	"bytes"
	"compress/zlib"
	"fmt"
	"math"
)
const (
	eot            = 3
	noTimestamps   = 4
	noSpeech       = 5
	sot            = 6
	timestampBegin = 10
)
var negInf = math.Inf(-1)
// CompressionRatio 对应 whisper/utils.py
func CompressionRatio(text string) float64 {
	b := []byte(text)
	var buf bytes.Buffer
	w := zlib.NewWriter(&buf)
	w.Write(b)
	w.Close()
	return float64(len(b)) / float64(buf.Len())
}
// RankerScore 对应 MaximumLikelihoodRanker：penalty 为 None 用长度，否则 ((5+L)/6)**α
func RankerScore(logprob float64, length int, alpha float64, useAlpha bool) float64 {
	if !useAlpha {
		return logprob / float64(length)
	}
	return logprob / math.Pow((5+float64(length))/6, alpha)
}
// Rank 返回分数最高的下标
func Rank(logprobs []float64, lengths []int, alpha float64, useAlpha bool) int {
	best, bi := math.Inf(-1), 0
	for i := range logprobs {
		s := RankerScore(logprobs[i], lengths[i], alpha, useAlpha)
		if s > best {
			best, bi = s, i
		}
	}
	return bi
}
func logSoftmax(logits []float64) []float64 {
	m := math.Inf(-1)
	for _, v := range logits {
		if v > m {
			m = v
		}
	}
	z := 0.0
	for _, v := range logits {
		if v != negInf {
			z += math.Exp(v - m)
		}
	}
	z = m + math.Log(z)
	out := make([]float64, len(logits))
	for i, v := range logits {
		out[i] = v - z
	}
	return out
}
// GreedyStep 对应 GreedyDecoder.update 的单条版本（temperature=0 走 argmax）
func GreedyStep(tokens []int, logits []float64, sumLogprob float64) ([]int, float64, bool) {
	nxt := 0
	bv := math.Inf(-1)
	for k, v := range logits {
		if v > bv {
			bv, nxt = v, k
		}
	}
	lp := logSoftmax(logits)[nxt]
	if tokens[len(tokens)-1] != eot {
		sumLogprob += lp
	}
	if tokens[len(tokens)-1] == eot {
		nxt = eot
	}
	out := append(append([]int{}, tokens...), nxt)
	return out, sumLogprob, out[len(out)-1] == eot
}
// ApplyTimestampRules 对应 decoding.py：成对、单调不减、首步必须是时间戳
func ApplyTimestampRules(logits []float64, tokens []int, sampleBegin int,
	maxInitialIndex int) []float64 {
	out := append([]float64{}, logits...)
	if noTimestamps < len(out) {
		out[noTimestamps] = negInf
	}
	seq := tokens[sampleBegin:]
	lastTS := len(seq) >= 1 && seq[len(seq)-1] >= timestampBegin
	penultTS := len(seq) < 2 || seq[len(seq)-2] >= timestampBegin
	if lastTS {
		if penultTS { // has to be non-timestamp
			for k := timestampBegin; k < len(out); k++ {
				out[k] = negInf
			}
		} else { // cannot be normal text tokens
			for k := 0; k < eot && k < len(out); k++ {
				out[k] = negInf
			}
		}
	}
	last := -1
	for _, t := range seq {
		if t >= timestampBegin {
			last = t
		}
	}
	if last >= 0 {
		limit := last + 1
		if lastTS && !penultTS {
			limit = last
		}
		for k := timestampBegin; k < limit && k < len(out); k++ {
			out[k] = negInf
		}
	}
	if len(tokens) == sampleBegin {
		for k := 0; k < timestampBegin && k < len(out); k++ {
			out[k] = negInf
		}
		if maxInitialIndex >= 0 {
			for k := timestampBegin + maxInitialIndex + 1; k < len(out); k++ {
				out[k] = negInf
			}
		}
	}
	return out
}
// BeamMaxCandidates patience 缺省为 1.0；max_candidates = round(beam_size*patience)
func BeamMaxCandidates(beamSize int, patience float64) int {
	p := patience
	if patience == 0 {
		p = 1.0
	}
	return int(math.Round(float64(beamSize) * p))
}
type DecResult struct {
	CompressionRatio float64
	AvgLogprob       float64
	NoSpeechProb     float64
}
// DecodeWithFallback 对应 transcribe.py 的 decode_with_fallback
func DecodeWithFallback(decode func(t float64) DecResult,
	temps []float64, crTh, lpTh, nspTh float64) (DecResult, float64, bool) {
	var res DecResult
	used := temps[0]
	fellBack := false
	for i, t := range temps {
		res = decode(t)
		used = t
		needs := false
		if crTh != 0 && res.CompressionRatio > crTh {
			needs = true
		}
		if lpTh != 0 && res.AvgLogprob < lpTh {
			needs = true
		}
		if nspTh != 0 && res.NoSpeechProb > nspTh && lpTh != 0 && res.AvgLogprob < lpTh {
			needs = false // silence
		}
		if !needs {
			return res, used, i > 0
		}
		fellBack = true
	}
	return res, used, fellBack
}
