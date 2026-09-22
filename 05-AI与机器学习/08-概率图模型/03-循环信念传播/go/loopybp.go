// Package main 循环信念传播（loopy BP）：收敛判据与消息误差。
//
// 依据（本轮实读）：Ihler, Fisher & Willsky, "Loopy Belief Propagation:
// Convergence and Effects of Message Errors", JMLR 6 (2005) 905–936
//   - 式(3)(4)：Mts(xt) ∝ ψt(xt)∏_{u∈Γt\s} mut(xt)；m^{i+1}_ts(xs) ∝ Σ_xt ψts(xs,xt)Mts(xt)
//   - 式(6)：d(e) = sup sqrt(e(a)/e(b))；Lemma 1：log d(e) = ½(sup log e − inf log e)
//   - 式(7)：d(ψ)² = sup ψ(a,b)ψ(c,d)/(ψ(a,d)ψ(c,b))
//   - Theorem 8：d(e^{i+1}) ≤ [d(ψ)²·d(E^i)+1] / [d(ψ)²+d(E^i)]
//   - Theorem 10（Simon）：max_t Σ_{u∈Γt} log d(ψ_ut) < 1 ⇒ 收敛
//   - Theorem 11：g'_ts(0) = Σ_{u∈Γt\s} (d²−1)/(d²+1) < 1 ⇒ 收敛
//   - 单环（|Γt\s| ≤ 1）⇒ g'(0) < 1 恒成立 ⇒ 必收敛到唯一不动点
package main

import (
	"math"
	"sort"
)

// MRF 二元变量的成对马尔可夫随机场。
type MRF struct {
	Nodes    []string
	Edges    [][2]string
	NodePot  map[string][2]float64
	EdgePot  map[string][2][2]float64
	Gamma    map[string][]string
}

func edgeKey(u, v string) string {
	if u > v {
		u, v = v, u
	}
	return u + "#" + v
}

// NewMRF 建场并预计算邻接表。
func NewMRF(nodes []string, edges [][2]string, nodePot map[string][2]float64,
	edgePot map[string][2][2]float64) *MRF {
	m := &MRF{Nodes: nodes, Edges: edges, NodePot: nodePot, EdgePot: edgePot,
		Gamma: map[string][]string{}}
	for _, e := range edges {
		m.Gamma[e[0]] = append(m.Gamma[e[0]], e[1])
		m.Gamma[e[1]] = append(m.Gamma[e[1]], e[0])
	}
	return m
}

// Psi 取边势函数（对称存、对称取）。
func (m *MRF) Psi(u, v string) [2][2]float64 { return m.EdgePot[edgeKey(u, v)] }

// SymmetricPotential 二元对称势 [[η,1−η],[1−η,η]]。
func SymmetricPotential(eta float64) [2][2]float64 {
	return [2][2]float64{{eta, 1 - eta}, {1 - eta, eta}}
}

// DynamicRange 式(6)：d(e) = sup sqrt(e(a)/e(b))。
func DynamicRange(e []float64) float64 {
	mx, mn := math.Inf(-1), math.Inf(1)
	for _, v := range e {
		if v <= 0 {
			continue
		}
		if v > mx {
			mx = v
		}
		if v < mn {
			mn = v
		}
	}
	if mn == math.Inf(1) {
		return math.Inf(1)
	}
	return math.Sqrt(mx / mn)
}

// LogDynamicRange Lemma 1：½(sup log e − inf log e)。
func LogDynamicRange(e []float64) float64 {
	mx, mn := math.Inf(-1), math.Inf(1)
	for _, v := range e {
		if v <= 0 {
			continue
		}
		if v > mx {
			mx = v
		}
		if v < mn {
			mn = v
		}
	}
	return 0.5 * (math.Log(mx) - math.Log(mn))
}

// PotentialStrength 式(7)：d(ψ)（返回 d(ψ)，不是 d(ψ)²）。
func PotentialStrength(psi [2][2]float64) float64 {
	best := 0.0
	for a := 0; a < 2; a++ {
		for b := 0; b < 2; b++ {
			for c := 0; c < 2; c++ {
				for d := 0; d < 2; d++ {
					den := psi[a][d] * psi[c][b]
					if den <= 0 {
						return math.Inf(1)
					}
					if v := psi[a][b] * psi[c][d] / den; v > best {
						best = v
					}
				}
			}
		}
	}
	return math.Sqrt(best)
}

// ContractionStep Theorem 8 的一步压缩上界。
func ContractionStep(dpsiSq, err float64) float64 {
	return (dpsiSq*err + 1.0) / (dpsiSq + err)
}

