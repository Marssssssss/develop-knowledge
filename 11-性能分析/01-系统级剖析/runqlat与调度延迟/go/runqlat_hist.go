package main

import (
	"fmt"
	"math/bits"
	"strings"
)

// runqlat_hist.go — 直方图分桶/渲染、模式识别、wakeup-switch 配对、
// /proc/schedstat 解析、排队论近似等纯函数。
//
// 与 runqlat_check.go 同属 package main,拆开只是为了让单文件落到 300 行以内;
// Go 同包多文件是零语义变化的搬运(barW / cpuCount 等包级常量两边共用)。
// 自检与 main 在 runqlat_check.go。

// ---------------------------------------------------------------- 直方图

// BucketIndex 返回 2 的幂桶下标: 0->0, 1->0, 2->1, 3->1, 15->3, 16->4 …
func BucketIndex(usec float64) int {
	if usec < 2 {
		return 0
	}
	return bits.Len64(uint64(usec)) - 1
}

// BucketLow 第 i 桶下界。第 0 桶是 [0,1] 而不是 [1,2],这是官方输出的特例。
func BucketLow(i int) uint64 {
	if i == 0 {
		return 0
	}
	return uint64(1) << uint(i)
}

// BucketHigh 第 i 桶上界(闭区间)。
func BucketHigh(i int) uint64 { return (uint64(1)<<uint(i+1) - 1) }

// BucketLabel 形如 "0 -> 1" / "8 -> 15"。
func BucketLabel(i int) string { return fmt.Sprintf("%d -> %d", BucketLow(i), BucketHigh(i)) }

// RenderHist 完全复刻 runqlat 的输出格式 "%10s -> %-11s: %-9d|%-40s|"。
func RenderHist(buckets map[int]int, unit string) []string {
	any := false
	last, maxc := 0, 0
	for i, c := range buckets {
		if c > 0 {
			any = true
			if i > last {
				last = i
			}
			if c > maxc {
				maxc = c
			}
		}
	}
	if !any {
		return []string{fmt.Sprintf("  (%s 无样本)", unit)}
	}
	out := []string{fmt.Sprintf("%10s%15s: count     distribution", unit, "")}
	for i := 0; i <= last; i++ {
		c := buckets[i]
		bar := 0
		if c > 0 {
			bar = c * barW / maxc
		}
		out = append(out, fmt.Sprintf("%10d -> %-11d: %-9d|%-40s|",
			BucketLow(i), BucketHigh(i), c, strings.Repeat("*", bar)))
	}
	return out
}

// Modes 找出「模式」:连续非空且占比达标的桶段。双峰即两段。
func Modes(buckets map[int]int, minShare float64) [][2]int {
	total := 0
	for _, c := range buckets {
		total += c
	}
	if total == 0 {
		return nil
	}
	var hot []int
	for i, c := range buckets {
		if float64(c)/float64(total) >= minShare {
			hot = append(hot, i)
		}
	}
	// 插入排序,桶数很少
	for a := 1; a < len(hot); a++ {
		for b := a; b > 0 && hot[b] < hot[b-1]; b-- {
			hot[b], hot[b-1] = hot[b-1], hot[b]
		}
	}
	var runs [][2]int
	var cur [2]int
	hasCur := false
	for _, i := range hot {
		if hasCur && i != cur[1]+1 {
			runs = append(runs, cur)
			hasCur = false
		}
		if !hasCur {
			cur = [2]int{i, i}
			hasCur = true
		} else {
			cur[1] = i
		}
	}
	if hasCur {
		runs = append(runs, cur)
	}
	return runs
}

// ---------------------------------------------------------------- 事件配对

// Event 一条调度事件。Kind 为 "wakeup" 时用 Tid;为 "switch" 时用 Prev/Next。
type Event struct {
	Kind      string
	TsNs      int64
	Tid       int
	Prev, Next int
}

