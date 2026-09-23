package overload

import (
	"errors"
	"math"
)

// Log10Root Java 的 LOG10：max(1, (int)log10(t))。
func Log10Root(t int) int {
	if t <= 1 {
		return 1
	}
	v := int(math.Log10(float64(t)))
	if v < 1 {
		return 1
	}
	return v
}

// VegasLimit 是 Netflix concurrency-limits 的 VegasLimit 最小复刻。
type VegasLimit struct {
	EstimatedLimit float64
	MaxLimit       int
	Smoothing      float64
	ProbeMultiplier int
	RTTNoLoad      float64
	ProbeCount     int
	jitter         float64
	jitterSource   func() float64
}

// NewVegasLimit 建立限制器；jitterSource 必须注入（否则行为不可复现）。
func NewVegasLimit(initialLimit, maxConcurrency int, smoothing float64, jitterSource func() float64) (*VegasLimit, error) {
	if initialLimit <= 0 || maxConcurrency <= 0 {
		return nil, errors.New("limits must be positive")
	}
	if jitterSource == nil {
		return nil, errors.New("jitter source is required")
	}
	return &VegasLimit{
		EstimatedLimit:  float64(initialLimit),
		MaxLimit:        maxConcurrency,
		Smoothing:       smoothing,
		ProbeMultiplier: 30,
		jitter:          jitterSource(),
		jitterSource:    jitterSource,
	}, nil
}

// Alpha alpha = 3 * LOG10(limit)。
func (v *VegasLimit) Alpha() int { return 3 * Log10Root(int(v.EstimatedLimit)) }

// Beta beta = 6 * LOG10(limit)。
func (v *VegasLimit) Beta() int { return 6 * Log10Root(int(v.EstimatedLimit)) }

// Threshold threshold = LOG10(limit)。
func (v *VegasLimit) Threshold() int { return Log10Root(int(v.EstimatedLimit)) }

// Increase limit + LOG10(limit)。
func (v *VegasLimit) Increase() float64 {
	return v.EstimatedLimit + float64(Log10Root(int(v.EstimatedLimit)))
}

// Decrease limit - LOG10(limit)。
func (v *VegasLimit) Decrease() float64 {
	return v.EstimatedLimit - float64(Log10Root(int(v.EstimatedLimit)))
}

// QueueSize queueSize = ceil(limit * (1 - rttNoLoad/rtt))。
func QueueSize(limit, rttNoLoad, rtt float64) (int, error) {
	if rtt <= 0 {
		return 0, errors.New("rtt must be >0")
	}
	return int(math.Ceil(limit * (1 - rttNoLoad/rtt))), nil
}

// ShouldProbe probeJitter * probeMultiplier * limit <= probeCount。
func (v *VegasLimit) ShouldProbe() bool {
	return v.jitter*float64(v.ProbeMultiplier)*v.EstimatedLimit <= float64(v.ProbeCount)
}

// Update 按源码判定顺序更新限制。
func (v *VegasLimit) Update(rtt float64, inflight int, didDrop bool) (int, error) {
	if rtt <= 0 {
		return 0, errors.New("rtt must be >0")
	}
	v.ProbeCount++
	if v.ShouldProbe() {
		v.jitter = v.jitterSource()
		v.ProbeCount = 0
		v.RTTNoLoad = rtt
		return int(v.EstimatedLimit), nil
	}
	if v.RTTNoLoad == 0 || rtt < v.RTTNoLoad {
		v.RTTNoLoad = rtt
		return int(v.EstimatedLimit), nil
	}
	est := v.EstimatedLimit
	q, err := QueueSize(est, v.RTTNoLoad, rtt)
	if err != nil {
		return 0, err
	}
	var newLimit float64
	switch {
	case didDrop:
		newLimit = v.Decrease()
	case inflight*2 < int(est):
		return int(est), nil
	case q <= v.Threshold():
		newLimit = est + float64(v.Beta())
	case q < v.Alpha():
		newLimit = v.Increase()
	case q > v.Beta():
		newLimit = v.Decrease()
	default:
		return int(est), nil
	}
	newLimit = math.Max(1, math.Min(float64(v.MaxLimit), newLimit))
	newLimit = (1-v.Smoothing)*est + v.Smoothing*newLimit
	v.EstimatedLimit = newLimit
	return int(newLimit), nil
}