// SimonCondition Theorem 10：max_t Σ_{u∈Γt} log d(ψ_ut)。
func SimonCondition(m *MRF) float64 {
	best := 0.0
	for _, t := range m.Nodes {
		s := 0.0
		for _, u := range m.Gamma[t] {
			s += math.Log(PotentialStrength(m.Psi(t, u)))
		}
		if s > best {
			best = s
		}
	}
	return best
}

// Theorem11Derivative Theorem 11 的 max g'(0)。
func Theorem11Derivative(m *MRF) float64 {
	best := 0.0
	for _, t := range m.Nodes {
		for _, s := range m.Gamma[t] {
			acc := 0.0
			for _, u := range m.Gamma[t] {
				if u == s {
					continue
				}
				d := PotentialStrength(m.Psi(t, u))
				d2 := d * d
				acc += (d2 - 1.0) / (d2 + 1.0)
			}
			if acc > best {
				best = acc
			}
		}
	}
	return best
}

// GCurve Theorem 11 的 g_ts(z)。
func GCurve(m *MRF, t, s string, z float64) float64 {
	acc := 0.0
	for _, u := range m.Gamma[t] {
		if u == s {
			continue
		}
		d := PotentialStrength(m.Psi(t, u))
		d2 := d * d
		acc += math.Log((d2*math.Exp(z) + 1.0) / (d2 + math.Exp(z)))
	}
	return acc
}

// MsgKey 消息键：src → dst。
type MsgKey struct{ From, To string }

// InitMessages 全 1 初始化。
func InitMessages(m *MRF) map[MsgKey][2]float64 {
	out := map[MsgKey][2]float64{}
	for _, e := range m.Edges {
		out[MsgKey{e[0], e[1]}] = [2]float64{1, 1}
		out[MsgKey{e[1], e[0]}] = [2]float64{1, 1}
	}
	return out
}

// MessageUpdate m_{t→s}(x_s) ∝ Σ_{x_t} ψts(x_s,x_t) ψt(x_t) ∏_{u∈Γt\s} m_{u→t}(x_t)。
func MessageUpdate(m *MRF, msgs map[MsgKey][2]float64, t, s string) [2]float64 {
	psi := m.Psi(t, s)
	var out [2]float64
	for xs := 0; xs < 2; xs++ {
		acc := 0.0
		for xt := 0; xt < 2; xt++ {
			p := m.NodePot[t][xt] * psi[xs][xt]
			for _, u := range m.Gamma[t] {
				if u == s {
					continue
				}
				p *= msgs[MsgKey{u, t}][xt]
			}
			acc += p
		}
		out[xs] = acc
	}
	z := out[0] + out[1]
	if z > 0 {
		return [2]float64{out[0] / z, out[1] / z}
	}
	return [2]float64{0.5, 0.5}
}

// Belief Mt(xt) ∝ ψt(xt)∏_{u∈Γt} m_{u→t}(xt)。
func Belief(m *MRF, msgs map[MsgKey][2]float64, t string) [2]float64 {
	var out [2]float64
	for xt := 0; xt < 2; xt++ {
		p := m.NodePot[t][xt]
		for _, u := range m.Gamma[t] {
			p *= msgs[MsgKey{u, t}][xt]
		}
		out[xt] = p
	}
	z := out[0] + out[1]
	if z > 0 {
		return [2]float64{out[0] / z, out[1] / z}
	}
	return [2]float64{0.5, 0.5}
}

// RunBP 同步和积 BP；返回 (消息, 每步最大变化, 状态)。
func RunBP(m *MRF, maxIter int, tol float64) (map[MsgKey][2]float64, []float64, string) {
	msgs := InitMessages(m)
	hist := []float64{}
	recent := []map[MsgKey][2]float64{}
	status := "not-converged"
	for i := 0; i < maxIter; i++ {
		next := map[MsgKey][2]float64{}
		for k := range msgs {
			next[k] = MessageUpdate(m, msgs, k.From, k.To)
		}
		delta := 0.0
		for k := range msgs {
			if d := math.Abs(next[k][0] - msgs[k][0]); d > delta {
				delta = d
			}
		}
		hist = append(hist, delta)
		if len(recent) >= 2 && delta > 1e-9 {
			same := 0.0
			for k := range next {
				if d := math.Abs(next[k][0] - recent[len(recent)-2][k][0]); d > same {
					same = d
				}
			}
			if same < tol {
				return next, hist, "oscillating"
			}
		}
		recent = append(recent, next)
		if len(recent) > 3 {
			recent = recent[1:]
		}
		msgs = next
		if delta < tol {
			status = "converged"
			break
		}
	}
	return msgs, hist, status
}
