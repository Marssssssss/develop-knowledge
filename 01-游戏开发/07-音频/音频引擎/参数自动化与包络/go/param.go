// Package param 实现 W3C Web Audio API §1.6.2 的 AudioParam 自动化事件表语义：
// 五类事件、不按采样率量化的取值公式、曲线区间互斥、两种取消与 a-rate/k-rate 渲染。
package param

import (
	"errors"
	"math"
)

// 事件类型（规范原文拼写）。
const (
	SetValue           = "SetValue"
	LinearRampToValue  = "LinearRampToValue"
	ExponentialRamp    = "ExponentialRampToValue"
	SetTarget          = "SetTarget"
	SetValueCurve      = "SetValueCurve"
)

// ErrRange / ErrNotSupported 对应规范的 RangeError / NotSupportedError。
var (
	ErrRange        = errors.New("RangeError")
	ErrNotSupported = errors.New("NotSupportedError")
)

// Event 是自动化事件表的一项。
type Event struct {
	Kind     string
	Time     float64
	Value    float64   // SetValue / 斜坡终点 V1
	Target   float64   // SetTarget 的 V1
	Tau      float64   // SetTarget 时间常数
	Values   []float64 // SetValueCurve 的值数组
	Duration float64   // SetValueCurve 时长
}

// EndTime 返回事件的结束时刻（曲线为 T0+TD，其余为自身时刻）。
func (e Event) EndTime() float64 {
	if e.Kind == SetValueCurve {
		return e.Time + e.Duration
	}
	return e.Time
}

// AudioParam 是可被自动化的参数。
type AudioParam struct {
	DefaultValue   float64
	Value          float64
	AutomationRate string
	Events         []Event
	CurrentTime    float64
}

// NewParam 构造参数，默认 a-rate。
func NewParam(value float64) *AudioParam {
	return &AudioParam{DefaultValue: value, Value: value,
		AutomationRate: "a-rate"}
}

func (p *AudioParam) clampTime(t float64) (float64, error) {
	if t < 0 {
		return 0, ErrRange
	}
	if t < p.CurrentTime { // 小于 currentTime 时钳到 currentTime
		return p.CurrentTime, nil
	}
	return t, nil
}

func (p *AudioParam) insert(e Event) (*Event, error) {
	idx := 0
	for i, cur := range p.Events {
		if cur.Time <= e.Time {
			idx = i + 1
		}
	}
	p.Events = append(p.Events, Event{})
	copy(p.Events[idx+1:], p.Events[idx:])
	p.Events[idx] = e
	return &p.Events[idx], nil
}

// guard 检查曲线区间 (T0, T0+TD) 内是否已有事件或与其他曲线重叠。
func (p *AudioParam) guard(start, duration float64, isCurve bool) error {
	end := start
	if isCurve {
		end = start + duration
	}
	for _, e := range p.Events {
		if start < e.Time && e.Time < end {
			return ErrNotSupported
		}
		if e.Kind == SetValueCurve {
			eEnd := e.EndTime()
			if (e.Time < start && start < eEnd) || (isCurve && e.Time < end && end < eEnd) {
				return ErrNotSupported
			}
		}
	}
	return nil
}

// SetValueAtTime 对应 setValueAtTime(value, startTime)。
func (p *AudioParam) SetValueAtTime(value, startTime float64) error {
	t, err := p.clampTime(startTime)
	if err != nil {
		return err
	}
	if err := p.guard(t, 0, false); err != nil {
		return err
	}
	_, err = p.insert(Event{Kind: SetValue, Time: t, Value: value})
	return err
}

// LinearRampToValueAtTime 对应 linearRampToValueAtTime(value, endTime)。
func (p *AudioParam) LinearRampToValueAtTime(value, endTime float64) error {
	t, err := p.clampTime(endTime)
	if err != nil {
		return err
	}
	if err := p.guard(t, 0, false); err != nil {
		return err
	}
	_, err = p.insert(Event{Kind: LinearRampToValue, Time: t, Value: value})
	return err
}

// ExponentialRampToValueAtTime 对应 exponentialRampToValueAtTime；value=0 报 RangeError。
func (p *AudioParam) ExponentialRampToValueAtTime(value, endTime float64) error {
	if value == 0 {
		return ErrRange
	}
	t, err := p.clampTime(endTime)
	if err != nil {
		return err
	}
	if err := p.guard(t, 0, false); err != nil {
		return err
	}
	_, err = p.insert(Event{Kind: ExponentialRamp, Time: t, Value: value})
	return err
}

// SetTargetAtTime 对应 setTargetAtTime(target, startTime, timeConstant)。
func (p *AudioParam) SetTargetAtTime(target, startTime, tau float64) error {
	t, err := p.clampTime(startTime)
	if err != nil {
		return err
	}
	if err := p.guard(t, 0, false); err != nil {
		return err
	}
	_, err = p.insert(Event{Kind: SetTarget, Time: t, Target: target, Tau: tau})
	return err
}

// SetValueCurveAtTime 对应 setValueCurveAtTime(values, startTime, duration)。
func (p *AudioParam) SetValueCurveAtTime(values []float64, startTime,
	duration float64) error {
	t, err := p.clampTime(startTime)
	if err != nil {
		return err
	}
	if err := p.guard(t, duration, true); err != nil {
		return err
	}
	_, err = p.insert(Event{Kind: SetValueCurve, Time: t, Values: values,
		Duration: duration})
	return err
}

// CancelScheduledValues 取消 >= cancelTime 的事件。
func (p *AudioParam) CancelScheduledValues(cancelTime float64) error {
	t, err := p.clampTime(cancelTime)
	if err != nil {
		return err
	}
	kept := []Event{}
	for _, e := range p.Events {
		if e.Time < t {
			kept = append(kept, e)
		}
	}
	p.Events = kept
	return nil
}

// CancelAndHoldAtTime 取消 > cancelTime 的事件并把值保持在 cancelTime 时刻。
func (p *AudioParam) CancelAndHoldAtTime(cancelTime float64) error {
	t, err := p.clampTime(cancelTime)
	if err != nil {
		return err
	}
	held := p.ValueAt(t)
	kept := []Event{}
	for _, e := range p.Events {
		if e.Time >= t {
			if e.Kind == SetValueCurve && e.Time < t { // 截断到 cancelTime
				kept = append(kept, Event{Kind: SetValueCurve, Time: e.Time,
					Values: e.Values, Duration: t - e.Time})
			}
			continue
		}
		kept = append(kept, e)
	}
	if len(kept) == 0 || kept[len(kept)-1].Kind != SetValueCurve ||
		kept[len(kept)-1].EndTime() < t {
		kept = append(kept, Event{Kind: SetValue, Time: t, Value: held})
	}
	p.Events = kept
	return nil
}
