// Package main 复刻 pyperf 的 warmup 校准判据，并与 JMH 的固定暖机次数对照。
// 口径来源：psf/pyperf 的 pyperf/_worker.py、_utils.py、_runner.py 与
// openjdk/jmh 的 Defaults.java、annotations/Warmup.java。
package main

import (
	"fmt"
	"math"
	"sort"
)

const (
	maxWarmupValues  = 300
	warmupSampleSize = 20

	pyperfDefaultWarmups   = 1
	pyperfDebugSingleValue = 0
)

// JMH Defaults.java 的常量。
const (
	warmupIterations         = 5
	warmupIterationsSingleshot = 0
	warmupBatchSize          = 1
	warmupTimeS              = 10
	measurementForks         = 5
	warmupForks              = 0
	warmupMode               = "INDI"
)

// Percentile 与 pyperf 一致：线性插值，k = (len-1)*p。
func Percentile(values []float64, p float64) float64 {
	if p < 0.0 || p > 1.0 {
		panic("p must be in [0;1]")
	}
	v := make([]float64, len(values))
	copy(v, values)
	sort.Float64s(v)
	if len(v) == 0 {
		panic("no value")
	}
	k := float64(len(v)-1) * p
	f := math.Floor(k)
	c := math.Ceil(k)
	if f != c {
		return v[int(f)]*(c-k) + v[int(c)]*(k-f)
	}
	return v[int(k)]
}

func median(sorted []float64) float64 {
	n := len(sorted)
	if n == 0 {
		return 0
	}
	if n%2 == 1 {
		return sorted[n/2]
	}
	return (sorted[n/2-1] + sorted[n/2]) / 2
}

// MedianAbsDev = median(|median(values) − x|)。
func MedianAbsDev(values []float64) float64 {
	v := make([]float64, len(values))
	copy(v, values)
	sort.Float64s(v)
	m := median(v)
	dev := make([]float64, len(v))
	for i, x := range v {
		dev[i] = math.Abs(m - x)
	}
	sort.Float64s(dev)
	return median(dev)
}

func mean(values []float64) float64 {
	s := 0.0
	for _, x := range values {
		s += x
	}
	return s / float64(len(values))
}

// Diagnostics 是五条判据各自的值与失败原因。
type Diagnostics struct {
	FirstValue          float64
	Outlier             bool
	OutlierMax          float64
	Q1, Q3, IQR         float64
	Mean1, Mean2        float64
	MeanDiff            float64
	Q1Diff, Q3Diff      float64
	MAD1, MAD2, MADDiff float64
	Failed              []string
}

// WarmupDiagnostics 复刻 WorkerTask.test_calibrate_warmups 的全部计算。
func WarmupDiagnostics(warmups []float64, nwarmup int) Diagnostics {
	half := nwarmup + (len(warmups)-nwarmup)/2
	sample1 := warmups[nwarmup:half]
	sample2 := warmups[half:]
	first := sample1[0]

	// 分布里刻意不含首值本身
	values := append(append([]float64{}, sample1[1:]...), sample2...)
	q1 := Percentile(values, 0.25)
	q3 := Percentile(values, 0.75)
	iqr := q3 - q1
	outlierMax := q3 + 1.5*iqr
	outlier := !(first <= outlierMax) // 只查最大值

	mean1 := mean(sample1)
	mean2 := mean(sample2)
	meanDiff := (mean1 - mean2) / mean2

	s1q1 := Percentile(sample1, 0.25)
	s2q1 := Percentile(sample2, 0.25)
	s1q3 := Percentile(sample1, 0.75)
	s2q3 := Percentile(sample2, 0.75)
	q1Diff := (s1q1 - s2q1) / s2q1
	q3Diff := (s1q3 - s2q3) / s2q3

	mad1 := MedianAbsDev(sample1)
	mad2 := MedianAbsDev(sample2)
	madDiff := 0.0
	if mad2 != 0 {
		madDiff = (mad1 - mad2) / mad2 // 官方此处有 FIXME: 除零未处理
	}

	d := Diagnostics{first, outlier, outlierMax, q1, q3, iqr, mean1, mean2,
		meanDiff, q1Diff, q3Diff, mad1, mad2, madDiff, nil}
	if outlier {
		d.Failed = append(d.Failed, "outlier")
	}
	if !(-0.5 <= meanDiff && meanDiff <= 0.10) {
		d.Failed = append(d.Failed, "mean_diff")
	}
	if math.Abs(madDiff) > 0.10 {
		d.Failed = append(d.Failed, "mad_diff")
	}
	if math.Abs(q1Diff) > 0.05 {
		d.Failed = append(d.Failed, "q1_diff")
	}
	if math.Abs(q3Diff) > 0.05 {
		d.Failed = append(d.Failed, "q3_diff")
	}
	return d
}

