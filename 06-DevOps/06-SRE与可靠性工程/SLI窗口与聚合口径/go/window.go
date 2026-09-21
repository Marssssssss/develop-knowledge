package main

// demo 517 Go 侧:Prometheus 区间向量与 rate/increase 的转写。
// 转写对象是 python/prom_window.py,后者是 promql/functions.go 中
// extrapolatedRate 的逐行转写。运行:`go run .`(同包多文件必须用 `go run .`)
//
// 本机无 Go 工具链,数值以 python 侧实跑为准;Go 侧另经人工审查 +
// bracket_check / go_sanity / go_crossref 三道静态检查。

// Sample 是一个采样点,T 单位秒(源码里是毫秒,转写时统一到秒)。
type Sample struct {
	T float64
	V float64
}

// SelectRange 区间向量选择器,左开右闭 (ts-range, ts]。
// Prometheus 文档原文: "The range is a left-open and right-closed interval,
// i.e. samples with timestamps coinciding with the left boundary of the range
// are excluded from the selection, while samples coinciding with the right
// boundary of the range are included."
func SelectRange(series []Sample, ts, rangeS float64) []Sample {
	rangeStart := ts - rangeS
	out := []Sample{}
	for _, s := range series {
		if rangeStart < s.T && s.T <= ts {
			out = append(out, s)
		}
	}
	return out
}

// ExtrapolatedRate 照 extrapolatedRate 转写。返回 (值, 是否有值);
// 源码里「空序列」与「单样本且没有可用 start timestamp」都走 return nothing。
func ExtrapolatedRate(points []Sample, ts, rangeS float64, isCounter, isRate bool) (float64, bool) {
	if len(points) == 0 {
		return 0, false
	}
	rangeStart := ts - rangeS
	rangeEnd := ts
	firstT, firstV := points[0].T, points[0].V
	lastT, lastV := points[len(points)-1].T, points[len(points)-1].V
	numMinusOne := len(points) - 1

	result := lastV - firstV
	if isCounter {
		for i := 1; i < len(points); i++ {
			if points[i].V < points[i-1].V {
				result += points[i-1].V
			}
		}
	}
	if numMinusOne == 0 {
		return 0, false
	}

	durationToStart := firstT - rangeStart
	durationToEnd := rangeEnd - lastT
	sampledInterval := lastT - firstT
	averageDuration := sampledInterval / float64(numMinusOne)
	extrapolationThreshold := averageDuration * 1.1

	if durationToStart >= extrapolationThreshold {
		durationToStart = averageDuration / 2
		if isCounter {
			// 计数器不能为负:把外推起点退回到"计数器值为 0 的时刻"。
			durationToZero := durationToStart
			if result > 0 && firstV >= 0 {
				durationToZero = sampledInterval * (firstV / result)
			}
			if durationToZero < durationToStart {
				durationToStart = durationToZero
			}
		}
	}
	if durationToEnd >= extrapolationThreshold {
		durationToEnd = averageDuration / 2
	}

	factor := 1.0
	if sampledInterval != 0 {
		factor = (sampledInterval + durationToStart + durationToEnd) / sampledInterval
	}
	if isRate {
		factor /= rangeS
	}
	return result * factor, true
}

// Rate 即 rate(m[rangeS])。
func Rate(points []Sample, ts, rangeS float64) (float64, bool) {
	return ExtrapolatedRate(points, ts, rangeS, true, true)
}

// Increase 即 increase(m[rangeS])。
func Increase(points []Sample, ts, rangeS float64, isCounter bool) (float64, bool) {
	return ExtrapolatedRate(points, ts, rangeS, isCounter, false)
}

// IRate 只看最后两个点,且不做任何外推。
func IRate(points []Sample) (float64, bool) {
	if len(points) < 2 {
		return 0, false
	}
	t0, v0 := points[len(points)-2].T, points[len(points)-2].V
	t1, v1 := points[len(points)-1].T, points[len(points)-1].V
	if t1 == t0 {
		return 0, false
	}
	d := v1 - v0
	if d < 0 {
		d = v1
	}
	return d / (t1 - t0), true
}
