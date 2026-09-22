// Package fanout 量化扇出放大，并实现 gRPC 的对冲策略与限流。
//
// 量化口径来自 Dean & Barroso《The Tail at Scale》：
//   - 单机 10ms 典型、p99 = 1s 时，扇出 100 台 -> 63% 的请求超过 1s；
//   - 单机 1/10000 超过 1s、扇出 2000 台 -> 几乎 1/5 的请求超过 1s；
//   - 对冲：secondary 推迟到 primary 已 outstanding 超过 p95 之后，额外负载约 5%；
//     Google benchmark（1000 keys / 100 servers、10ms 后对冲）：p99.9 从 1800ms 降到 74ms，
//     只多发 2% 的请求。
//
// 策略口径来自 gRPC Request Hedging 指南：
//   - maxAttempts 必填，>5 时按 5 处理；hedgingDelay 不填则所有请求同时发出；
//   - deadline 作用于整条对冲链；
//   - 限流：失败 -1、成功 +token_ratio，对冲仅在 token_count > maxTokens/2 时发出；
//   - pushback 用 metadata grpc-retry-pushback-ms，负值或不可解析 = 不要重试。
//
// Go 侧的实现注意点：
//   - math.Pow(1-p, n) 在 n 很大时会损失精度，放大概率一律走 exp/log1p 形式；
//   - token_count 是浮点（token_ratio 可以是 0.1 这类小数），
//     判定用严格大于，与 gRPC 文档 "greater than the threshold" 一致。
package fanout

import "math"

// Amplify 返回"至少一个下游超阈值"的概率：1 - (1-p)^n。
// 用 exp/log1p 而非 Pow，避免 n 大时 (1-p)^n 被舍入成 0。
func Amplify(p float64, n int) float64 {
	if p <= 0 {
		return 0
	}
	if p >= 1 {
		return 1
	}
	return -math.Expm1(float64(n) * math.Log1p(-p))
}

// MaxTolerableFanout 返回使放大概率不超过 target 的最大扇出数。
func MaxTolerableFanout(p, target float64) int {
	if p <= 0 {
		return 1 << 40
	}
	if p >= 1 {
		return 0
	}
	n := math.Floor(math.Log(1-target) / math.Log1p(-p))
	if n < 0 {
		return 0
	}
	return int(n)
}

// EffectiveAttempts 是 maxAttempts 的生效值：>5 一律按 5。
func EffectiveAttempts(maxAttempts int) int {
	if maxAttempts > 5 {
		return 5
	}
	if maxAttempts < 0 {
		return 0
	}
	return maxAttempts
}

// HedgeThrottle 是 RetryThrottlingPolicy 的令牌桶。
type HedgeThrottle struct {
	MaxTokens  int
	TokenRatio float64
	Tokens     float64
}

// NewHedgeThrottle 建桶，初始 token_count = maxTokens。
func NewHedgeThrottle(maxTokens int, tokenRatio float64) *HedgeThrottle {
	return &HedgeThrottle{MaxTokens: maxTokens, TokenRatio: tokenRatio, Tokens: float64(maxTokens)}
}

// Threshold 是 maxTokens / 2；对冲请求只在 token_count **严格大于**它时才发出。
func (h *HedgeThrottle) Threshold() float64 { return float64(h.MaxTokens) / 2.0 }

// OnFailure 失败减 1，下限 0。
func (h *HedgeThrottle) OnFailure() {
	h.Tokens--
	if h.Tokens < 0 {
		h.Tokens = 0
	}
}

// OnSuccess 成功加 tokenRatio，上限 maxTokens。
func (h *HedgeThrottle) OnSuccess() {
	h.Tokens += h.TokenRatio
	if h.Tokens > float64(h.MaxTokens) {
		h.Tokens = float64(h.MaxTokens)
	}
}

// MayHedge 判断是否允许发出对冲请求。
func (h *HedgeThrottle) MayHedge() bool { return h.Tokens > h.Threshold() }

// ParsePushbackMS 解析 grpc-retry-pushback-ms；返回 (毫秒, 是否有效)。
// 负值、空值、不可解析都返回 false，语义是"服务端要求不要重试"。
func ParsePushbackMS(value string, ok bool) (float64, bool) {
	if !ok || value == "" {
		return 0, false
	}
	v, positive := parseSignedDecimal(value)
	if !positive || v < 0 {
		return 0, false
	}
	return v, true
}

// parseSignedDecimal 只接受可选符号 + 数字（含一个小数点），不接受 "abc" / "1e3"。
func parseSignedDecimal(s string) (float64, bool) {
	i, sign := 0, 1.0
	if i < len(s) && (s[i] == '+' || s[i] == '-') {
		if s[i] == '-' {
			sign = -1
		}
		i++
	}
	intPart, digits := 0.0, 0
	for i < len(s) && s[i] >= '0' && s[i] <= '9' {
		intPart = intPart*10 + float64(s[i]-'0')
		digits++
		i++
	}
	if digits == 0 {
		return 0, false
	}
	frac, scale := 0.0, 1.0
	if i < len(s) && s[i] == '.' {
		i++
		for i < len(s) && s[i] >= '0' && s[i] <= '9' {
			frac = frac*10 + float64(s[i]-'0')
			scale *= 10
			i++
		}
	}
	if i != len(s) {
		return 0, false // 仍有残余字符：不可解析
	}
	return sign * (intPart + frac/scale), true
}

// HedgeOutcome 是一次对冲的结果。
type HedgeOutcome struct {
	Latency   float64 // 客户端感知到的延迟
	SentExtra bool    // 是否真的发出了 secondary
}

// SimulateHedge 模拟一次对冲：primary 在 0 时刻发出，delayMS 后发 secondary，取先回者。
func SimulateHedge(primary, secondary, delayMS float64) HedgeOutcome {
	if primary <= delayMS {
		return HedgeOutcome{Latency: primary, SentExtra: false}
	}
	h := delayMS + secondary
	if h < primary {
		return HedgeOutcome{Latency: h, SentExtra: true}
	}
	return HedgeOutcome{Latency: primary, SentExtra: true}
}
