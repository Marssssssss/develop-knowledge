// Package main 是 big-brain（Bevy 的 Utility AI 库）评分与选择链路的 Go 转写。
//
// 对应官方源码（zkat/big-brain，main，逐行实读后转写）：
//   src/scorers.rs（Score / AllOrNothing / SumOfScorers / ProductOfScorers /
//                   WinningScorer / MeasuredScorer）
//   src/measures.rs（WeightedSum / WeightedProduct / ChebyshevDistance / WeightedMeasure）
//   src/evaluators.rs（Linear / Power / Sigmoid + clamp）
//   src/pickers.rs（FirstToScore / Highest / HighestToScore）
//
// 语言差异显式落地：
//   - Rust f32 除以 0 得 inf/-inf → Go 的 float32/64 同样产生 ±Inf，但 Go 不允许
//     对常量做除零，所以奇点必须用变量参与运算（见 sigmoid.evaluate）；
//   - Rust 的 Score::set 越界 panic! → Go 返回 error；
//   - Rust fold(0f32, ...) 的初值语义原样保留（WeightedProduct 恒为 0 的原因）。
package main

import (
	"errors"
	"math"
)

func clamp(val, low, high float64) float64 {
	v := val
	if v > high {
		v = high
	}
	if v < low {
		return low
	}
	return v
}

func clamp01(v float64) float64 { return clamp(v, 0, 1) }

// ---------------------------------------------------------------- Score

type score struct{ v float64 }

var errOutOfRange = errors.New("Score value must be between 0.0 and 1.0")

func (s *score) get() float64 { return s.v }
func (s *score) set(v float64) error {
	if v < 0.0 || v > 1.0 {
		return errOutOfRange
	}
	s.v = v
	return nil
}
func (s *score) setUnchecked(v float64) { s.v = v }

// ---------------------------------------------------------------- Evaluators

type linearEvaluator struct{ xa, ya, xb, yb, dyOverDx float64 }

func newLinear(xa, ya, xb, yb float64) *linearEvaluator {
	return &linearEvaluator{xa: xa, ya: ya, xb: xb, yb: yb, dyOverDx: (yb - ya) / (xb - xa)}
}
func newLinearIdentity() *linearEvaluator { return newLinear(0, 0, 1, 1) }
func newLinearRanged(min, max float64) *linearEvaluator {
	return newLinear(min, 0, max, 1)
}
func newLinearInversed() *linearEvaluator { return newLinearRanged(1, 0) }

func (l *linearEvaluator) evaluate(value float64) float64 {
	return clamp(l.ya+l.dyOverDx*(value-l.xa), l.ya, l.yb)
}

type powerEvaluator struct {
	power     float64
	xa, ya, xb float64
	dy         float64
}

func newPower(power, xa, ya, xb, yb float64) *powerEvaluator {
	return &powerEvaluator{power: clamp(power, 0, 10000), xa: xa, ya: ya, xb: xb, dy: yb - ya}
}
func newPowerDefault(power float64) *powerEvaluator { return newPower(power, 0, 0, 1, 1) }

func (p *powerEvaluator) evaluate(value float64) float64 {
	cx := clamp(value, p.xa, p.xb)
	return p.dy*math.Pow((cx-p.xa)/(p.xb-p.xa), p.power) + p.ya
}

type sigmoidEvaluator struct {
	k                                   float64
	xa, xb, ya, yb                      float64
	twoOverDx, xMean, yMean, dyOverTwo  float64
	oneMinusK                           float64
}

// newSigmoid：官方 two_over_dx = |2 / (xb - ya)|，用的是 ya 而不是 xa
func newSigmoid(k, xa, ya, xb, yb float64) *sigmoidEvaluator {
	return &sigmoidEvaluator{
		k:          clamp(k, -0.99999, 0.99999),
		xa:         xa, xb: xb, ya: ya, yb: yb,
		twoOverDx:  math.Abs(2.0 / (xb - ya)),
		xMean:      (xa + xb) / 2.0,
		yMean:      (ya + yb) / 2.0,
		dyOverTwo:  (yb - ya) / 2.0,
		oneMinusK:  1.0 - clamp(k, -0.99999, 0.99999),
	}
}
func newSigmoidDefault(k float64) *sigmoidEvaluator { return newSigmoid(k, 0, 0, 1, 1) }

