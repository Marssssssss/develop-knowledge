package main

// use_metrics.go — Stats 结构、采集层(sampleA/sampleB)与 USE 指标计算。
//
// 与 use_check.go 同属 package main,拆开只是为了让单文件落到 300 行以内;
// 判定三态、清单组装与自检在 use_check.go。
// 本文件只用算术,不需要任何标准库包,所以没有 import 块。

type Stats struct {
	Stat map[string]float64
	Load map[string]float64
	Mem  map[string]float64
	Vm   map[string]float64
	Net  map[string]map[string]float64
	Dev  map[string]map[string]float64
	Df   map[string]map[string]float64
}

func sampleA() *Stats {
	return &Stats{
		Stat: map[string]float64{"user": 100, "nice": 0, "system": 50, "idle": 800,
			"iowait": 100, "irq": 5, "softirq": 10, "steal": 35},
		Load: map[string]float64{"load1": 4.0, "running": 6, "total": 500},
		Mem:  map[string]float64{"MemTotal": 16384000, "MemAvailable": 2048000},
		Vm:   map[string]float64{"pswpin": 0, "pswpout": 0},
		Net: map[string]map[string]float64{"eth0": {"rx_bytes": 5_000_000_000,
			"tx_bytes": 1_000_000_000, "rx_drop": 0, "tx_drop": 0, "rx_fifo": 0, "tx_fifo": 0,
			"rx_errs": 0, "tx_errs": 0}},
		Dev: map[string]map[string]float64{"sda": {"busy_ms": 10000, "weighted_ms": 12000,
			"reads": 100000, "writes": 50000, "ioerr": 0}},
		Df: map[string]map[string]float64{"sda1": {"size_kb": 100_000_000, "used_kb": 91_000_000}},
	}
}

func sampleB() *Stats {
	return &Stats{
		Stat: map[string]float64{"user": 3700, "nice": 0, "system": 2200, "idle": 13900,
			"iowait": 900, "irq": 50, "softirq": 240, "steal": 35},
		Load: map[string]float64{"load1": 9.5, "running": 12, "total": 500},
		Mem:  map[string]float64{"MemTotal": 16384000, "MemAvailable": 1228800},
		Vm:   map[string]float64{"pswpin": 4096, "pswpout": 8192},
		Net: map[string]map[string]float64{"eth0": {"rx_bytes": 5_000_000_000 + 94_000_000_000,
			"tx_bytes": 1_000_000_000 + 1_000_000_000, "rx_drop": 37, "tx_drop": 0,
			"rx_fifo": 12, "tx_fifo": 0, "rx_errs": 0, "tx_errs": 0}},
		Dev: map[string]map[string]float64{"sda": {"busy_ms": 10000 + 9400,
			"weighted_ms": 12000 + 34000, "reads": 140000, "writes": 72000, "ioerr": 0}},
		Df: map[string]map[string]float64{"sda1": {"size_kb": 100_000_000, "used_kb": 99_200_000}},
	}
}

// ---------------------------------------------------------------- 指标计算

// CpuUtilization 官方:us+sy+st,即除 %idle 与 %iowait 外全部字段求和,分母为全部。
func CpuUtilization(s0, s1 *Stats) float64 {
	busy, total := 0.0, 0.0
	for k, bv := range s1.Stat {
		d := bv - s0.Stat[k]
		total += d
		if k != "idle" && k != "iowait" {
			busy += d
		}
	}
	if total == 0 {
		return 0
	}
	return 100 * busy / total
}

// CpuSaturation 官方:runq-sz > CPU 数才算饱和;返回值是「排队长度 - CPU 数」。
func CpuSaturation(s *Stats) float64 { return s.Load["running"] - CpuCount }

// MemUtilization 已用/总量(用 MemAvailable 作未用口径)。
func MemUtilization(s *Stats) float64 {
	return 100 * (s.Mem["MemTotal"] - s.Mem["MemAvailable"]) / s.Mem["MemTotal"]
}

// MemSaturation 官方:anon paging / swapping,即 si+so 增量。
func MemSaturation(s0, s1 *Stats) float64 {
	return (s1.Vm["pswpin"] - s0.Vm["pswpin"]) + (s1.Vm["pswpout"] - s0.Vm["pswpout"])
}

// NicUtilization 吞吐/最大带宽。
func NicUtilization(s0, s1 *Stats, iface string) float64 {
	a, b := s0.Net[iface], s1.Net[iface]
	bps := ((b["rx_bytes"] - a["rx_bytes"]) + (b["tx_bytes"] - a["tx_bytes"])) / IntervalS
	return 100 * bps / NicMaxBps
}

// NicDrops dropped/overruns(官方脚注 7:同时算饱和与错误)。
func NicDrops(s0, s1 *Stats, iface string) float64 {
	a, b := s0.Net[iface], s1.Net[iface]
	return (b["rx_drop"] - a["rx_drop"]) + (b["tx_drop"] - a["tx_drop"]) +
		(b["rx_fifo"] - a["rx_fifo"]) + (b["tx_fifo"] - a["tx_fifo"])
}

// DevUtilization %util = 设备忙时间 / 墙钟时间。
func DevUtilization(s0, s1 *Stats, dev string) float64 {
	a, b := s0.Dev[dev], s1.Dev[dev]
	return 100 * (b["busy_ms"] - a["busy_ms"]) / (IntervalS * 1000)
}

// DevAvgQu avgqu-sz = 加权 I/O 时间 / 墙钟时间;> 1 即说明确实排了队。
func DevAvgQu(s0, s1 *Stats, dev string) float64 {
	a, b := s0.Dev[dev], s1.Dev[dev]
	return (b["weighted_ms"] - a["weighted_ms"]) / (IntervalS * 1000)
}

// DevIOPS Δ(reads+writes)/Δt。
func DevIOPS(s0, s1 *Stats, dev string) float64 {
	a, b := s0.Dev[dev], s1.Dev[dev]
	return ((b["reads"] - a["reads"]) + (b["writes"] - a["writes"])) / IntervalS
}

// StorageCapUtilization 容量型资源用 population 口径(与 I/O 的 busy-time 口径不同)。
func StorageCapUtilization(s *Stats, fs string) float64 {
	d := s.Df[fs]
	return 100 * d["used_kb"] / d["size_kb"]
}

