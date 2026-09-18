// Go ``testing.B`` 的迭代标定(benchtime)与样本量(-count)的镜像实现。
//
// 口径同源:``golang/go`` src/testing/benchmark.go(master)的 durationOrCountFlag.Set /
// predictN / launch / runN / RunParallel,以及 BenchmarkResult 的 NsPerOp / AllocedBytesPerOp。
//
// 与 Python 版逐函数对应,断言值一致,便于两边对拍。
package main

import (
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
	"time"
)

// maxBenchPredictIters 对应源码常量:上限 1e9(同时保证 32 位平台上 n 不溢出 int)。
const maxBenchPredictIters = 1_000_000_000

// InvalidValue 对应 Go 侧的 invalid count / invalid duration。
type InvalidValue string

func (e InvalidValue) Error() string { return string(e) }

// DurationOrCount 对应 durationOrCountFlag:d 与 n 互斥。
type DurationOrCount struct {
	d int64
	n int
}

// IsCount 对应 n > 0 的判定。
func (f DurationOrCount) IsCount() bool { return f.n > 0 }

func (f DurationOrCount) String() string {
	if f.n > 0 {
		return fmt.Sprintf("%dx", f.n)
	}
	return (time.Duration(f.d)).String()
}

// ParseBenchTime 是 durationOrCountFlag.Set 的逐行移植。
// 以 "x" 结尾 -> strconv.ParseInt;否则 -> time.ParseDuration。
// n<0 或 n==0(不允许零时)报 invalid count;d<0 或 d==0 报 invalid duration。
func ParseBenchTime(s string) (DurationOrCount, error) {
	if strings.HasSuffix(s, "x") {
		n, err := strconv.ParseInt(s[:len(s)-1], 10, 0)
		if err != nil || n < 0 || n == 0 {
			return DurationOrCount{}, InvalidValue("invalid count")
		}
		return DurationOrCount{n: int(n)}, nil
	}
	d, err := time.ParseDuration(s)
	if err != nil || d < 0 || d == 0 {
		return DurationOrCount{}, InvalidValue("invalid duration")
	}
	return DurationOrCount{d: int64(d)}, nil
}

// PredictN 是 predictN 的逐行移植:先乘后除 -> +20% -> 100*last -> last+1 -> 1e9。
func PredictN(goalns, prevIters, prevns, last int64) int64 {
	if prevns == 0 {
		prevns = 1 // 除零兜底,见 go.dev/issue/70709
	}
	n := goalns * prevIters / prevns
	n += n / 5
	if n > 100*last {
		n = 100 * last
	}
	if n < last+1 {
		n = last + 1
	}
	if n > maxBenchPredictIters {
		n = maxBenchPredictIters
	}
	return n
}

// LaunchSequence 复刻 run1 + launch,返回每轮 runN 的 N 值(含 run1 的那次 1)。
// 每轮 runN 内部先 ResetTimer,故 duration 只是本轮耗时。
func LaunchSequence(perIterNS int64, bt DurationOrCount) []int64 {
	seq := []int64{1}
	lastN := int64(1)
	duration := perIterNS * 1
	if bt.IsCount() {
		if int64(bt.n) > 1 {
			seq = append(seq, int64(bt.n))
		}
		return seq // 1x 时复用 run1,不再 runN
	}
	var n int64 = 1
	for duration < bt.d && n < maxBenchPredictIters {
		last := n
		prevIters := lastN
		prevns := duration
		n = PredictN(bt.d, prevIters, prevns, last)
		duration = perIterNS * n
		lastN = n
		seq = append(seq, n)
	}
	return seq
}

// NsPerOp 对应 BenchmarkResult.NsPerOp:整数除法,直接截断。
func NsPerOp(totalNS, n int64) int64 {
	if n <= 0 {
		return 0
	}
	return totalNS / n
}

// AllocedBytesPerOp 对应 BenchmarkResult.AllocedBytesPerOp:同样是整数除法。
func AllocedBytesPerOp(netBytes, n int64) int64 {
	if n <= 0 {
		return 0
	}
	return netBytes / n
}

// RunParallelGrain 复刻 RunParallel 里的 grain 计算:目标 ~100µs,钳制在 [1, 1e4]。
func RunParallelGrain(previousN, previousDurationNS int64) int64 {
	var grain int64
	if previousN > 0 && previousDurationNS > 0 {
		grain = 1e5 * previousN / previousDurationNS
	}
	if grain < 1 {
		grain = 1
	}
	if grain > 1e4 {
		grain = 1e4
	}
	return grain
}