// PairLatencies 配对 wakeup 与 switch,得到等待直方图。
// 关键:switch 的 Next 才是「开始运行」的那一个;Prev 是「离开 CPU」。
// 没有对应 wakeup 的首次上 CPU 不能算成 now-0(会造出巨值),应计数并跳过。
func PairLatencies(events []Event) (map[int]int, int, int) {
	pending := map[int]int64{}
	buckets := map[int]int{}
	paired, orphan := 0, 0
	for _, e := range events {
		if e.Kind == "wakeup" {
			pending[e.Tid] = e.TsNs
			continue
		}
		if ts, ok := pending[e.Next]; ok {
			delete(pending, e.Next)
			i := BucketIndex(float64(e.TsNs-ts) / 1000.0)
			buckets[i]++
			paired++
		} else {
			orphan++
		}
	}
	return buckets, paired, orphan
}

// ---------------------------------------------------------------- /proc/schedstat

// SchedStat CPU 行的 9 个字段(对应官方 sched-stats 文档编号 1..9)。
type SchedStat struct {
	YldCount   int64
	ArrayExp   int64
	SchedCount int64
	SchedGoidle int64
	TtwuCount  int64
	TtwuLocal  int64
	RqCPUTime  int64
	RunDelay   int64
	PCount     int64
}

// ParseSchedStatCPU 解析第一条 cpu<N> 行。只有 domain 行时返回错误。
func ParseSchedStatCPU(text string) (*SchedStat, error) {
	for _, line := range strings.Split(text, "\n") {
		f := strings.Fields(line)
		if len(f) >= 10 && strings.HasPrefix(f[0], "cpu") && len(f[0]) > 3 {
			var v [9]int64
			for i := 0; i < 9; i++ {
				n, err := parseInt64(f[i+1])
				if err != nil {
					return nil, err
				}
				v[i] = n
			}
			return &SchedStat{v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7], v[8]}, nil
		}
	}
	return nil, fmt.Errorf("找不到 cpu<N> 行")
}

func parseInt64(s string) (int64, error) {
	var n int64
	neg := false
	if strings.HasPrefix(s, "-") {
		neg, s = true, s[1:]
	}
	for i := 0; i < len(s); i++ {
		if s[i] < '0' || s[i] > '9' {
			return 0, fmt.Errorf("非数字: %q", s)
		}
		n = n*10 + int64(s[i]-'0')
	}
	if neg {
		n = -n
	}
	return n, nil
}

// RunDelayRatio 官方 perf sched stats 的派生指标:等待时间 / 运行时间(%)。
// 两次采样的 RunDelay 都恒为 0 时无法区分「真没人等」与「统计没开」,
// 必须返回 ok=false 让调用方显式处理,而不是给出误导性的 0%。
func RunDelayRatio(a, b *SchedStat) (float64, bool) {
	if a.RunDelay == 0 && b.RunDelay == 0 {
		return 0, false
	}
	ran := b.RqCPUTime - a.RqCPUTime
	if ran == 0 {
		return 0, false
	}
	return 100.0 * float64(b.RunDelay-a.RunDelay) / float64(ran), true
}

// ParsePidSchedStat 三个字段: CPU 上时间(ns) / 运行队列等待时间(ns) / 被调度次数。
func ParsePidSchedStat(text string) ([3]int64, error) {
	f := strings.Fields(text)
	var out [3]int64
	if len(f) < 3 {
		return out, fmt.Errorf("字段不足: %v", f)
	}
	for i := 0; i < 3; i++ {
		n, err := parseInt64(f[i])
		if err != nil {
			return out, err
		}
		out[i] = n
	}
	return out, nil
}

// ---------------------------------------------------------------- 排队论

// MMCWait M/M/1 的平均排队等待时间(以服务时间为单位): Wq = ρ/(1-ρ)。
// ρ→1 时发散,这是「CPU 利用率 90% 与 98% 体感完全不同」的数学来源。
func MMCWait(rho float64) float64 {
	if rho <= 0 {
		return 0
	}
	if rho >= 1 {
		return inf()
	}
	return rho / (1 - rho)
}

func inf() float64 {
	v := 1e308
	return v * 10
}