// TestCalibrateWarmups 五条全过才算预热够了。
func TestCalibrateWarmups(warmups []float64, nwarmup int) bool {
	return len(WarmupDiagnostics(warmups, nwarmup).Failed) == 0
}

// CalibrateWarmups 复刻 calibrate_warmups 的递增循环。
func CalibrateWarmups(source func(start, nvalue int) []float64) (int, []float64, error) {
	warmups := []float64{}
	nwarmup := 1
	for {
		total := nwarmup + warmupSampleSize*2
		if nvalue := total - len(warmups); nvalue > 0 {
			warmups = append(warmups, source(len(warmups), nvalue)...)
		}
		if TestCalibrateWarmups(warmups, nwarmup) {
			return nwarmup, warmups, nil
		}
		if len(warmups) >= maxWarmupValues {
			return nwarmup, warmups, fmt.Errorf("failed to calibrate the number of warmups (%d values)", len(warmups))
		}
		nwarmup++
	}
}

// Lcg 是固定种子的线性同余，保证噪声可复现。
type Lcg struct{ state int64 }

func (r *Lcg) UniformPM1() float64 {
	r.state = (1103515245*r.state + 12345) % (1 << 31)
	return float64(r.state)/float64(1<<31)*2.0 - 1.0
}

// JitCurve = steady + overhead*decay^k + 确定性抖动。
func JitCurve(n int, steady, overhead, decay, noise float64) []float64 {
	rng := Lcg{20260921}
	out := make([]float64, n)
	for k := 0; k < n; k++ {
		out[k] = steady + overhead*math.Pow(decay, float64(k)) + noise*rng.UniformPM1()
	}
	return out
}

// CurveSource 把整条曲线包装成 CalibrateWarmups 需要的 source(start, nvalue)。
func CurveSource(n int, steady, overhead, decay, noise float64) func(int, int) []float64 {
	full := JitCurve(n+400, steady, overhead, decay, noise)
	return func(start, nvalue int) []float64 { return full[start : start+nvalue] }
}

// JmhWarmupPlan 复刻 @Warmup 的 BLANK_*=-1 哨兵 + Defaults 兜底。
func JmhWarmupPlan(singleShot bool) map[string]interface{} {
	iters := warmupIterations
	if singleShot {
		iters = warmupIterationsSingleshot
	}
	return map[string]interface{}{"iterations": iters, "time_s": warmupTimeS,
		"batchSize": warmupBatchSize, "warmup_forks": warmupForks, "warmup_mode": warmupMode}
}

// JmhWarmupDeviation 返回固定 N 次暖机后，最后一次相对稳态的偏差。
func JmhWarmupDeviation(curve []float64, iterations int) float64 {
	tail := curve[len(curve)-20:]
	if iterations > len(curve) {
		iterations = len(curve)
	}
	return (curve[iterations-1] - median(sortedCopy(tail))) / median(sortedCopy(tail))
}

func sortedCopy(v []float64) []float64 {
	c := make([]float64, len(v))
	copy(c, v)
	sort.Float64s(c)
	return c
}

func main() {
	n, warmups, err := CalibrateWarmups(CurveSource(400, 100, 900, 0.7, 1.0))
	if err != nil {
		fmt.Println("校准失败:", err)
		return
	}
	fmt.Printf("pyperf 校准出的 warmup 次数 = %d（共采样 %d = %d + 40）\n", n, len(warmups), n)

	curve := JitCurve(400, 100, 900, 0.7, 1.0)
	fmt.Printf("JMH 固定 %d 次: 相对稳态偏差 %+.2f%%\n",
		warmupIterations, JmhWarmupDeviation(curve, warmupIterations)*100)
	fmt.Printf("pyperf 校准的 %d 次: 相对稳态偏差 %+.2f%%\n",
		n, JmhWarmupDeviation(curve, n)*100)

	// 非对称区间：慢 11% 被拒，快 40% 被 mean 这条放行（由 q1/MAD 拦下）
	base := []float64{}
	for i := 0; i < 4; i++ {
		base = append(base, 98, 99, 100, 101, 102)
	}
	slow := make([]float64, 0, 20)
	fast := make([]float64, 0, 20)
	for _, v := range base {
		slow = append(slow, v+11)
		fast = append(fast, v*0.6)
	}
	both := append(append([]float64{}, slow...), base...)
	fmt.Printf("慢 11%%: failed=%v\n", WarmupDiagnostics(both, 0).Failed)
	both = append(append([]float64{}, fast...), base...)
	d := WarmupDiagnostics(both, 0)
	fmt.Printf("快 40%%: mean_diff=%+.2f failed=%v（mean 这条不在其中）\n", d.MeanDiff, d.Failed)
	fmt.Println("JMH 默认计划:", JmhWarmupPlan(false))
}
