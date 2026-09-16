// cgroup_attrib.go — 容器性能归因:cgroup v2 解析 + 反向诊断的 Go 实现
//
// 命名空间限制「看得见什么」,cgroup 限制「能用多少」。
// 反向诊断(Gregg DockerCon 2017):先穷举结论,再倒推所需指标;
// CPU 侧第一步查 cpu.stat 的 throttled_usec,把「被自己硬 cap 限流」排除掉。
//
// 关键口径: cpu.stat 的 5 个 CFS 带宽字段是**非层级**的(只算自己 cap 造成的节流),
// 含祖先牵连的实际节流要看 cpu.stat.local。
//
// 只有标准库。运行: go run .
package main

import (
	"fmt"
	"strconv"
	"strings"
)

// ---------------------------------------------------------------- 解析

// ParseFlat 解析 "key value" 逐行文件。
func ParseFlat(text string) map[string]int64 {
	out := map[string]int64{}
	for _, line := range strings.Split(text, "\n") {
		f := strings.Fields(line)
		if len(f) == 2 {
			if v, err := strconv.ParseInt(f[1], 10, 64); err == nil {
				out[f[0]] = v
			}
		}
	}
	return out
}

// ParseCPUMax 解析 cpu.max = "<$MAX> <$PERIOD>";max 表示不限。返回 (CPU 上限, period, 是否不限)。
func ParseCPUMax(text string) (float64, int64, bool) {
	f := strings.Fields(text)
	period := int64(100000)
	if len(f) > 1 {
		if p, err := strconv.ParseInt(f[1], 10, 64); err == nil {
			period = p
		}
	}
	if len(f) == 0 || f[0] == "max" {
		return 0, period, true
	}
	q, _ := strconv.ParseInt(f[0], 10, 64)
	return float64(q) / float64(period), period, false
}

// ParseIOStat 解析按 "MAJ:MIN" 分行的嵌套键。
func ParseIOStat(text string) map[string]map[string]int64 {
	out := map[string]map[string]int64{}
	for _, line := range strings.Split(text, "\n") {
		f := strings.Fields(line)
		if len(f) < 2 || !strings.Contains(f[0], ":") {
			continue
		}
		m := map[string]int64{}
		for _, kv := range f[1:] {
			if i := strings.Index(kv, "="); i > 0 {
				v, _ := strconv.ParseInt(kv[i+1:], 10, 64)
				m[kv[:i]] = v
			}
		}
		out[f[0]] = m
	}
	return out
}

// ParseNested 解析 "avg10=0.60 avg60=1.20 total=987654" 这类空格分隔的 k=v。
func ParseNested(line string) map[string]float64 {
	out := map[string]float64{}
	for _, kv := range strings.Fields(line) {
		if i := strings.Index(kv, "="); i > 0 {
			v, err := strconv.ParseFloat(kv[i+1:], 64)
			if err == nil {
				out[kv[:i]] = v
			}
		}
	}
	return out
}

// ---------------------------------------------------------------- 反向诊断

// 结论枚举
const (
	CapThrottled      = "被自己的 cpu.max 硬限流"
	AncestorThrottled = "被祖先 cgroup 的带宽限制牵连"
	HostContended     = "宿主/系统级争用(不是容器的配额问题)"
	AppQueued         = "容器在等下游(应用自身排队,不是 CPU 不够)"
	CPUOk             = "CPU 侧未见瓶颈"
)

// DiagnoseCPU 按排除法顺序给出结论与依据。
func DiagnoseCPU(cpuStat, cpuLocal map[string]int64, pressureSome float64, usageDelta int64) (string, string) {
	own := cpuStat["throttled_usec"]
	if own > 0 && usageDelta > 0 && float64(own)/float64(usageDelta) > 0.05 {
		return CapThrottled, fmt.Sprintf("throttled_usec/usage_usec = %.1f%%",
			100*float64(own)/float64(usageDelta))
	}
	if cpuLocal != nil && cpuLocal["throttled_usec"] > 0 && own == 0 {
		return AncestorThrottled, fmt.Sprintf(
			"cpu.stat.throttled_usec=0 但 cpu.stat.local.throttled_usec=%d", cpuLocal["throttled_usec"])
	}
	if pressureSome > 10.0 {
		return HostContended, fmt.Sprintf("cpu.pressure some avg10 = %.1f%%", pressureSome)
	}
	if pressureSome <= 1.0 {
		return AppQueued, "cpu.pressure some 几乎为 0;应转向 off-CPU / 下游依赖分析"
	}
	return CPUOk, "无明显信号"
}

// SharesLimits 官方两公式:上限用「总忙份额」(可抢空闲 = bursting),保底用「总分配份额」。
func SharesLimits(shares, totalAllocated, totalBusy int64) (float64, float64) {
	hi, lo := 0.0, 0.0
	if totalBusy > 0 {
		hi = 100 * float64(shares) / float64(totalBusy)
	}
	if totalAllocated > 0 {
		lo = 100 * float64(shares) / float64(totalAllocated)
	}
	return hi, lo
}

// DiagItem 一条内存侧归因结论。
type DiagItem struct{ Kind, Why string }

