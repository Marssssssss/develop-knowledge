package main

// demo 518 Go 侧:错误预算与 burn rate,与 python/budget.py 同构。
// 数值权威来自 python 侧实跑;Go 侧经人工审查 + bracket/sanity/crossref 三道检查。

import (
	"fmt"
	"math"
)

const (
	hourSeconds = 3600.0
	daySeconds  = 24.0 * hourSeconds
)

// ErrorBudget 错误预算 = 1 - SLO。SLO 取值域 [0, 1)。
func ErrorBudget(slo float64) (float64, error) {
	if !(slo >= 0.0 && slo < 1.0) {
		return 0, fmt.Errorf("SLO must be in [0, 1), got %v", slo)
	}
	return 1.0 - slo, nil
}

// BudgetMinutes 把错误预算换算成"允许不可靠的分钟数"。99.9% / 30 天 = 43.2 分钟。
func BudgetMinutes(slo, periodSeconds float64) (float64, error) {
	eb, err := ErrorBudget(slo)
	if err != nil {
		return 0, err
	}
	return eb * periodSeconds / 60.0, nil
}

// BurnRate burn rate = 实际错误率 / 错误预算率。1 表示恰好按 SLO 的速度花预算。
func BurnRate(errorRate, slo float64) (float64, error) {
	eb, err := ErrorBudget(slo)
	if err != nil {
		return 0, err
	}
	return errorRate / eb, nil
}

// BudgetConsumed 窗口内消耗的预算占整期预算的比例。
func BudgetConsumed(errorRate, slo, windowSeconds, periodSeconds float64) (float64, error) {
	eb, err := ErrorBudget(slo)
	if err != nil {
		return 0, err
	}
	return errorRate * windowSeconds / (eb * periodSeconds), nil
}

// TimeToExhaustion 按当前燃烧率烧光整期预算所需时间 = period / burn。
func TimeToExhaustion(burn, periodSeconds float64) float64 {
	if burn <= 0 {
		// 注意:Go 里写 1.0/0.0 是编译期错误(常量除零),必须走 math.Inf
		return math.Inf(1)
	}
	return periodSeconds / burn
}

// BurnRateFactor 照 sloth 的 getBurnRateFactor 转写:
//
//	hoursRequiredConsumption = errorBudgetPercent * totalWindow.Hours() / 100
//	speed = hoursRequiredConsumption / consumptionWindow.Hours()
func BurnRateFactor(errorBudgetPercent, sloPeriodSeconds, consumptionWindowSeconds float64) (float64, error) {
	if consumptionWindowSeconds <= 0 {
		return 0, fmt.Errorf("consumption window must be positive")
	}
	hoursRequired := errorBudgetPercent * (sloPeriodSeconds / hourSeconds) / 100.0
	return hoursRequired / (consumptionWindowSeconds / hourSeconds), nil
}

// Window sloth 的 alert.Window:预算百分比 + 短窗 + 长窗。
type Window struct {
	ErrorBudgetPercent float64
	ShortWindow        float64
	LongWindow         float64
}

// Validate 源码要求三个字段都非 0。
func (w Window) Validate() error {
	if w.LongWindow == 0 {
		return fmt.Errorf("long window is required")
	}
	if w.ShortWindow == 0 {
		return fmt.Errorf("short window is required")
	}
	if w.ErrorBudgetPercent == 0 {
		return fmt.Errorf("error budget is required")
	}
	return nil
}

// Speed 该档位需要的燃烧率。
func (w Window) Speed(sloPeriodSeconds float64) (float64, error) {
	if err := w.Validate(); err != nil {
		return 0, err
	}
	return BurnRateFactor(w.ErrorBudgetPercent, sloPeriodSeconds, w.LongWindow)
}

// Windows sloth 的 alert.Windows:page/ticket × quick/slow 四档。
type Windows struct {
	SLOPeriod    float64
	PageQuick    Window
	PageSlow     Window
	TicketQuick  Window
	TicketSlow   Window
}

// Speeds 四档燃烧率;出错时返回 error 而不是静默给 0。
func (w Windows) Speeds() (map[string]float64, error) {
	if w.SLOPeriod == 0 {
		return nil, fmt.Errorf("slo period is required")
	}
	out := map[string]float64{}
	for name, win := range map[string]Window{
		"page_quick":   w.PageQuick,
		"page_slow":    w.PageSlow,
		"ticket_quick": w.TicketQuick,
		"ticket_slow":  w.TicketSlow,
	} {
		s, err := win.Speed(w.SLOPeriod)
		if err != nil {
			return nil, err
		}
		out[name] = s
	}
	return out, nil
}
