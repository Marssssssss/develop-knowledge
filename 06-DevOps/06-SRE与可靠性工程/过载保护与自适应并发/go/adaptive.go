package main

// demo 521 Go 侧:Envoy 自适应并发与过载管理器,与 python/adaptive.py 同构。
// 数值权威来自 python 侧实跑;Go 侧经人工审查 + bracket/sanity/crossref。

import (
	"fmt"
	"math"
	"math/rand"
)

// 文档给出的 min_concurrency 缺省值("a concurrency limit of 3 by default")
const defaultMinConcurrency = 3

// 重算 minRTT 所需的连续采样窗口数("5 consecutive sampling windows")
const minRttTriggerWindows = 5

// BufferValue B = minRTT * buffer_pct / 100。
//
// 口径说明:文档给的式子是 B = minRTT * buffer_pct,又说 buffer 是 percentage,
// 故入参按"10 表示 10%"处理,公式里除以 100。按字面把 10 当 0.10 用会让 B 变成
// minRTT 的 10 倍、梯度恒 >1,控制器会把并发放大到天文数字。
func BufferValue(minRtt, bufferPct float64) float64 {
	return minRtt * bufferPct / 100.0
}

// Gradient gradient = (minRTT + B) / sampleRTT。
func Gradient(minRtt, bufferPct, sampleRtt float64) (float64, error) {
	if sampleRtt <= 0 {
		return 0, fmt.Errorf("sampleRTT must be positive")
	}
	return (minRtt + BufferValue(minRtt, bufferPct)) / sampleRtt, nil
}

// Headroom headroom = sqrt(limit),不可配置。口径:取**更新前**的 limit。
func Headroom(limit float64) float64 { return math.Sqrt(limit) }

// NextLimit limit_new = max(min_limit, gradient*limit_old + headroom)。
func NextLimit(limit, minRtt, bufferPct, sampleRtt, minLimit float64) (float64, error) {
	g, err := Gradient(minRtt, bufferPct, sampleRtt)
	if err != nil {
		return 0, err
	}
	return math.Max(minLimit, g*limit+Headroom(limit)), nil
}

// FixedPoint 稳态闭式:L = g·L + sqrt(L) → sqrt(L) = 1/(1−g) → L = 1/(1−g)²。
// 只在 g < 1 时有意义。
func FixedPoint(g float64) (float64, bool) {
	if g >= 1 {
		return 0, false
	}
	return 1.0 / ((1.0 - g) * (1.0 - g)), true
}

// Iterate 迭代若干步;sampleFn(step, limit) 返回该步的 sampleRTT。
func Iterate(limit, minRtt, bufferPct float64, sampleFn func(int, float64) float64,
	minLimit float64, steps int) ([]float64, error) {
	out := make([]float64, 0, steps)
	cur := limit
	for s := 0; s < steps; s++ {
		next, err := NextLimit(cur, minRtt, bufferPct, sampleFn(s, cur), minLimit)
		if err != nil {
			return nil, err
		}
		cur = next
		out = append(out, cur)
	}
	return out, nil
}

// MinRttController minRTT 测量窗口的触发:连续 N 个窗口处在最小并发才启动。
type MinRttController struct {
	MinConcurrency int
	TriggerWindows int
	AtMinStreak    int
}

// NewMinRttController 用文档缺省值构造。
func NewMinRttController() *MinRttController {
	return &MinRttController{MinConcurrency: defaultMinConcurrency,
		TriggerWindows: minRttTriggerWindows}
}

// Observe 每个采样窗口结束时调用;返回是否触发一次 minRTT 重算。
func (c *MinRttController) Observe(limit float64) bool {
	if limit <= float64(c.MinConcurrency) {
		c.AtMinStreak++
	} else {
		c.AtMinStreak = 0
	}
	if c.AtMinStreak >= c.TriggerWindows {
		c.AtMinStreak = 0
		return true
	}
	return false
}

// JitteredStart jitter 随机推迟 minRTT 窗口起点。
// 文档只说 "randomly delay the start" 未给分布,本实现取均匀分布并标注口径。
func JitteredStart(baseInterval, jitterPct float64, rng *rand.Rand) (float64, error) {
	if jitterPct < 0 {
		return 0, fmt.Errorf("jitter must be non-negative")
	}
	return baseInterval + rng.Float64()*(baseInterval*jitterPct/100.0), nil
}

// AllHostsAligned 统计 N 个 host 的 minRTT 窗口起点落在同一小段时间内的个数。
func AllHostsAligned(nHosts int, jitterPct float64, rng *rand.Rand, tolerance float64) (int, error) {
	starts := make([]float64, nHosts)
	lo := math.MaxFloat64
	for i := 0; i < nHosts; i++ {
		s, err := JitteredStart(60.0, jitterPct, rng)
		if err != nil {
			return 0, err
		}
		starts[i] = s
		if s < lo {
			lo = s
		}
	}
	aligned := 0
	for _, s := range starts {
		if s-lo <= tolerance*60.0 {
			aligned++
		}
	}
	return aligned, nil
}

// ThresholdTrigger threshold 型:pressure > threshold 时为 1,否则 0。
func ThresholdTrigger(pressure, threshold float64) float64 {
	if pressure > threshold {
		return 1.0
	}
	return 0.0
}

// ScaledTrigger scaled 型:三段式线性斜坡。
func ScaledTrigger(pressure, scaling, saturation float64) (float64, error) {
	if saturation <= scaling {
		return 0, fmt.Errorf("saturation must be greater than scaling")
	}
	if pressure <= scaling {
		return 0.0
	}
	if pressure >= saturation {
		return 1.0
	}
	return (pressure - scaling) / (saturation - scaling), nil
}

// MemoryPressure cgroup 内存压力 = usage/limit;未设 limit 时为 0。
func MemoryPressure(usage, limit float64) float64 {
	if limit <= 0 {
		return 0.0
	}
	return usage / limit
}