// DiagnoseMemory 区分 high 节流 / 触 max / OOM,并区分层级与本地。
func DiagnoseMemory(ev, evLocal map[string]int64, current, max, high int64) []DiagItem {
	var out []DiagItem
	if ev["oom_kill"] > 0 {
		out = append(out, DiagItem{"OOM_KILLED", fmt.Sprintf(
			"memory.events.oom_kill = %d(local %d)", ev["oom_kill"], evLocal["oom_kill"])})
	}
	if ev["max"] > evLocal["max"] {
		out = append(out, DiagItem{"CGROUP_TREE_HIT_MAX", fmt.Sprintf(
			"max=%d 而 local=%d -> 顶到上限的是后代 cgroup", ev["max"], evLocal["max"])})
	}
	if current > high {
		out = append(out, DiagItem{"HIGH_THROTTLED", fmt.Sprintf(
			"memory.current %d > memory.high %d -> 节流进入直接回收,但永不触发 OOM", current, high)})
	}
	if max > 0 && float64(current)/float64(max) > 0.9 {
		out = append(out, DiagItem{"NEAR_MAX", fmt.Sprintf("current/max = %.1f%%",
			100*float64(current)/float64(max))})
	}
	return out
}

// ---------------------------------------------------------------- fixture 与 main

const (
	fCPUStat = "usage_usec 8000000\nuser_usec 5000000\nsystem_usec 3000000\n" +
		"nr_periods 100\nnr_throttled 0\nthrottled_usec 0\nnr_bursts 2\nburst_usec 1500\n"
	fCPULocal = "throttled_usec 1800000\n"
	fCPUMax   = "400000 100000\n"
	fPressure = "some avg10=0.60 avg60=1.20 avg300=2.40 total=987654\n" +
		"full avg10=0.20 avg60=0.40 avg300=0.80 total=123456\n"
	fMemEvents = "low 0\nhigh 128\nmax 4\noom 2\noom_kill 1\noom_group_kill 0\n"
	fMemLocal  = "low 0\nhigh 12\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n"
	fIOStat    = "8:16 rbytes=1459200 wbytes=314773504 rios=192 wios=353 dbytes=0 dios=0\n" +
		"8:0 rbytes=90430464 wbytes=299008000 rios=8950 wios=1252 dbytes=50331648 dios=3021\n"
)

func main() {
	cs := ParseFlat(fCPUStat)
	quota, period, unlimited := ParseCPUMax(fCPUMax)
	press := ParseNested(strings.Split(fPressure, "\n")[0])
	verdict, why := DiagnoseCPU(cs, ParseFlat(fCPULocal), press["avg10"], 8_000_000)

	fmt.Println("== cpu.stat(8 字段:恒定 3 + CFS 带宽 5) ==")
	keys := []string{"usage_usec", "user_usec", "system_usec", "nr_periods",
		"nr_throttled", "throttled_usec", "nr_bursts", "burst_usec"}
	for _, k := range keys {
		fmt.Printf("  %-16s = %d\n", k, cs[k])
	}
	fmt.Printf("== cpu.max = %.1f CPU(period %d us,unlimited=%v) ==\n", quota, period, unlimited)
	fmt.Printf("== 反向诊断第 1 步: cpu.stat.throttled_usec = %d -> 不是被自己 cap 限流 ==\n",
		cs["throttled_usec"])
	fmt.Printf("   但 cpu.stat.local.throttled_usec = %d(非层级字段为 0 也可能被祖先限)\n",
		ParseFlat(fCPULocal)["throttled_usec"])
	fmt.Printf("   结论: %s  (%s)\n", verdict, why)

	fmt.Println("== 排除顺序(换个输入即可看到分支会变) ==")
	for _, tc := range []struct {
		name     string
		stat     map[string]int64
		local    map[string]int64
		some     float64
		usage    int64
	}{
		{"自己 cap 限流", map[string]int64{"throttled_usec": 2_000_000}, nil, 0.1, 8_000_000},
		{"无 throttled 但 PSI some=42%", map[string]int64{"throttled_usec": 100_000}, nil, 42.0, 8_000_000},
		{"两者皆无", map[string]int64{}, nil, 0.2, 8_000_000},
	} {
		v, w := DiagnoseCPU(tc.stat, tc.local, tc.some, tc.usage)
		fmt.Printf("  %-28s -> %s  (%s)\n", tc.name, v, w)
	}

	fmt.Println("== CPU shares 的两条公式 ==")
	hi, lo := SharesLimits(1024, 4096, 2048)
	fmt.Printf("  shares=1024 总分配=4096 总忙=2048 -> 上限 %.0f%%(含 bursting) / 保底 %.0f%%\n", hi, lo)

	fmt.Println("== memory:层级 vs 本地 ==")
	ev, evl := ParseFlat(fMemEvents), ParseFlat(fMemLocal)
	for _, it := range DiagnoseMemory(ev, evl, 1_073_741_824, 2_147_483_648, 1_932_735_283) {
		fmt.Printf("  %-22s %s\n", it.Kind, it.Why)
	}
	fmt.Println("== io.stat(按设备分行) ==")
	for dev, m := range ParseIOStat(fIOStat) {
		fmt.Printf("  %-6s wbytes=%-12d rios=%-6d dbytes=%d\n", dev, m["wbytes"], m["rios"], m["dbytes"])
	}
	fmt.Println("== 容器内视角 ==")
	fmt.Println("  容器内 /proc/meminfo 报的是宿主 MemTotal(16384000 kB),不是自己的 memory.max")
	fmt.Println("  -> 「容器内存够不够」只能看 memory.current / memory.max")
}
