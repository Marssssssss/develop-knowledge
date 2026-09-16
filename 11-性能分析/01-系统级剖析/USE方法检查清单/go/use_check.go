// use_check.go — USE 方法检查清单的 Go 实现
//
// 对每一种资源检查 Utilization / Saturation / Errors;拿不到指标的格子显式记 "?" 并算覆盖率
// (把 unknown-unknowns 变成 known-unknowns 才是这份清单的价值)。
//
// 采集层用「命名计数器 + 两次采样求差」建模,不绑定 /proc 的列序,便于换到真机。
// 口径与 python/ 版一致。运行: go run .
package main

import (
	"fmt"
	"sort"
)

const (
	UtilCaution = 70.0  // 官方:长窗口的 70% 会掩盖短时 100% 峰值,故 70% 起提示
	UtilCrit    = 100.0 // 官方:100% 利用率即瓶颈信号
	CpuCount    = 8
	DevMaxIOPS  = 300.0
	NicMaxBps   = 12_500_000_000.0
	IntervalS   = 10.0
)

// Stats 是一次采样:命名计数器,而非 /proc 的列。
// ---------------------------------------------------------------- 判定与清单

// Verdict 三态判定;spec 取 util/sat/err。
func Verdict(spec string, v float64, has bool) string {
	if !has {
		return "?"
	}
	switch spec {
	case "util":
		if v >= UtilCrit {
			return "BOTTLENECK"
		}
		if v >= UtilCaution {
			return "CAUTION"
		}
		return "OK"
	case "sat":
		// 调用方负责把「阈值内的值」归零(如 avgqu-sz <= 1 视为未排队)
		if v == 0 {
			return "OK"
		}
		return "QUEUED"
	default:
		if v == 0 {
			return "OK"
		}
		return "ERRORS"
	}
}

// Cell 是检查清单的一格。
type Cell struct {
	Resource, Kind, Metric, Source string
	Value                          float64
	Has                            bool
	Verdict                        string
}

// PhysicalResources 官方 physical resources 清单,顺序照抄 use-linux.html。
// 硬件缓存被有意省略:缓存在高利用率下反而改善性能,不适用 USE。
var PhysicalResources = []string{"CPU", "Memory capacity", "Network Interfaces",
	"Network controller", "Storage device I/O", "Storage capacity", "Storage controller",
	"CPU interconnect", "Memory interconnect", "I/O interconnect"}

// SoftwareResources 官方 software resources 清单。
var SoftwareResources = []string{"Kernel mutex", "User mutex", "Task capacity", "File descriptors"}

// Checklist 是整张矩阵。
type Checklist struct{ Cells []Cell }

// Add 追加一格;has=false 表示该指标当前拿不到(记 "?")。
func (c *Checklist) Add(res, kind, metric, source, spec string, v float64, has bool) *Checklist {
	c.Cells = append(c.Cells, Cell{res, kind, metric, source, v, has, Verdict(spec, v, has)})
	return c
}

// Coverage 覆盖率 = 已填格子 / 总格子。
func (c *Checklist) Coverage() float64 {
	got := 0
	for _, x := range c.Cells {
		if x.Has {
			got++
		}
	}
	return 100 * float64(got) / float64(len(c.Cells))
}

