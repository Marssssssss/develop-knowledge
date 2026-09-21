// Package main 建模虚拟化与容器环境（cgroup CPU quota、steal time）对 -benchtime 标定的影响。
// 口径来源：golang/go 的 src/internal/runtime/cgroup/cgroup.go、src/testing/benchmark.go、
// src/runtime/proc.go，以及 Linux Documentation/filesystems/proc.rst。
package main

import (
	"errors"
	"fmt"
	"math"
	"strings"
)

const maxBenchPredictIters = 1_000_000_000

var errMalformedFile = errors.New("malformed file")

// Version 对应 cgroup.go 的 Version。
type Version int

const (
	VersionUnknown Version = iota
	V1
	V2
)

// ParseV1Number 复刻 parseV1Number：先按 \n 截断再 ParseInt。
func ParseV1Number(buf string) (int64, error) {
	i := strings.IndexByte(buf, '\n')
	if i < 0 {
		return 0, errMalformedFile
	}
	var v int64
	if _, err := fmt.Sscanf(buf[:i], "%d", &v); err != nil {
		return 0, errMalformedFile
	}
	return v, nil
}

// ParseV2Limit 复刻 parseV2Limit：quota 可以是字面量 "max"。
func ParseV2Limit(buf string) (float64, bool, error) {
	i := strings.IndexByte(buf, ' ')
	if i < 0 {
		return 0, false, errMalformedFile
	}
	if buf[:i] == "max" {
		return 0, false, nil
	}
	periodStr := buf[i+1:]
	j := strings.IndexByte(periodStr, '\n')
	if j < 0 {
		return 0, false, errMalformedFile
	}
	var quota, period int64
	if _, err := fmt.Sscanf(buf[:i], "%d", &quota); err != nil {
		return 0, false, errMalformedFile
	}
	if _, err := fmt.Sscanf(periodStr[:j], "%d", &period); err != nil {
		return 0, false, errMalformedFile
	}
	return float64(quota) / float64(period), true, nil
}

// ContainsCPU 是按逗号切分后的精确 token 匹配。
func ContainsCPU(controllers string) bool {
	for _, c := range strings.Split(controllers, ",") {
		if c == "cpu" {
			return true
		}
	}
	return false
}

// ParseCPUCgroup 复刻 parseCPUCgroup：v1 的 CPU controller 优先于 v2。
func ParseCPUCgroup(lines []string) (string, Version, error) {
	out := ""
	for _, line := range lines {
		i := strings.IndexByte(line, ':')
		if i < 0 {
			return "", VersionUnknown, errMalformedFile
		}
		hierarchy, rest := line[:i], line[i+1:]
		j := strings.IndexByte(rest, ':')
		if j < 0 {
			return "", VersionUnknown, errMalformedFile
		}
		controllers, path := rest[:j], rest[j+1:]
		if !strings.HasPrefix(path, "/") {
			return "", VersionUnknown, errMalformedFile
		}
		if hierarchy == "0" {
			out = path
		} else if ContainsCPU(controllers) {
			return path, V1, nil
		}
	}
	if out == "" {
		return "", VersionUnknown, errMalformedFile
	}
	return out, V2, nil
}

// CfsBandwidth = period P 内最多跑 Q 的 CPU 时间，配额耗尽后 throttle 到下个 period。
type CfsBandwidth struct {
	QuotaNS, PeriodNS int64
}

// WallNS：跑完 cpuNS 的 CPU 工作所需墙钟 = (k-1)*P + (cpuNS - (k-1)*Q)，k = ceil(cpuNS/Q)。
func (c CfsBandwidth) WallNS(cpuNS int64) int64 {
	if c.QuotaNS <= 0 || cpuNS <= 0 {
		return cpuNS
	}
	k := int64(math.Ceil(float64(cpuNS) / float64(c.QuotaNS)))
	return (k-1)*c.PeriodNS + (cpuNS - (k-1)*c.QuotaNS)
}

// Ratio = cpuNS / wallNS。
func (c CfsBandwidth) Ratio(cpuNS int64) float64 {
	w := c.WallNS(cpuNS)
	if w == 0 {
		return 1.0
	}
	return float64(cpuNS) / float64(w)
}

