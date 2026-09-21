package main

// demo 519 Go 侧:多窗口多燃烧率告警,与 python/mwmb.py 同构。
// 转写对象是 sloth 的 mwmbAlertTpl 与 google-30d.yaml;数值权威来自 python 侧实跑。

import "fmt"

const (
	hourSeconds = 3600.0
	daySeconds  = 86400.0
	minSeconds  = 60.0
)

// Tier 一个告警档位:预算百分比 + 短窗 + 长窗。
type Tier struct {
	Name               string
	ErrorBudgetPercent float64
	ShortWindow        float64
	LongWindow         float64
}

// Google30D Google 默认四档(sloth 的 google-30d.yaml 实读)。
var Google30D = []Tier{
	{"page_quick", 2.0, 5 * minSeconds, 1 * hourSeconds},
	{"page_slow", 5.0, 30 * minSeconds, 6 * hourSeconds},
	{"ticket_quick", 10.0, 2 * hourSeconds, 1 * daySeconds},
	{"ticket_slow", 10.0, 6 * hourSeconds, 3 * daySeconds},
}

// BurnFactor 与 demo 518 同源的 getBurnRateFactor。
func BurnFactor(errorBudgetPercent, sloPeriodSeconds, longWindowSeconds float64) (float64, error) {
	if longWindowSeconds <= 0 {
		return 0, fmt.Errorf("consumption window must be positive")
	}
	hoursRequired := errorBudgetPercent * (sloPeriodSeconds / hourSeconds) / 100.0
	return hoursRequired / (longWindowSeconds / hourSeconds), nil
}

// Thresholds 四档各自的错误率阈值 = 燃烧率 × (1 - SLO)。
func Thresholds(slo, periodSeconds float64, table []Tier) (map[string]struct {
	Burn      float64
	Threshold float64
	Short     float64
	Long      float64
}, error) {
	if !(slo >= 0 && slo < 1) {
		return nil, fmt.Errorf("SLO must be in [0, 1)")
	}
	budgetRatio := 1.0 - slo
	out := map[string]struct {
		Burn      float64
		Threshold float64
		Short     float64
		Long      float64
	}{}
	for _, t := range table {
		b, err := BurnFactor(t.ErrorBudgetPercent, periodSeconds, t.LongWindow)
		if err != nil {
			return nil, err
		}
		out[t.Name] = struct {
			Burn      float64
			Threshold float64
			Short     float64
			Long      float64
		}{b, b * budgetRatio, t.ShortWindow, t.LongWindow}
	}
	return out, nil
}

// Fires 单档判定:短窗与长窗都破线才算。模板用的是严格 `>`。
func Fires(rateShort, rateLong, threshold float64, allowEqual bool) bool {
	if allowEqual {
		return rateShort >= threshold && rateLong >= threshold
	}
	return rateShort > threshold && rateLong > threshold
}

// TrailingAvg 时间线在时刻 t 的窗口均值,区间 (t-window, t](左开右闭)。
// 窗口内无样本时返回 (0, false) —— 是"无数据"而不是"错误率为 0"。
func TrailingAvg(timeline []float64, t, windowSeconds, stepSeconds float64) (float64, bool) {
	lo := t - windowSeconds
	sum, n := 0.0, 0
	for i, r := range timeline {
		tt := float64(i) * stepSeconds
		if lo < tt && tt <= t {
			sum += r
			n++
		}
	}
	if n == 0 {
		return 0, false
	}
	return sum / float64(n), true
}

// EvaluateBoth 一次算出 page / ticket 是否告警,并给出命中的档位名。
func EvaluateBoth(rates map[float64]float64, slo, periodSeconds float64, table []Tier) (bool, bool, string) {
	th, err := Thresholds(slo, periodSeconds, table)
	if err != nil {
		return false, false, ""
	}
	page, ticket, hit := false, false, ""
	for _, t := range table {
		spec := th[t.Name]
		rs, okS := rates[spec.Short]
		rl, okL := rates[spec.Long]
		if !okS || !okL {
			continue // 无数据的窗口不参与判定
		}
		if !Fires(rs, rl, spec.Threshold, false) {
			continue
		}
		if t.Name == "page_quick" || t.Name == "page_slow" {
			page = true
		} else {
			ticket = true
		}
		if hit == "" {
			hit = t.Name
		}
	}
	return page, ticket, hit
}
