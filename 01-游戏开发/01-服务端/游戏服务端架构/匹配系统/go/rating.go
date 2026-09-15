// rating.go — 评分模型: Elo 与 TrueSkill(常量 / 类型 / 公式)
//
// 与 python/rating.py、c/rating.c 数值一致:
//
//	TrueSkill: 新玩家 mu=25, sigma=8.333, 展示分 = mu - 3*sigma = 0;
//	Elo: 期望胜率 E = 1/(1+10^((Rb-Ra)/400))。
package main

import "math"

const (
	mu0      = 25.0
	sigma0   = 25.0 / 3.0
	beta     = sigma0 / 2.0     // 表现围绕技能波动
	tau      = sigma0 / 100.0   // 赛前 sigma 微增("动量")
	kDisplay = 3.0              // 保守估计 mu - 3*sigma
	eloK     = 32.0
)

// rating: 技能信念 —— 均值 mu + 不确定性 sigma
type rating struct {
	mu    float64
	sigma float64
}

// rng: 线性同余发生器, 与 Python/C 同一序列, 保证跨语言结果可比
type rng struct{ s uint32 }

func newRng(seed uint32) *rng { return &rng{s: seed} }

func (r *rng) u32() uint32 {
	r.s = (1664525*r.s + 1013904223) & 0xFFFFFFFF
	return r.s
}

func (r *rng) u() float64 { return float64(r.u32()) / 4294967296.0 }

// normal: Irwin-Hall —— 4 个均匀分布之和近似标准正态
func (r *rng) normal() float64 {
	return (r.u() + r.u() + r.u() + r.u() - 2.0) * math.Sqrt(3.0)
}

func normPdf(x float64) float64 { return math.Exp(-x*x/2.0) / math.Sqrt(2.0*math.Pi) }

func normCdf(x float64) float64 { return 0.5 * (1.0 + math.Erf(x/math.Sqrt2)) }

// ---------------------------------------------------------------- Elo
func eloExpect(ra, rb float64) float64 {
	return 1.0 / (1.0 + math.Pow(10.0, (rb-ra)/400.0))
}

func eloUpdate(ra, rb, scoreA float64) (float64, float64) {
	ea := eloExpect(ra, rb)
	return ra + eloK*(scoreA-ea), rb + eloK*((1.0-scoreA)-(1.0-ea))
}

// ---------------------------------------------------------- TrueSkill
func display(mu, sigma float64) float64 { return mu - kDisplay*sigma }

// ts1v1: 双人贝叶斯更新(先加动量, 再按胜负更新)。
//
//	c^2 = 2*beta^2 + sigma_w^2 + sigma_l^2 是总方差;
//	t = (mu_w - mu_l)/c; v = phi(t)/Phi(t); w = v*(v+t)。
func ts1v1(win, lose rating) (rating, rating) {
	sw := math.Sqrt(win.sigma*win.sigma + tau*tau)
	sl := math.Sqrt(lose.sigma*lose.sigma + tau*tau)
	c2 := 2.0*beta*beta + sw*sw + sl*sl
	c := math.Sqrt(c2)
	t := (win.mu - lose.mu) / c
	v := normPdf(t) / normCdf(t)
	w := v * (v + t)
	return rating{win.mu + sw*sw/c*v, math.Sqrt(math.Max(0.0, sw*sw*(1.0-sw*sw/c2*w)))},
		rating{lose.mu - sl*sl/c*v, math.Sqrt(math.Max(0.0, sl*sl*(1.0-sl*sl/c2*w)))}
}

// matchQuality: 匹配质量 = (虚拟)平局概率, 0(最差)~1(最好)
func matchQuality(a, b rating) float64 {
	denom := 2.0*beta*beta + a.sigma*a.sigma + b.sigma*b.sigma
	d := a.mu - b.mu
	return math.Sqrt(2.0*beta*beta/denom) * math.Exp(-d*d/(2.0*denom))
}