// StealTime：/proc/stat 的 steal —— 宿主把 CPU 给了别的 guest，墙钟被乘性拉长。
type StealTime struct{ Fraction float64 }

func (s StealTime) WallNS(cpuNS int64) int64 {
	return int64(math.Round(float64(cpuNS) / (1.0 - s.Fraction)))
}

// Host 把限流与 steal 叠加。
type Host struct {
	CFS   CfsBandwidth
	Steal *StealTime
}

func (h Host) WallNS(cpuNS int64) int64 {
	w := h.CFS.WallNS(cpuNS)
	if h.Steal != nil {
		w = int64(math.Round(float64(w) / (1.0 - h.Steal.Fraction)))
	}
	return w
}

// PredictN 复刻 predictN 的四步钳制。
func PredictN(goalns, prevIters, prevns, last int64) int64 {
	if prevns == 0 {
		prevns = 1 // 上取整躲除零，go.dev/issue/70709
	}
	n := goalns * prevIters / prevns // 先乘后除
	n += n / 5                       // 1.2x
	if m := 100 * last; n > m {
		n = m
	}
	if m := last + 1; n < m {
		n = m
	}
	if n > maxBenchPredictIters {
		n = maxBenchPredictIters
	}
	return n
}

// Calibration 是 launch 标定循环的结果。
type Calibration struct {
	N, DurationNS int64
	Rounds        int
	History       [][2]int64
}

func (c Calibration) NsPerOp() int64 {
	if c.N <= 0 {
		return 0
	}
	return c.DurationNS / c.N
}

// Launch 复刻 launch：run1 之后反复 predictN + runN，判据用上一轮的墙钟。
func Launch(benchtimeNS, nsPerOpCPU int64, h Host) Calibration {
	n := int64(1)
	duration := h.WallNS(n * nsPerOpCPU)
	hist := [][2]int64{{n, duration}}
	for duration < benchtimeNS && n < maxBenchPredictIters && len(hist) < 200 {
		last := n
		n = PredictN(benchtimeNS, n, duration, last)
		duration = h.WallNS(n * nsPerOpCPU)
		hist = append(hist, [2]int64{n, duration})
	}
	return Calibration{n, duration, len(hist), hist}
}

// GomaxprocsController 复刻 sysmonUpdateGOMAXPROCS 的两道闸门。
type GomaxprocsController struct {
	Value  int32
	Custom bool
}

func (g *GomaxprocsController) SetCustom(v int32) { g.Value = v; g.Custom = true }

func (g *GomaxprocsController) SysmonUpdate(defaultFn func() int32) bool {
	if g.Custom {
		return false
	}
	procs := defaultFn()
	if procs == g.Value {
		return false
	}
	g.Value = procs
	return true
}

func main() {
	bare := Host{}
	lim := Host{CFS: CfsBandwidth{QuotaNS: 50e6, PeriodNS: 100e6}}
	both := Host{CFS: CfsBandwidth{50e6, 100e6}, Steal: &StealTime{0.10}}

	for name, h := range map[string]Host{"裸机": bare, "cgroup 50%": lim} {
		c := Launch(1e9, 1000, h)
		fmt.Printf("%-12s N=%d 墙钟=%.3fs ns/op=%d 轮数=%d\n",
			name, c.N, float64(c.DurationNS)/1e9, c.NsPerOp(), c.Rounds)
	}
	c := Launch(1e9, 1000, both)
	fmt.Printf("%-12s N=%d 墙钟=%.3fs ns/op=%d\n", "50%+steal10%", c.N,
		float64(c.DurationNS)/1e9, c.NsPerOp())
	s := Launch(10e6, 1000, lim)
	fmt.Printf("-benchtime=10ms 在 50%% 限流下: ns/op=%d（真值 1000）\n", s.NsPerOp())
	fmt.Printf("配额边界悬崖: CPU 50ms→%dms, 51ms→%dms\n",
		lim.CFS.WallNS(50e6)/1e6, lim.CFS.WallNS(51e6)/1e6)
}