func (s *sigmoidEvaluator) evaluate(x float64) float64 {
	d := clamp(x, s.xa, s.xb) - s.xMean
	numerator := s.twoOverDx * d * s.oneMinusK
	// 分母可能为 0：Go 对变量除零产生 ±Inf，与 Rust f32 语义一致
	denominator := s.k*math.Abs(1.0-2.0*(s.twoOverDx*d)) + 1.0
	return clamp(s.dyOverTwo*(numerator/denominator)+s.yMean, s.ya, s.yb)
}

// ---------------------------------------------------------------- Measures

type weightedSum struct{}

func (weightedSum) calculate(scores []float64, weights []float64) float64 {
	acc := 0.0
	for i := range scores {
		acc += scores[i] * weights[i]
	}
	return acc
}

// weightedProduct：官方 fold 初值是 0.0 ⇒ 结果恒为 0
type weightedProduct struct{}

func (weightedProduct) calculate(scores []float64, weights []float64) float64 {
	acc := 0.0
	for i := range scores {
		acc = acc * scores[i] * weights[i]
	}
	return acc
}

type chebyshevDistance struct{}

func (chebyshevDistance) calculate(scores []float64, weights []float64) float64 {
	best := 0.0
	for i := range scores {
		if v := scores[i] * weights[i]; v > best {
			best = v
		}
	}
	return best
}

// weightedMeasure：默认 measure，加权二次平均 sqrt(Σ (w/Σw) * s²)
type weightedMeasure struct{}

func (weightedMeasure) calculate(scores []float64, weights []float64) float64 {
	wsum := 0.0
	for _, w := range weights {
		wsum += w
	}
	if wsum == 0.0 {
		return 0.0
	}
	acc := 0.0
	for i := range scores {
		acc += weights[i] / wsum * scores[i] * scores[i]
	}
	return math.Sqrt(acc)
}

// ---------------------------------------------------------------- 复合 Scorer

func allOrNothing(children []float64, threshold float64) float64 {
	sum := 0.0
	for _, s := range children {
		if s < threshold { // 判据是 <：等于阈值放行
			return 0.0
		}
		sum += s
	}
	return clamp01(sum)
}

func sumOfScorers(children []float64, threshold float64) float64 {
	sum := 0.0
	for _, s := range children {
		sum += s
	}
	if sum < threshold {
		return 0.0
	}
	return clamp01(sum)
}

func productOfScorers(children []float64, threshold float64, useCompensation bool) float64 {
	product := 1.0
	n := 0
	for _, s := range children {
		product *= s
		n++
	}
	if useCompensation && product < 1.0 {
		modFactor := 1.0 - 1.0/float64(n)
		makeup := (1.0 - product) * modFactor
		product += makeup * product
	}
	if product < threshold {
		return 0.0
	}
	return clamp01(product)
}

func winningScorer(children []float64, threshold float64) float64 {
	if len(children) == 0 {
		return 0.0
	}
	best := children[0]
	for _, s := range children[1:] {
		if s > best {
			best = s
		}
	}
	if best < threshold {
		return 0.0
	}
	return clamp01(best)
}

// ---------------------------------------------------------------- Pickers

type choice struct {
	label string
	value float64
}

func pickFirstToScore(choices []choice, threshold float64) *choice {
	for i := range choices {
		if choices[i].value >= threshold { // 非严格
			return &choices[i]
		}
	}
	return nil
}

func pickHighest(choices []choice) *choice {
	var best *choice
	maxScore := 0.0
	for i := range choices {
		if choices[i].value <= maxScore || choices[i].value <= 0.0 {
			continue
		}
		maxScore = choices[i].value
		best = &choices[i]
	}
	return best
}

func pickHighestToScore(choices []choice, threshold float64) *choice {
	var best *choice
	highest := 0.0
	for i := range choices {
		if choices[i].value <= threshold || choices[i].value <= highest {
			continue
		}
		highest = choices[i].value
		best = &choices[i]
	}
	return best
}
