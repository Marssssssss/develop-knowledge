// CPU 利用率口径与 IPC 归因 —— Go 侧实现（无工具链，人工审查 + 机械核查）。
//
// 口径同 Python 版，全部来自 Brendan Gregg《CPU Utilization is Wrong》(2017-05-09)：
//   %CPU 实为 non-idle time；IPC<1.0 ⇒ 内存停顿，IPC>1.0 ⇒ 指令受限；
//   4-wide 上 IPC 0.78 = 峰值 19.5%；iowait 是磁盘 I/O 不是内存停顿。
package main

import (
	"fmt"
	"strconv"
	"strings"
)

// Stat 是一次 /proc/stat 的 cpu 汇总行（jiffies）。
type Stat struct {
	User, Nice, System, Idle, Iowait, IRQ, SoftIRQ, Steal, Guest, GuestNice int64
}

// 参与配平的 8 个经典字段；guest 的归属口径本 demo 不展开。
func (s Stat) Total() int64 {
	return s.User + s.Nice + s.System + s.Idle + s.Iowait + s.IRQ + s.SoftIRQ + s.Steal
}

func field(s Stat, i int) int64 {
	switch i {
	case 0:
		return s.User
	case 1:
		return s.Nice
	case 2:
		return s.System
	case 3:
		return s.Idle
	case 4:
		return s.Iowait
	case 5:
		return s.IRQ
	case 6:
		return s.SoftIRQ
	case 7:
		return s.Steal
	}
	return 0
}

func ParseProcStat(line string) (Stat, error) {
	var s Stat
	parts := strings.Fields(line)
	if len(parts) == 0 || parts[0] != "cpu" {
		return s, fmt.Errorf("不是 cpu 汇总行: %q", line)
	}
	if len(parts) < 11 {
		return s, fmt.Errorf("字段不足 10 个: %q", line)
	}
	dst := []*int64{&s.User, &s.Nice, &s.System, &s.Idle, &s.Iowait, &s.IRQ,
		&s.SoftIRQ, &s.Steal, &s.Guest, &s.GuestNice}
	for i, p := range dst {
		v, err := strconv.ParseInt(parts[i+1], 10, 64)
		if err != nil {
			return Stat{}, fmt.Errorf("第 %d 个字段不是整数: %q", i+1, parts[i+1])
		}
		*p = v
	}
	return s, nil
}

// DeltaPct 返回两次采样间各态占比（百分比）。
func DeltaPct(prev, cur Stat) map[string]float64 {
	total := float64(cur.Total() - prev.Total())
	out := map[string]float64{}
	if total <= 0 {
		return out
	}
	names := []string{"user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal"}
	for i, n := range names {
		out[n] = float64(field(cur, i)-field(prev, i)) * 100.0 / total
	}
	return out
}

// BusyPct 是工具口径的繁忙度：100 − idle，**含 iowait**。
func BusyPct(p map[string]float64) float64 { return 100.0 - p["idle"] }

// IdleThreadPct 是真正跑 idle 线程的时间：idle + iowait。
func IdleThreadPct(p map[string]float64) float64 { return p["idle"] + p["iowait"] }

func IPC(instructions, cycles float64) float64 {
	if cycles <= 0 {
		return 0
	}
	return instructions / cycles
}

// Verdict 按作者拍的 1.0 分界线定性。
func Verdict(v float64) string {
	if v < 1.0 {
		return "memory"
	}
	return "instruction"
}

func PctOfPeak(v, width float64) float64 {
	if width <= 0 {
		return 0
	}
	return v * 100.0 / width
}

// StallSplit 把 non-idle 周期拆成退休周期与停顿周期（%INS / %STL）。
func StallSplit(cycles, instructions, width float64) (retired, stalled, pctSTL float64) {
	if cycles <= 0 || width <= 0 {
		return 0, 0, 0
	}
	retired = instructions / width
	stalled = cycles - retired
	if stalled < 0 {
		stalled = 0
		retired = cycles
	}
	return retired, stalled, stalled * 100.0 / cycles
}

// IsSpinLock：busy 高、IPC 高、但逻辑上没有前进。
func IsSpinLock(busy, v float64, progressed bool) bool {
	return busy >= 90.0 && v >= 2.0 && !progressed
}

func main() {
	// 原文 perf stat 的实测值
	const cycles = 1.433972173374e12
	const instr = 1.118336816068e12
	const taskClockMs = 641398.723351
	const elapsedMs = 10003.794539

	cur, err := ParseProcStat("cpu  100 200 300 400 50 60 70 80 9 9")
	if err != nil {
		fmt.Println("parse error:", err)
		return
	}
	p := DeltaPct(Stat{}, cur)
	fmt.Printf("busy=%.4f%% iowait=%.4f%% idle线程=%.4f%%\n",
		BusyPct(p), p["iowait"], IdleThreadPct(p))
	fmt.Printf("busy + idle线程 = %.4f （= 100 + iowait，两个口径重叠）\n",
		BusyPct(p)+IdleThreadPct(p))

	v := IPC(instr, cycles)
	fmt.Printf("IPC=%.6f ⇒ %s bound，峰值(4-wide)的 %.2f%%\n", v, Verdict(v), PctOfPeak(v, 4.0))
	_, _, stl := StallSplit(cycles, instr, 4.0)
	fmt.Printf("%%STL=%.4f%%\n", stl)
	fmt.Printf("主频=%.6f GHz，CPUs utilized=%.4f\n",
		cycles/(taskClockMs/1000.0)/1e9, taskClockMs/elapsedMs)
	fmt.Printf("自旋锁样例(100%%,IPC 3.9,零前进)=%v\n", IsSpinLock(100.0, 3.9, false))
}
