// Package main 建模参数化基准（hyperfine --parameter-scan / JMH @Param）的多维比较。
// 口径来源：sharkdp/hyperfine 的 src/parameter/range_step.rs、src/benchmark/relative_speed.rs、
// src/benchmark/benchmark_result.rs，以及 openjdk/jmh 的 annotations/Param.java。
package main

import (
	"errors"
	"fmt"
	"math"
	"sort"
)

const maxParameters = 100_000

const blankArgs = "blank_blank_blank_2014"

var (
	errEmptyRange = errors.New("Empty parameter range")
	errZeroStep   = errors.New("Zero is not a valid parameter step")
	errTooLarge   = errors.New("Parameter range is too large")
)

// RangeStep 复刻 range_step.rs：闭区间、按步长累加、越过 end 就停。
type RangeStep struct {
	start, end, step int64
}

// RangeStepSizeHint 复刻官方公式 (end - start + 1) / step —— 加的是绝对值 1，不是一个 step。
func RangeStepSizeHint(start, end, step int64) int64 {
	if step == 0 {
		return -1 // (usize::MAX, None)
	}
	return (end - start + 1) / step
}

func NewRangeStep(start, end, step int64) (*RangeStep, error) {
	if end < start {
		return nil, errEmptyRange
	}
	if step == 0 {
		return nil, errZeroStep
	}
	if h := RangeStepSizeHint(start, end, step); h >= 0 && h > maxParameters {
		return nil, errTooLarge
	}
	return &RangeStep{start, end, step}, nil
}

func (r *RangeStep) Values() []int64 {
	out := []int64{}
	for s := r.start; s <= r.end; s += r.step {
		out = append(out, s)
	}
	return out
}

func (r *RangeStep) SizeHint() int64 { return RangeStepSizeHint(r.start, r.end, r.step) }

// BenchmarkResult 对应 benchmark_result.rs；Parameters 是 BTreeMap ⇒ 按 key 字典序。
type BenchmarkResult struct {
	Command    string
	Mean       float64
	Stddev     *float64
	Parameters map[string]string
}

