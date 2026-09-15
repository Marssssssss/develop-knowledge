// 主动基准测试(Go 版): 把多口径证据变成陷阱判定。
//
// 与 python/active_benchmarking_check.py 对应, 实现 Gregg 的 problem checklist 中
// 6 条可程序化判定的项, 外加"CoV 过高则结果不可信"的统计门禁。
package main

import (
	"fmt"
	"math"
	"sort"
)

// Evidence 一次运行中收集到的多口径证据。
type Evidence struct {
	ProcCPUUtil       float64   // 被测进程占用(核)
	OtherCPUUtil      float64   // 其它进程占用整机算力的比例
	MachineCores      int       // 机器核数
	BenchmarkThreads  int       // 基准并发线程数
	CgroupQuotaCores  float64   // cgroup cpu.max 折算的核数; <0 表示未知
	ServerCPUUtil     float64   // 服务端进程 CPU 占用比例
	ServerThroughput  float64   // 服务端吞吐
	ClientThroughput  float64   // 客户端吞吐
	LinkCapacity      float64   // 链路上限(与吞吐同单位)
	FSBytesRead       float64   // 文件系统层读字节(/proc/<pid>/io read_bytes 的 read_chars 侧)
	DiskBytesRead     float64   // 块设备层读字节(read_bytes)
	FreqSeries        []float64 // CPU 频率时间序列(MHz / GHz)
	ThroughputSeries  []float64 // 吞吐时间序列
	RepeatResults     []float64 // 同一份代码重复测量的结果
}

type Hit struct {
	Name     string
	Fired    bool
	Evidence string
}

func trend(series []float64) float64 {
	n := len(series)
	if n < 2 {
		return 0
	}
	mx, my := float64(n-1)/2, mean(series)
	num, den := 0.0, 0.0
	for i, y := range series {
		x := float64(i)
		num += (x - mx) * (y - my)
		den += (x - mx) * (x - mx)
	}
	if den == 0 || my == 0 {
		return 0
	}
	return num / den / my
}

func mean(x []float64) float64 {
	s := 0.0
	for _, v := range x {
		s += v
	}
	return s / float64(len(x))
}

func stdev(x []float64) float64 {
	if len(x) < 2 {
		return 0
	}
	m := mean(x)
	s := 0.0
	for _, v := range x {
		s += (v - m) * (v - m)
	}
	return math.Sqrt(s / float64(len(x)-1))
}

func cov(x []float64) float64 {
	if len(x) < 2 || mean(x) == 0 {
		return 0
	}
	return stdev(x) / math.Abs(mean(x))
}

