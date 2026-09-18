package main

// 范围聚合：把日志范围向量折叠成指标样本。
//
// 与 Prometheus 一致的两点写死而不是「大概」：
//   * stddev / stdvar 用**总体**口径（除以 N），不是样本口径（除以 N-1）。
//   * quantile_over_time 用线性插值：rank = φ·(N-1)，floor 与 ceil 相同则直接
//     取该元素，否则在相邻两点间插值。所以
//     quantile_over_time(0.5, [10,20,30,40]) = 25，不是 20 也不是 30。
//
// 硬约束：**指标查询不允许携带错误**。只要范围聚合前还残留 __error__，
// 整条查询直接失败，而不是返回 0 或部分结果。

import (
	"fmt"
	"math"
	"sort"
)

var logRangeFuncs = map[string]bool{
	"count_over_time": true, "bytes_over_time": true, "rate": true,
	"bytes_rate": true, "absent_over_time": true,
}

var unwrapRangeFuncs = map[string]bool{
	"sum_over_time": true, "avg_over_time": true, "max_over_time": true,
	"min_over_time": true, "first_over_time": true, "last_over_time": true,
	"stdvar_over_time": true, "stddev_over_time": true, "quantile_over_time": true,
}

// PromQuantile Prometheus 口径的分位数（与 PromQL quantile() 同算法）。
func PromQuantile(phi float64, sorted []float64) float64 {
	if len(sorted) == 0 || phi < 0 || phi > 1 {
		return math.NaN()
	}
	if len(sorted) == 1 {
		return sorted[0]
	}
	rank := phi * float64(len(sorted)-1)
	lower := int(math.Floor(rank))
	upper := int(math.Ceil(rank))
	if lower == upper {
		return sorted[lower]
	}
	frac := rank - float64(lower)
	return sorted[lower]*(1-frac) + sorted[upper]*frac
}

// PopulationStdVar 总体方差（除以 N）。
func PopulationStdVar(values []float64) float64 {
	if len(values) == 0 {
		return math.NaN()
	}
	mean := 0.0
	for _, v := range values {
		mean += v
	}
	mean /= float64(len(values))
	acc := 0.0
	for _, v := range values {
		d := v - mean
		acc += d * d
	}
	return acc / float64(len(values))
}

// EvaluateMetric 执行一条范围聚合，返回「序列键 -> 值」。
//
// 执行顺序严格是「选择器 → 管线 → 窗口 → 函数」，其中管线与窗口由调用方
// 在外面完成；这里只负责函数分派与错误门禁。hasUnwrap 用来校验函数与管线
// 形态是否配套 —— count_over_time 不接受 unwrap 后的数值范围向量。
func EvaluateMetric(
	name string,
	windowS float64,
	phi float64,
	hasUnwrap bool,
	entries []*Entry,
) (map[string]float64, error) {
	if hasUnwrap && !unwrapRangeFuncs[name] {
		return nil, fmt.Errorf("%s 不接受 unwrap 之后的数值范围向量", name)
	}
	if !hasUnwrap && !logRangeFuncs[name] {
		return nil, fmt.Errorf("%s 需要 unwrap 阶段提供数值", name)
	}
	for _, e := range entries {
		if msg, ok := e.Labels[ErrorLabel]; ok {
			return nil, fmt.Errorf("metric query contains errors: %s", msg)
		}
	}
	groups := map[string][]*Entry{}
	for _, e := range entries {
		key := SeriesKey(e.Labels)
		groups[key] = append(groups[key], e)
	}
	out := make(map[string]float64, len(groups))
	for key, bucket := range groups {
		value, err := rangeValue(name, windowS, phi, bucket)
		if err != nil {
			return nil, err
		}
		out[key] = value
	}
	return out, nil
}

func rangeValue(name string, windowS, phi float64, entries []*Entry) (float64, error) {
	totalBytes := 0
	values := make([]float64, 0, len(entries))
	for _, e := range entries {
		totalBytes += len(e.Line)
		if e.HasValue {
			values = append(values, e.Value)
		}
	}
	switch name {
	case "count_over_time":
		return float64(len(entries)), nil
	case "bytes_over_time":
		return float64(totalBytes), nil
	case "rate":
		return float64(len(entries)) / windowS, nil
	case "bytes_rate":
		return float64(totalBytes) / windowS, nil
	case "absent_over_time":
		if len(entries) == 0 {
			return 1, nil
		}
		return 0, nil
	case "sum_over_time", "avg_over_time", "max_over_time", "min_over_time",
		"first_over_time", "last_over_time", "stdvar_over_time",
		"stddev_over_time", "quantile_over_time":
		if len(values) == 0 {
			return math.NaN(), nil
		}
		return unwrapValue(name, windowS, phi, values), nil
	}
	return 0, fmt.Errorf("未知范围函数: %q", name)
}

func unwrapValue(name string, windowS, phi float64, values []float64) float64 {
	switch name {
	case "sum_over_time":
		total := 0.0
		for _, v := range values {
			total += v
		}
		return total
	case "avg_over_time":
		total := 0.0
		for _, v := range values {
			total += v
		}
		return total / float64(len(values))
	case "max_over_time":
		best := values[0]
		for _, v := range values {
			if v > best {
				best = v
			}
		}
		return best
	case "min_over_time":
		best := values[0]
		for _, v := range values {
			if v < best {
				best = v
			}
		}
		return best
	case "first_over_time":
		return values[0]
	case "last_over_time":
		return values[len(values)-1]
	case "stdvar_over_time":
		return PopulationStdVar(values)
	case "stddev_over_time":
		return math.Sqrt(PopulationStdVar(values))
	}
	// quantile_over_time：[H] 后面是注释，不能直接塞在 switch 的 default 上，
	// 所以这里用显式判断收尾。
	if name == "quantile_over_time" {
		sorted := append([]float64(nil), values...)
		sort.Float64s(sorted)
		return PromQuantile(phi, sorted)
	}
	_ = windowS
	return math.NaN()
}
