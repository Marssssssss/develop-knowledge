// Package overload 实现关键性分级降级、客户端自适应限流稳态与 Envoy 梯度控制器。
//
// 口径：Google SRE Book Chapter 21 "Handling Overload" 与 Envoy《Adaptive Concurrency》。
package overload

import (
	"errors"
	"math"
	"sort"
)

// 四个关键性值（SRE 书原文，从低到高）。
const (
	Sheddable      = "SHEDDABLE"
	SheddablePlus  = "SHEDDABLE_PLUS"
	Critical       = "CRITICAL"
	CriticalPlus   = "CRITICAL_PLUS"
)

// Order 关键性从低到高的顺序。
var Order = []string{Sheddable, SheddablePlus, Critical, CriticalPlus}

// Rank 关键性的序号（0 最低）。
func Rank(c string) (int, error) {
	for i, v := range Order {
		if v == c {
			return i, nil
		}
	}
	return 0, errors.New("unknown criticality")
}

// Shedder 按利用率分级降级；阈值必须随关键性严格升高。
type Shedder struct {
	Thresholds map[string]float64
}

// NewShedder 校验四档齐全且阈值严格递增。
func NewShedder(thresholds map[string]float64) (*Shedder, error) {
	prev := -1.0
	for _, c := range Order {
		v, ok := thresholds[c]
		if !ok {
			return nil, errors.New("missing threshold for " + c)
		}
		if v <= 0 || v > 1 {
			return nil, errors.New("threshold must be in (0,1]")
		}
		if v <= prev {
			return nil, errors.New("thresholds must strictly increase with criticality")
		}
		prev = v
	}
	return &Shedder{Thresholds: thresholds}, nil
}

// Serves 该关键性在当前利用率下是否仍被服务。
func (s *Shedder) Serves(c string, utilization float64) bool {
	return utilization < s.Thresholds[c]
}

// RejectedSet 当前被拒绝的关键性（从低到高）。
func (s *Shedder) RejectedSet(utilization float64) []string {
	out := []string{}
	for _, c := range Order {
		if !s.Serves(c, utilization) {
			out = append(out, c)
		}
	}
	return out
}

// CheckInvariant SRE 书的不变量：拒绝某关键性 ⇒ 所有更低关键性都已被拒绝。
func (s *Shedder) CheckInvariant(utilization float64) bool {
	rej := s.RejectedSet(utilization)
	if len(rej) == 0 {
		return true
	}
	high, _ := Rank(rej[len(rej)-1])
	for _, c := range Order[:high] {
		r, _ := Rank(c)
		if !containsRank(rej, r) {
			return false
		}
	}
	return true
}

func containsRank(list []string, r int) bool {
	for _, c := range list {
		if v, err := Rank(c); err == nil && v == r {
			return true
		}
	}
	return false
}

// ClientThrottleEquilibrium 返回 (sent, accepted, backendRejected, localRejected)。
// 门限把发送速率钉在 S = K·C：未过载全发；后端过载只由后端拒；G > K·C 时客户端自限。
func ClientThrottleEquilibrium(appRate, capacity, K float64) (float64, float64, float64, float64, error) {
	if appRate < 0 || capacity < 0 || K <= 0 {
		return 0, 0, 0, 0, errors.New("invalid parameters")
	}
	sent := math.Min(appRate, K*capacity)
	accepted := math.Min(sent, capacity)
	return sent, accepted, sent - accepted, appRate - sent, nil
}

// BackendRejectRatio 稳态下后端「拒绝/接受」= K − 1。
func BackendRejectRatio(K float64) float64 { return K - 1.0 }

// EnvoyGradient gradient = (minRTT + minRTT*bufferPct) / sampleRTT。
func EnvoyGradient(minRTT, sampleRTT, bufferPct float64) (float64, error) {
	if minRTT <= 0 || sampleRTT <= 0 {
		return 0, errors.New("RTT must be positive")
	}
	return (minRTT + minRTT*bufferPct) / sampleRTT, nil
}

// EnvoyHeadroom headroom 不可配置，固定 sqrt(limit)。
func EnvoyHeadroom(limit float64) float64 { return math.Sqrt(limit) }

// EnvoyUpdate limit_new = gradient*limit_old + headroom，再夹到配置区间。
func EnvoyUpdate(limit, gradient, minLimit, maxLimit float64) float64 {
	v := gradient*limit + EnvoyHeadroom(limit)
	if v < minLimit {
		return minLimit
	}
	if v > maxLimit {
		return maxLimit
	}
	return v
}

// SortedByRank 便于打印：把关键性按序号排序。
func SortedByRank(list []string) []string {
	out := append([]string{}, list...)
	sort.Slice(out, func(i, j int) bool {
		a, _ := Rank(out[i])
		b, _ := Rank(out[j])
		return a < b
	})
	return out
}