// analyze 返回全部判定项与"限流因子"结论。
func analyze(ev Evidence, covLimit float64) ([]Hit, bool, string) {
	hits := []Hit{}

	// 1. 被其它系统事件/邻居扰动
	hits = append(hits, Hit{"被其它系统事件/邻居扰动",
		ev.OtherCPUUtil >= 0.30,
		fmt.Sprintf("benchmark 进程占用 %.2f 核, 其它进程占用整机算力的 %.0f%%(阈值 30%%)",
			ev.ProcCPUUtil, ev.OtherCPUUtil*100)})

	// 2. 被软件资源控制(cgroup)限流
	if ev.CgroupQuotaCores < 0 {
		hits = append(hits, Hit{"被软件资源控制(cgroup)限流", false, "无 cgroup 配额信息"})
	} else {
		hits = append(hits, Hit{"被软件资源控制(cgroup)限流",
			ev.ProcCPUUtil >= ev.CgroupQuotaCores*0.95,
			fmt.Sprintf("利用率 %.2f 核 vs 配额 %.2f 核(≥95%% 即判定被钉住)",
				ev.ProcCPUUtil, ev.CgroupQuotaCores)})
	}

	// 3. 被客户端-服务端网络限流
	hits = append(hits, Hit{"被客户端-服务端网络限流",
		ev.ClientThroughput >= ev.LinkCapacity*0.95 && ev.ServerCPUUtil < 0.50,
		fmt.Sprintf("客户端吞吐 %.1f vs 链路上限 %.1f(≥95%%), 服务端 CPU %.0f%%(<50%%)",
			ev.ClientThroughput, ev.LinkCapacity, ev.ServerCPUUtil*100)})

	// 4. 基准软件单线程受限
	hits = append(hits, Hit{"基准软件单线程受限",
		ev.BenchmarkThreads == 1 && ev.MachineCores > 1 && ev.ProcCPUUtil >= 0.95,
		fmt.Sprintf("基准线程数 %d, 机器 %d 核, 进程占用 %.2f 核 -> 未利用其余 %d 核",
			ev.BenchmarkThreads, ev.MachineCores, ev.ProcCPUUtil, ev.MachineCores-1)})

	// 5. 测的是磁盘 I/O 而非文件系统 I/O
	if ev.FSBytesRead <= 0 {
		hits = append(hits, Hit{"测的是磁盘 I/O 而非文件系统 I/O", false, "无文件系统读计数"})
	} else {
		ratio := (ev.FSBytesRead - ev.DiskBytesRead) / ev.FSBytesRead
		hits = append(hits, Hit{"测的是磁盘 I/O 而非文件系统 I/O", ratio >= 0.50,
			fmt.Sprintf("文件系统读 %.3g B, 磁盘读 %.3g B -> %.0f%% 由 page cache 满足(阈值 50%%)",
				ev.FSBytesRead, ev.DiskBytesRead, ratio*100)})
	}

	// 6. 热降频
	if len(ev.FreqSeries) < 3 || len(ev.ThroughputSeries) < 3 {
		hits = append(hits, Hit{"热降频", false, "缺少频率/吞吐时间序列"})
	} else {
		ft, tt := trend(ev.FreqSeries), trend(ev.ThroughputSeries)
		hits = append(hits, Hit{"热降频", ft < -0.02 && tt < -0.02,
			fmt.Sprintf("频率漂移 %+.2f%%/样本, 吞吐漂移 %+.2f%%/样本(同为负即判定)",
				ft*100, tt*100)})
	}

	// 7. 统计口径不可信
	trusted := true
	covText := "缺少重复测量"
	if len(ev.RepeatResults) >= 3 {
		c := cov(ev.RepeatResults)
		trusted = c <= covLimit
		covText = fmt.Sprintf("重复测量 CoV=%.2f%%(阈值 %.0f%%)", c*100, covLimit*100)
	}

	names := []string{}
	for _, h := range hits {
		if h.Fired {
			names = append(names, h.Name)
		}
	}
	verdict := ""
	switch {
	case !trusted:
		verdict = fmt.Sprintf("结果不可信: %s —— 先降噪, 不要下性能结论", covText)
	case len(names) > 0:
		verdict = "限流因子: "
		for i, n := range names {
			if i > 0 {
				verdict += " + "
			}
			verdict += n
		}
	default:
		verdict = "未见明显陷阱: 结果可用于解释'为什么是 X'"
	}
	return hits, trusted, verdict
}

// ------------------------------------------------------------------- 自检

func cleanEvidence() Evidence {
	return Evidence{
		ProcCPUUtil: 3.8, OtherCPUUtil: 0.05, MachineCores: 8, BenchmarkThreads: 4,
		CgroupQuotaCores: -1, ServerCPUUtil: 0.72, ServerThroughput: 900,
		ClientThroughput: 900, LinkCapacity: 10000,
		FSBytesRead: 1e9, DiskBytesRead: 0.9e9,
		FreqSeries: []float64{3.4, 3.39, 3.41, 3.40},
		ThroughputSeries: []float64{900, 901, 899, 900},
		RepeatResults:    []float64{900, 902, 899, 901, 900},
	}
}