// SortedParameterValues 返回按参数名排序后的取值序列。
func (b BenchmarkResult) SortedParameterValues() []string {
	keys := make([]string, 0, len(b.Parameters))
	for k := range b.Parameters {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := make([]string, 0, len(keys))
	for _, k := range keys {
		out = append(out, b.Parameters[k])
	}
	return out
}

// CompareMeanTime：-1/0/1；NaN 按相等处理（unwrap_or(Ordering::Equal)）。
func CompareMeanTime(l, r BenchmarkResult) int {
	if math.IsNaN(l.Mean) || math.IsNaN(r.Mean) {
		return 0
	}
	if l.Mean < r.Mean {
		return -1
	}
	if l.Mean > r.Mean {
		return 1
	}
	return 0
}

func fastestOf(results []BenchmarkResult) BenchmarkResult {
	best := results[0]
	for _, r := range results[1:] {
		if r.Mean < best.Mean {
			best = r
		}
	}
	return best
}

// WithRelativeSpeed 对应 BenchmarkResultWithRelativeSpeed。
type WithRelativeSpeed struct {
	Result                BenchmarkResult
	RelativeSpeed         float64
	RelativeSpeedStddev   *float64
	IsReference           bool
}

// ComputeRelativeSpeeds 复刻 compute_relative_speeds：比值恒 ≥ 1，标准差走误差传播。
func ComputeRelativeSpeeds(results []BenchmarkResult, reference BenchmarkResult) []WithRelativeSpeed {
	out := make([]WithRelativeSpeed, 0, len(results))
	for _, result := range results {
		isRef := result.Command == reference.Command
		ordering := CompareMeanTime(result, reference)
		if result.Mean == 0.0 {
			speed := math.Inf(1)
			if isRef {
				speed = 1.0
			}
			out = append(out, WithRelativeSpeed{result, speed, nil, isRef})
			continue
		}
		var ratio float64
		switch {
		case ordering < 0:
			ratio = reference.Mean / result.Mean
		case ordering == 0:
			ratio = 1.0
		default:
			ratio = result.Mean / reference.Mean
		}
		var rs *float64
		if result.Stddev != nil && reference.Stddev != nil {
			a := *result.Stddev / result.Mean
			b := *reference.Stddev / reference.Mean
			v := ratio * math.Sqrt(a*a+b*b)
			rs = &v
		}
		out = append(out, WithRelativeSpeed{result, ratio, rs, isRef})
	}
	return out
}

func RelativeSpeeds(results []BenchmarkResult) []WithRelativeSpeed {
	return ComputeRelativeSpeeds(results, fastestOf(results))
}

// GroupBy 按 fixed 之外的维度分组。
func GroupBy(results []BenchmarkResult, fixed []string) map[string][]BenchmarkResult {
	skip := map[string]bool{}
	for _, f := range fixed {
		skip[f] = true
	}
	groups := map[string][]BenchmarkResult{}
	for _, r := range results {
		keys := []string{}
		for k := range r.Parameters {
			if !skip[k] {
				keys = append(keys, k)
			}
		}
		sort.Strings(keys)
		key := ""
		for _, k := range keys {
			key += k + "=" + r.Parameters[k] + ";"
		}
		groups[key] = append(groups[key], r)
	}
	return groups
}

// GroupedRelativeSpeeds 在每个"其它维度都相同"的组内各自选参考点。
func GroupedRelativeSpeeds(results []BenchmarkResult, varying string) map[string][]WithRelativeSpeed {
	out := map[string][]WithRelativeSpeed{}
	for k, v := range GroupBy(results, []string{varying}) {
		out[k] = RelativeSpeeds(v)
	}
	return out
}

// ParamOuterProduct 复刻 JMH 的"When multiple @Param-s are needed ... outer product"。
func ParamOuterProduct(params map[string][]string) []map[string]string {
	keys := make([]string, 0, len(params))
	for k := range params {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	out := []map[string]string{{}}
	for _, k := range keys {
		next := []map[string]string{}
		for _, base := range out {
			for _, v := range params[k] {
				m := map[string]string{}
				for bk, bv := range base {
					m[bk] = bv
				}
				m[k] = v
				next = append(next, m)
			}
		}
		out = next
	}
	return out
}

// EnumDefaults：Enum 是唯一有隐式默认值的 @Param 类型（全部枚举常量）。
func EnumDefaults(constants []string) []string { return constants }

func main() {
	r, _ := NewRangeStep(0, 10, 3)
	fmt.Printf("0..10 step 3 -> %v, size_hint=%d (实际 %d)\n",
		r.Values(), r.SizeHint(), len(r.Values()))
	if _, err := NewRangeStep(0, 100_001, 1); err != nil {
		fmt.Println("0..100001 step 1 ->", err)
	}
	big, err := NewRangeStep(0, 300_000, 3)
	if err == nil {
		fmt.Printf("0..300000 step 3 -> size_hint=%d 但实际 %d 个值\n",
			big.SizeHint(), len(big.Values()))
	}

	sd := func(v float64) *float64 { return &v }
	results := []BenchmarkResult{
		{"t1-large", 40.0, sd(2.0), map[string]string{"threads": "1", "size": "large"}},
		{"t1-small", 10.0, sd(0.5), map[string]string{"threads": "1", "size": "small"}},
		{"t8-small", 4.0, sd(0.2), map[string]string{"threads": "8", "size": "small"}},
		{"t8-large", 16.0, sd(0.8), map[string]string{"threads": "8", "size": "large"}},
	}
	fmt.Println("全局参考:", fastestOf(results).Command)
	for _, w := range RelativeSpeeds(results) {
		fmt.Printf("  %-10s ×%.2f\n", w.Result.Command, w.RelativeSpeed)
	}
	fmt.Println("分组参考（固定 threads，只比 size）:")
	for k, items := range GroupedRelativeSpeeds(results, "size") {
		rs := []float64{}
		for _, w := range items {
			rs = append(rs, w.RelativeSpeed)
		}
		fmt.Printf("  %s -> %v\n", k, rs)
	}
	fmt.Println("JMH @Param 外积 2×3 =", len(ParamOuterProduct(
		map[string][]string{"mode": {"a", "b"}, "size": {"s", "m", "l"}})))
}
