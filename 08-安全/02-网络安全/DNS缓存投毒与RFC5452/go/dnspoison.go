// Package dnspoison 复现 RFC 5452 §7.2 的组合难度公式，并把 §7/§8/§8.1
// 里给出的每一组数字做成可核对的量。
//
// 与 Python 版的差异显式落地：
//   - Python 的默认参数用 Go 的显式入参替代（NsCount/Ports/... 一律传入）；
//   - TTL 为 0 表示「退化口径：有效 TTL 取窗口 W」；
//   - 时间一律用 float64 秒。
package main

import "math"

// Params 对应 RFC 5452 §7.1 的符号。
type Params struct {
	NsCount    float64 // N：域的权威服务器数量（约 2.5）
	Ports      float64 // P：解析器可用的源端口数量（常为 1）
	IDSpace    float64 // I：可用的 Query ID 数量（最大 65536）
	Window     float64 // W：机会窗口，秒（常为 0.1）
	Outstanding float64 // D：相同的在途查询数（通常为 1）
}

// DefaultParams 是 RFC 5452 §7.1 列出的常用值。
func DefaultParams() Params {
	return Params{NsCount: 2.5, Ports: 1, IDSpace: 65536, Window: 0.1, Outstanding: 1}
}

// ProblemSpace 返回 N*P*I。
func (p Params) ProblemSpace() float64 { return p.NsCount * p.Ports * p.IDSpace }

// PSingle 是 P_s = D*F/(N*P*I)，F 为窗口内到达的伪造包数。
func (p Params) PSingle(fakePackets float64) float64 {
	return p.Outstanding * fakePackets / p.ProblemSpace()
}

// PSingleFromRate 是 F = R*W 之后的 P_s。
func (p Params) PSingleFromRate(ratePps float64) float64 {
	return p.PSingle(ratePps * p.Window)
}

// ReducedConstant 是 §7.2 化简后的分母 N*P*I/(D*W)，文档给的是 1638400。
func (p Params) ReducedConstant() float64 {
	return p.ProblemSpace() / (p.Outstanding * p.Window)
}

// Attempts 是 A = T/TTL；TTL 为 0 时按退化口径取 W。
func (p Params) Attempts(periodS, ttlS float64) float64 {
	effTTL := ttlS
	if effTTL == 0 {
		effTTL = p.Window
	}
	return periodS / effTTL
}

// PCombined 是 P_cs = 1 - (1 - P_s)^A。
func (p Params) PCombined(periodS, ttlS, ratePps float64) float64 {
	ps := p.PSingleFromRate(ratePps)
	return 1.0 - math.Pow(1.0-ps, p.Attempts(periodS, ttlS))
}

// PeriodForProbability 用精确公式反解达到 target 所需的时间（秒）。
func (p Params) PeriodForProbability(target, ttlS, ratePps float64) float64 {
	ps := p.PSingleFromRate(ratePps)
	effTTL := ttlS
	if effTTL == 0 {
		effTTL = p.Window
	}
	return math.Log(1.0-target) / math.Log(1.0-ps) * effTTL
}

// RateForProbability 用精确公式反解达到 target 所需的发包速率（pps）。
func (p Params) RateForProbability(target, periodS, ttlS float64) float64 {
	a := p.Attempts(periodS, ttlS)
	ps := 1.0 - math.Pow(1.0-target, 1.0/a)
	return ps * p.ProblemSpace() / (p.Outstanding * p.Window)
}

// MeanTriesSingleShot 是 §7 首段的 N*P*I/2。
func (p Params) MeanTriesSingleShot() float64 { return p.ProblemSpace() / 2.0 }

// BirthdayPSingle 是 §5 的线性放大：D 个相同在途查询。
func BirthdayPSingle(basePSingle, outstanding float64) float64 {
	return basePSingle * outstanding
}

const dnsResponseBytes = 80

// BandwidthBps 把 pps 换成 bit/s，文档假设的最小应答是 80 字节。
func BandwidthBps(ratePps float64) float64 {
	return ratePps * dnsResponseBytes * 8.0
}

// PortRandomizationGain 源端口随机化把问题空间放大的倍数。
func PortRandomizationGain(ports float64) float64 { return ports }