// Build 按官方 checklist 建矩阵。
func Build(s0, s1 *Stats) *Checklist {
	c := &Checklist{}
	h := func(v float64) (float64, bool) { return v, true }
	miss := func() (float64, bool) { return 0, false }
	add := func(res, kind, metric, source, spec string, v float64, ok bool) {
		c.Add(res, kind, metric, source, spec, v, ok)
	}

	v, ok := h(CpuUtilization(s0, s1))
	add("CPU", "utilization", "us+sy+st(除 idle/iowait)", "vmstat 1 / sar -u", "util", v, ok)
	v, ok = h(CpuSaturation(s1))
	if v < 0 { // 排队长度不足 CPU 数 -> 未饱和,按 0 判定
		v = 0
	}
	add("CPU", "saturation", "runq-sz > CPU 数", "vmstat 1 'r' / sar -q", "sat", v, ok)
	v, ok = miss()
	add("CPU", "errors", "处理器 ECC 等", "perf(LPE)", "err", v, ok)

	v, ok = h(MemUtilization(s1))
	add("Memory capacity", "utilization", "(MemTotal-MemAvailable)/MemTotal",
		"free -m / sar -r", "util", v, ok)
	v, ok = h(MemSaturation(s0, s1))
	add("Memory capacity", "saturation", "si+so(换页)", "vmstat 1 'si'/'so'", "sat", v, ok)
	v, ok = miss()
	add("Memory capacity", "errors", "dmesg 物理故障", "dmesg | grep killed", "err", v, ok)

	v, ok = h(NicUtilization(s0, s1, "eth0"))
	add("Network Interfaces", "utilization", "吞吐/最大带宽", "sar -n DEV 1", "util", v, ok)
	v, ok = h(NicDrops(s0, s1, "eth0"))
	add("Network Interfaces", "saturation", "dropped / overruns", "ifconfig / netstat -s", "sat", v, ok)
	v, ok = h(s1.Net["eth0"]["rx_errs"] - s0.Net["eth0"]["rx_errs"])
	add("Network Interfaces", "errors", "errors", "ip -s link / /proc/net/dev", "err", v, ok)
	for _, k := range []string{"utilization", "saturation", "errors"} {
		v, ok = miss()
		add("Network controller", k, "同接口层", "ip -s link", map[string]string{
			"utilization": "util", "saturation": "sat", "errors": "err"}[k], v, ok)
	}

	v, ok = h(DevUtilization(s0, s1, "sda"))
	add("Storage device I/O", "utilization", "%util(忙时间占比)", "iostat -xz 1", "util", v, ok)
	q, _ := DevAvgQu(s0, s1, "sda")
	if q <= 1 { // 官方判据是 avgqu-sz > 1 才算排队
		q = 0
	}
	add("Storage device I/O", "saturation", "avgqu-sz > 1", "iostat -xnz 1", "sat", q, true)
	v, ok = h(s1.Dev["sda"]["ioerr"])
	add("Storage device I/O", "errors", "设备错误", "/sys/.../ioerr_cnt, smartctl", "err", v, ok)
	v, ok = h(StorageCapUtilization(s1, "sda1"))
	add("Storage capacity", "utilization", "已用/容量", "df -h / swapon -s", "util", v, ok)
	v, ok = miss()
	add("Storage capacity", "saturation", "满即 ENOSPC(无排队语义)", "—", "sat", v, ok)
	v, ok = miss()
	add("Storage capacity", "errors", "ENOSPC", "strace / 日志", "err", v, ok)
	v, ok = h(100 * DevIOPS(s0, s1, "sda") / DevMaxIOPS)
	add("Storage controller", "utilization", "各设备相加 vs 单卡上限", "iostat -xz 1 求和", "util", v, ok)
	for _, k := range []string{"saturation", "errors"} {
		v, ok = miss()
		add("Storage controller", k, "见存储设备", "—", "sat", v, ok)
	}
	for _, res := range []string{"CPU interconnect", "Memory interconnect", "I/O interconnect"} {
		for _, k := range []string{"utilization", "saturation", "errors"} {
			v, ok = miss()
			add(res, k, "每端口吞吐/最大带宽 / stall cycles", "perf(LPE/CPC)", "util", v, ok)
		}
	}
	v, ok = miss()
	add("Kernel mutex", "utilization", "holdtime-total/acquisitions", "/proc/lock_stat", "util", v, ok)
	v, ok = miss()
	add("Kernel mutex", "saturation", "waittime-total/contentions", "/proc/lock_stat", "sat", v, ok)
	v, ok = h(100 * 500 / 63000)
	add("Task capacity", "utilization", "当前任务数/threads-max", "top 'Tasks'", "util", v, ok)
	v, ok = miss()
	add("Task capacity", "errors", "can't fork()", "pthread_create 失败", "err", v, ok)
	v, ok = h(100 * 100000 / 1000000)
	add("File descriptors", "utilization", "已用/上限", "/proc/sys/fs/file-nr", "util", v, ok)
	v, ok = miss()
	add("File descriptors", "errors", "EMFILE", "strace", "err", v, ok)
	return c
}

func main() {
	s0, s1 := sampleA(), sampleB()
	cl := Build(s0, s1)

	fmt.Println("== 官方资源清单 ==")
	fmt.Printf("  物理(%d): %v\n", len(PhysicalResources), PhysicalResources)
	fmt.Printf("  软件(%d): %v\n", len(SoftwareResources), SoftwareResources)
	fmt.Println("  注意:硬件缓存被有意排除 —— 缓存在高利用率下反而改善性能,不适用 USE")

	fmt.Println("== 指标矩阵(资源 × 类型) ==")
	fmt.Printf("  %-24s%-12s%15s  %-11s%s\n", "资源", "类型", "值", "判定", "来源")
	for _, c := range cl.Cells {
		val := "   n/a"
		if c.Has {
			val = fmt.Sprintf("%14.3f", c.Value)
		}
		fmt.Printf("  %-24s%-12s%15s  %-11s%s\n", c.Resource, c.Kind, val, c.Verdict, c.Source)
	}

	missed := map[string]bool{}
	for _, c := range cl.Cells {
		if !c.Has {
			missed[c.Resource] = true
		}
	}
	keys := make([]string, 0, len(missed))
	for k := range missed {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	fmt.Printf("== 覆盖率 = %.1f%%(%d/%d);'?' 格子分布在 %d 个资源上 ==\n",
		cl.Coverage(), countHas(cl), len(cl.Cells), len(keys))
	fmt.Printf("  未测资源: %v\n", keys)
	fmt.Printf("== 关键口径 == CPU 忙 = %v %% (排除 idle 与 iowait);iowait 若计入则为 %v %%\n",
		round3(CpuUtilization(s0, s1)), round3(CpuUtilizationWithIowait(s0, s1)))
	fmt.Printf("  存储双身份: I/O %%util = %v %% ,容量 population = %v %% —— 同一设备两种口径\n",
		round3(DevUtilization(s0, s1, "sda")), round3(StorageCapUtilization(s1, "sda1")))
}

// CpuUtilizationWithIowait 故意把 iowait 算进忙时间,用来对照口径差异。
func CpuUtilizationWithIowait(s0, s1 *Stats) float64 {
	busy, total := 0.0, 0.0
	for k, bv := range s1.Stat {
		d := bv - s0.Stat[k]
		total += d
		if k != "idle" {
			busy += d
		}
	}
	return 100 * busy / total
}

func countHas(c *Checklist) int {
	n := 0
	for _, x := range c.Cells {
		if x.Has {
			n++
		}
	}
	return n
}

func round3(f float64) float64 { return float64(int(f*1000+0.5)) / 1000 }