func firedNames(hits []Hit) []string {
	out := []string{}
	for _, h := range hits {
		if h.Fired {
			out = append(out, h.Name)
		}
	}
	sort.Strings(out)
	return out
}

func contains(list []string, want string) bool {
	for _, v := range list {
		if v == want {
			return true
		}
	}
	return false
}

func main() {
	clean := cleanEvidence()
	hits, trusted, verdict := analyze(clean, 0.02)
	if !trusted || len(firedNames(hits)) != 0 {
		panic("clean evidence should fire nothing")
	}
	fmt.Printf("[干净] %s\n", verdict)

	type tc struct {
		tag  string
		ev   Evidence
		want string
	}
	cases := []tc{}
	ev := clean
	ev.OtherCPUUtil = 0.55
	cases = append(cases, tc{"扰动", ev, "被其它系统事件/邻居扰动"})
	ev = clean
	ev.ProcCPUUtil, ev.CgroupQuotaCores = 2.0, 2.0
	cases = append(cases, tc{"配额", ev, "被软件资源控制(cgroup)限流"})
	ev = clean
	ev.ClientThroughput, ev.ServerThroughput, ev.ServerCPUUtil = 9990, 9990, 0.20
	cases = append(cases, tc{"网络", ev, "被客户端-服务端网络限流"})
	ev = clean
	ev.BenchmarkThreads, ev.ProcCPUUtil = 1, 0.99
	cases = append(cases, tc{"单线程", ev, "基准软件单线程受限"})
	ev = clean
	ev.DiskBytesRead = 1e6
	cases = append(cases, tc{"测错目标", ev, "测的是磁盘 I/O 而非文件系统 I/O"})
	ev = clean
	ev.FreqSeries = []float64{3.6, 3.4, 3.2, 2.9}
	ev.ThroughputSeries = []float64{980, 930, 880, 830}
	cases = append(cases, tc{"热降频", ev, "热降频"})

	for _, c := range cases {
		h, ok, _ := analyze(c.ev, 0.02)
		names := firedNames(h)
		if !contains(names, c.want) || !ok {
			panic(fmt.Sprintf("%s: fired=%v want=%s", c.tag, names, c.want))
		}
		for _, hit := range h {
			if hit.Name == c.want {
				fmt.Printf("[%s] 命中 %d 项: %s\n    证据 %s\n", c.tag, len(names), c.want, hit.Evidence)
			}
		}
	}

	// CoV 门禁
	jumpy := cleanEvidence()
	jumpy.RepeatResults = []float64{900, 1040, 810, 990, 860}
	_, trustedJump, verdictJump := analyze(jumpy, 0.02)
	if trustedJump {
		panic("jumpy evidence should be untrusted")
	}
	fmt.Printf("[抖动] %s\n", verdictJump)

	// 多陷阱叠加: 限流因子要全部列出
	multi := cleanEvidence()
	multi.OtherCPUUtil, multi.BenchmarkThreads, multi.ProcCPUUtil = 0.6, 1, 0.99
	if len(firedNames(mustAnalyze(multi))) < 2 {
		panic("multi-hit should list all fired checks")
	}
	_, _, verdictMulti := analyze(multi, 0.02)
	fmt.Printf("[叠加] %s\n", verdictMulti)

	// 统计可信 != 结论正确
	sig := cleanEvidence()
	sig.RepeatResults = []float64{1000, 1000, 1000, 1000, 1000}
	sig.ProcCPUUtil, sig.BenchmarkThreads = 0.98, 1
	_, trustedSig, verdictSig := analyze(sig, 0.02)
	if !trustedSig {
		panic("zero-variance evidence should be statistically trusted")
	}
	fmt.Printf("[组合] %s\n     -> 统计可信 != 结论正确: 这正是被动基准测试的陷阱\n", verdictSig)

	fmt.Println("\nactive_benchmarking_check: 全部自检通过")
}

func mustAnalyze(ev Evidence) []Hit {
	hits, _, _ := analyze(ev, 0.02)
	return hits
}
