package param

import (
	"math"
)

// valueBefore 返回第 idx 个事件起点处的值 V0。
func (p *AudioParam) valueBefore(idx int) float64 {
	if idx == 0 {
		return p.Value
	}
	return p.ValueAt(p.Events[idx].Time)
}

// ValueAt 按事件表求 t 时刻的参数值。
func (p *AudioParam) ValueAt(t float64) float64 {
	if len(p.Events) == 0 {
		return p.Value
	}
	idx := -1
	nxt := -1
	for i := range p.Events {
		if p.Events[i].Time > t {
			nxt = i
			break
		}
	}
	switch {
	case nxt >= 0 && (p.Events[nxt].Kind == LinearRampToValue ||
		p.Events[nxt].Kind == ExponentialRamp || p.Events[nxt].Kind == SetValueCurve):
		idx = nxt
	case nxt > 0:
		idx = nxt - 1
	case nxt == 0:
		idx = -1
	default:
		idx = len(p.Events) - 1
	}
	if idx < 0 {
		return p.Value
	}
	e := p.Events[idx]
	switch e.Kind {
	case SetValue:
		return e.Value
	case SetValueCurve:
		n := len(e.Values)
		if t >= e.Time+e.Duration {
			return e.Values[n-1]
		}
		pos := float64(n-1) * (t - e.Time) / e.Duration
		k := int(math.Floor(pos))
		if k >= n-1 {
			return e.Values[n-1]
		}
		return e.Values[k] + (e.Values[k+1]-e.Values[k])*(pos-float64(k))
	case SetTarget:
		v0 := p.valueBefore(idx)
		return e.Target + (v0-e.Target)*math.Exp(-(t-e.Time)/e.Tau)
	}
	v0 := p.valueBefore(idx)
	t0 := p.CurrentTime
	if idx > 0 {
		t0 = p.Events[idx-1].Time
	}
	if t >= e.Time || math.Abs(e.Time-t0) < 1e-12 {
		return e.Value
	}
	ratio := (t - t0) / (e.Time - t0)
	if e.Kind == LinearRampToValue {
		return v0 + (e.Value-v0)*ratio
	}
	if v0 == 0 || (v0 > 0) != (e.Value > 0) { // 异号或 V0=0：整段恒等于 V0
		return v0
	}
	return v0 * math.Pow(e.Value/v0, ratio)
}

// RenderBlock 按 automationRate 渲染一块：a-rate 逐样本，k-rate 取块首值。
func (p *AudioParam) RenderBlock(startTime float64, count int,
	sampleRate float64) []float64 {
	out := make([]float64, 0, count)
	if p.AutomationRate == "k-rate" {
		head := p.ValueAt(startTime)
		for i := 0; i < count; i++ {
			out = append(out, head)
		}
		return out
	}
	for i := 0; i < count; i++ {
		out = append(out, p.ValueAt(startTime+float64(i)/sampleRate))
	}
	return out
}

// ADSR 用规范允许的自动化搭一个 ADSR：释音段用 setTargetAtTime（指数斜坡不能到 0）。
func (p *AudioParam) ADSR(peak, attack, decay, sustainLevel, release,
	startTime, noteDuration float64) error {
	if err := p.SetValueAtTime(0.0, startTime); err != nil {
		return err
	}
	if err := p.LinearRampToValueAtTime(peak, startTime+attack); err != nil {
		return err
	}
	if err := p.LinearRampToValueAtTime(peak*sustainLevel,
		startTime+attack+decay); err != nil {
		return err
	}
	noteEnd := startTime + noteDuration
	if err := p.SetValueAtTime(peak*sustainLevel, noteEnd); err != nil {
		return err
	}
	return p.SetTargetAtTime(0.0, noteEnd, release)
}