// MedianCIHalfWidth 是 benchstat 风格的中位数置信区间半宽近似;n<2 时给不出分布。
func MedianCIHalfWidth(xs []float64) (float64, bool) {
	if len(xs) < 2 {
		return 0, false
	}
	s := append([]float64(nil), xs...)
	for i := 0; i < len(s); i++ {
		for j := i + 1; j < len(s); j++ {
			if s[j] < s[i] {
				s[i], s[j] = s[j], s[i]
			}
		}
	}
	m := len(s)
	z := 1.959963985
	lo := int((float64(m) - z*math.Sqrt(float64(m))) / 2)
	hi := int((float64(m)+z*math.Sqrt(float64(m)))/2) + 1
	if lo < 0 {
		lo = 0
	}
	if hi > m-1 {
		hi = m - 1
	}
	return (s[hi] - s[lo]) / 2.0, true
}

var failures int

func check(label string, cond bool, detail string) {
	if cond {
		fmt.Printf("  ok   %s\n", label)
		return
	}
	fmt.Printf("  FAIL %s -- %s\n", label, detail)
	failures++
}

func main() {
	// 1) 解析:默认 1s;Nx 与 duration 两种形态;非法值
	def, err := ParseBenchTime("1s")
	check("默认 -benchtime=1s", err == nil && def.d == int64(time.Second) && def.n == 0, fmt.Sprint(def, err))
	c1, err := ParseBenchTime("1000000x")
	check("-benchtime=1000000x -> n=1000000", err == nil && c1.n == 1000000, fmt.Sprint(c1, err))
	ms, err := ParseBenchTime("100ms")
	check("-benchtime=100ms -> 100ms", err == nil && ms.d == int64(100*time.Millisecond), fmt.Sprint(ms, err))
	for _, bad := range []string{"0x", "-1x", "0s", "-1s", "abc"} {
		_, err := ParseBenchTime(bad)
		check("-benchtime="+bad+" 被拒绝", err != nil, "应当报错")
	}

	// 2) predictN 的三处钳制
	check("predictN(1e9,1,1000,1)=100", PredictN(1e9, 1, 1000, 1) == 100, "")
	check("predictN(1e9,100,1e5,100)=1e4", PredictN(1e9, 100, 100000, 100) == 10000, "")
	check("predictN(1e9,1e4,1e7,1e4)=1e6", PredictN(1e9, 10000, 10000000, 10000) == 1000000, "")
	check("predictN 的 prevns=0 兜底", PredictN(1e9, 1, 0, 1) == 100, "")

	// 3) 1µs/op 的标定序列:100 倍增长而非 1,2,5,10
	seq := LaunchSequence(1000, def)
	check("1µs/op 序列 = [1 100 10000 1000000]",
		len(seq) == 4 && seq[0] == 1 && seq[1] == 100 && seq[2] == 10000 && seq[3] == 1000000,
		fmt.Sprint(seq))

	// 4) 1ns/op:100*last 上限迫使 6 轮
	fast := LaunchSequence(1, def)
	check("1ns/op 需 6 轮且末值 1e9", len(fast) == 6 && fast[len(fast)-1] == maxBenchPredictIters, fmt.Sprint(fast))

	// 5) Nx:1000000x 一次 runN;1x 复用 run1
	nx := LaunchSequence(1000, c1)
	check("1000000x -> [1 1000000]", len(nx) == 2 && nx[1] == 1000000, fmt.Sprint(nx))
	one := LaunchSequence(1000, DurationOrCount{n: 1})
	check("1x -> 只跑 run1", len(one) == 1 && one[0] == 1, fmt.Sprint(one))

	// 6) 样本量由 -count 决定
	_, ok1 := MedianCIHalfWidth([]float64{10.0})
	_, ok2 := MedianCIHalfWidth([]float64{10.0, 12.0})
	check("-count=1 给不出中位数置信区间", !ok1 && ok2, "")

	// 7) ns/op 与 B/op 的整数截断
	check("ns_per_op(1501,2)=750(截断)", NsPerOp(1501, 2) == 750, "")
	check("500KB/1e6op -> 0 B/op", AllocedBytesPerOp(500000, 1_000_000) == 0, "")
	check("500KB/1op -> 500000 B/op", AllocedBytesPerOp(500000, 1) == 500000, "")

	// 8) RunParallel 的 grain
	check("grain(1µs/op)=100", RunParallelGrain(1_000_000, 1_000_000_000) == 100, "")
	check("grain(1ns/op)顶到 1e4", RunParallelGrain(100_000_000, 100_000_000) == 10000, "")
	check("grain 首轮无历史=1", RunParallelGrain(0, 0) == 1, "")

	// 9) -benchtime 只改单次测量精度,且 1.2x 会超调
	ten, _ := ParseBenchTime("10s")
	s10 := LaunchSequence(1000, ten)
	check("-benchtime=10s 因 1.2x 超调到 12s", s10[len(s10)-1] == 12_000_000, fmt.Sprint(s10))
	fmt.Printf("      -benchtime=10s: N=%d, 墙钟 %.3fs\n", s10[len(s10)-1], float64(1000*s10[len(s10)-1])/1e9)

	if failures > 0 {
		fmt.Printf("\n%d 项失败\n", failures)
		os.Exit(1)
	}
	fmt.Println("\ngobench_sample(go): 全部自检通过")
}
