// runqlat_check.go — 运行队列延迟(run queue latency)与调度器统计
//
// run queue latency:线程从「变为可运行」到「真的开始在某个 CPU 上运行」之间的时间。
// 本文件是 python/runqlat_check.py 的 Go 姊妹实现,覆盖同样的两条观测路径:
//  1. sched_wakeup / sched_switch 配对 -> power-of-2 微秒直方图(复刻 runqlat 输出格式)
//  2. /proc/schedstat 的 rq_cpu_time / run_delay 两次采样求差
//
// 运行: go run runqlat_check.go
package main

import (
	"fmt"
	"os"
	"strings"
)

const (
	barW     = 40 // runqlat 柱区宽度(与官方样例一致)
	cpuCount = 8
)

// ---------------------------------------------------------------- 自检

var ok = true

func check(label string, cond bool, detail ...string) {
	tag := "PASS"
	if !cond {
		tag, ok = "FAIL", false
	}
	extra := ""
	if len(detail) > 0 && detail[0] != "" {
		extra = "  <- " + detail[0]
	}
	fmt.Printf("  [%s] %s%s\n", tag, label, extra)
}

// SyntheticEvents 重放官方博文那台「重负载机」的直方图形状(计数取自该样例)。
// 快峰 1~12 us,慢峰 18~40 ms,两峰之间是真空。这是形状重放,不是真实采集数据。
func SyntheticEvents() []Event {
	plan := [][2]int{{1, 233}, {3, 742}, {7, 203}, {12, 173}, {18000, 809}, {40000, 64}}
	var ev []Event
	var ts int64
	tid := 1000
	for _, p := range plan {
		latNs := int64(p[0]) * 1000
		for n := 0; n < p[1]; n++ {
			tid++
			ev = append(ev, Event{Kind: "wakeup", TsNs: ts, Tid: tid})
			ts += latNs
			ev = append(ev, Event{Kind: "switch", TsNs: ts, Prev: 1, Next: tid})
		}
		ts += 1000 // 事件之间留缝,避免不同组共享同一时间点
	}
	return ev
}

func sameModes(a, b [][2]int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func main() {
	fmt.Println("== 1. runqlat 的桶边界是 2 的幂 ==")
	got := []int{}
	for _, v := range []float64{0, 1, 2, 3, 15, 16, 31} {
		got = append(got, BucketIndex(v))
	}
	want := []int{0, 0, 1, 1, 3, 4, 4}
	eq := len(got) == len(want)
	for i := range want {
		if got[i] != want[i] {
			eq = false
		}
	}
	check("0->0,1->0,2->1,3->1,15->3,16->4,31->4:数值落在 [2^i, 2^(i+1)) 里", eq,
		fmt.Sprint(got))
	check("标签是 '0 -> 1' / '8 -> 15' / '16384 -> 32767'(第 0 桶特例为 [0,1])",
		BucketLabel(0) == "0 -> 1" && BucketLabel(3) == "8 -> 15" &&
			BucketLabel(14) == "16384 -> 32767", BucketLabel(0)+" | "+BucketLabel(14))

	fmt.Println("== 2. 输出列宽与官方样例逐字符一致 ==")
	// 只保留承载两个峰的桶,让断言可读;官方样例的完整 16 桶见 README
	b := map[int]int{0: 233, 1: 742, 2: 203, 3: 173, 14: 809, 15: 64}
	lines := RenderHist(b, "usecs")
	head := lines[0]
	var row string
	for _, x := range lines {
		if strings.HasPrefix(x, fmt.Sprintf("%10d -> ", 0)) {
			row = x
			break
		}
	}
	check("表头 = 'usecs' 右对齐 10 列 + 15 空格 + ': count     distribution'",
		head == fmt.Sprintf("%10s%15s: count     distribution", "usecs", ""), head)
	check("数据行前 27 列 = '         0 -> 1          : '",
		len(row) >= 27 && row[:27] == "         0 -> 1          : ", row[:27])
	check("计数左对齐 9 列后再接 '|'",
		len(row) >= 36 && row[27:36] == fmt.Sprintf("%-9d", 233), row[27:36])
	allW := true
	for _, x := range lines[1:] {
		p := strings.Split(x, "|")
		if len(p) < 2 || len(p[1]) != barW {
			allW = false
		}
	}
	check("柱区总宽恒为 40", allW)
	maxStars := 0
	for _, x := range lines {
		if n := strings.Count(x, "*"); n > maxStars {
			maxStars = n
		}
	}
	check("最大桶占满 40 个 *(官方样例 809 占满)", maxStars == barW)
	check("233 相对 809 -> 233*40/809 = 11 个 *(与官方样例的 11 个一致)",
		strings.Count(row, "*") == 233*barW/809 && 233*barW/809 == 11,
		fmt.Sprint(strings.Count(row, "*")))

	fmt.Println("== 3. 双峰识别 ==")
	m := Modes(b, 0.05)
	check("识别出 2 个模式(双峰)", len(m) == 2, fmt.Sprint(m))
	check("第一峰在桶 0..3(0~15 us)", len(m) == 2 && m[0] == [2]int{0, 3}, fmt.Sprint(m))
	check("第二峰起点在桶 14(16384~32767 us,即 16~32 ms)",
		len(m) == 2 && m[1][0] == 14, fmt.Sprint(m))
	check("5% 阈值把桶 15(64/2224=2.9%)排除在峰外,第二峰收缩为 (14,14)",
		len(m) == 2 && m[1] == [2]int{14, 14}, fmt.Sprint(m))
	check("阈值降到 2% 就把整段慢峰恢复成 14..15",
		func() bool { m2 := Modes(b, 0.02); return len(m2) == 2 && m2[1] == [2]int{14, 15} }(),
		fmt.Sprint(Modes(b, 0.02)))
	fast := map[int]int{}
	for i, c := range b {
		if i <= 3 {
			fast[i] = c
		}
	}
	check("只有快路径时退化成单峰(对照机就该长这样)", len(Modes(fast, 0.05)) == 1)

	fmt.Println("== 4. wakeup / switch 配对 ==")
	buckets, paired, orphan := PairLatencies(SyntheticEvents())
	total := 0
	for _, c := range b {
		total += c
	}
	check("全部 switch 都配上了 wakeup(重放序列成对构造)", orphan == 0, fmt.Sprint(orphan))
	check("配对数 = 各桶计数之和 = 2224", paired == total && total == 2224, fmt.Sprint(paired))
	check("重放后仍是双峰", sameModes(Modes(buckets, 0.05), [][2]int{{0, 3}, {14, 14}}),
		fmt.Sprint(Modes(buckets, 0.05)))
	b2, p2c, o2c := PairLatencies(SyntheticEvents())
	same := p2c == paired && o2c == orphan && len(b2) == len(buckets)
	for i := range buckets {
		if b2[i] != buckets[i] {
			same = false
		}
	}
	check("重放是确定性的,两次结果完全一致", same)
	_, p3, o3 := PairLatencies([]Event{{Kind: "switch", TsNs: 5_000_000, Prev: 1, Next: 42}})
	check("没有 wakeup 的首次上 CPU 记成 orphan,而非制造 now-0 巨值", p3 == 0 && o3 == 1)
	_, p4, _ := PairLatencies([]Event{{Kind: "wakeup", TsNs: 1, Tid: 7}})
	check("有 wakeup 但无 switch -> 不产生样本", p4 == 0)

	fmt.Println("== 5. /proc/schedstat: 9 个字段、等待/运行比 ==")
	s0 := "version 17\ncpu0 0 0 402267 147161 236309 1062 7000000000 3000000000 255035\ndomain0 SMT ff 1 2 3\n"
	s1 := "version 17\ncpu0 0 0 402267 147161 236309 1062 7083791148 3449973971 255035\ndomain0 SMT ff 1 2 3\n"
	a, errA := ParseSchedStatCPU(s0)
	bb, errB := ParseSchedStatCPU(s1)
	if errA != nil || errB != nil {
		fmt.Println("解析失败:", errA, errB)
		os.Exit(1)
	}
	check("cpu0 行读出 9 个具名字段", a.SchedCount == 402267 && a.PCount == 255035,
		fmt.Sprintf("sched_count=%d pcount=%d", a.SchedCount, a.PCount))
	check("字段 7/8 分别是 rq_cpu_time 与 run_delay",
		a.RqCPUTime == 7_000_000_000 && a.RunDelay == 3_000_000_000)
	check("字段 2(array_exp)是 O(1) 调度器的遗留,恒为 0", a.ArrayExp == 0)
	_, errDom := ParseSchedStatCPU("version 17\ndomain0 SMT ff 1 2 3\n")
	check("只有 domain 行时明确报错,不会把 domain 当成 CPU", errDom != nil)
	ratio, okRatio := RunDelayRatio(a, bb)
	wantRatio := 100 * float64(449_973_971) / float64(83_791_148)
	check("等待/运行 = (3449973971-3000000000)/(7083791148-7000000000) = 537.02%",
		okRatio && abs(ratio-wantRatio) < 1e-9, fmt.Sprintf("%.2f%%", ratio))
	single := 100 * float64(a.RunDelay) / float64(a.RqCPUTime)
	check("计数器只增不减,必须两次采样求差:单点读值会得到完全不同的数",
		abs(single-42.86) < 0.01 && abs(ratio-42.86) > 100,
		fmt.Sprintf("单点=%.2f%% 差值法=%.2f%%", single, ratio))

	fmt.Println("== 6. sched_schedstats 开关:0 表示「没统计」而不是「没等待」 ==")
	off := "cpu0 0 0 402267 147161 236309 1062 7000000000 0 255035\n"
	off2 := "cpu0 0 0 480000 147161 236309 1062 7300000000 0 255035\n"
	d, _ := ParseSchedStatCPU(off)
	d2, _ := ParseSchedStatCPU(off2)
	check("sysctl=0 时 run_delay 恒为 0", d.RunDelay == 0 && d2.RunDelay == 0)
	check("此时 sched_count 与 rq_cpu_time 照常推进,证明文件在动、只是没统计",
		d2.SchedCount > d.SchedCount && d2.RqCPUTime > d.RqCPUTime)
	_, okD := RunDelayRatio(d, d2)
	check("两次采样 run_delay 都是 0 -> 比值判定为「未统计」,不是 0%", !okD)

	fmt.Println("== 7. /proc/<pid>/schedstat 三字段 ==")
	ps, errP := ParsePidSchedStat("1234567890 98765432 1042\n")
	check("1=CPU 上时间 2=运行队列等待时间 3=被调度次数",
		errP == nil && ps == [3]int64{1234567890, 98765432, 1042}, fmt.Sprint(ps))
	check("进程级等待时间 98765432 ns = 98.77 ms 换算正确",
		abs(float64(ps[1])/1e6-98.765432) < 1e-6)

	fmt.Println("== 8. 为什么饱和度是非线性的 ==")
	// 0.9/0.1 在 IEEE754 下是 8.999999999999998,断言必须带容差
	near := func(x, y float64) bool { return abs(x-y) < 1e-9 }
	w50, w90, w98 := MMCWait(0.5), MMCWait(0.9), MMCWait(0.98)
	check("ρ=0.5 -> 等待 1 个服务时间(Wq = ρ/(1-ρ))", near(w50, 1.0), fmt.Sprintf("%v", w50))
	check("ρ=0.9 -> 9 倍服务时间", near(w90, 9.0), fmt.Sprintf("%v", w90))
	check("ρ=0.98 -> 49 倍", near(w98, 49.0), fmt.Sprintf("%v", w98))
	check("饱和度 0.9→0.98 只涨 8.9%,等待时间却从 9 涨到 49(5.4 倍)",
		abs((0.98-0.9)/0.9-0.0889) < 0.001 && abs(w98/w90-49.0/9.0) < 1e-9)
	check("ρ→1 时等待时间发散(运行队列长 1 与长 10 完全不是一回事)",
		MMCWait(1.0) > 1e300 && MMCWait(0) == 0)

	fmt.Println("== 9. run queue latency 的定位 ==")
	_, pc1, _ := PairLatencies([]Event{{Kind: "wakeup", TsNs: 0, Tid: 7}, {Kind: "switch", TsNs: 9_000, Prev: 1, Next: 7}})
	h1, _, _ := PairLatencies([]Event{{Kind: "wakeup", TsNs: 0, Tid: 7}, {Kind: "switch", TsNs: 9_000, Prev: 1, Next: 7}})
	h2, _, _ := PairLatencies([]Event{{Kind: "wakeup", TsNs: 0, Tid: 7}, {Kind: "switch", TsNs: 90_000, Prev: 1, Next: 7}})
	check("它测的是「可运行 -> 真正在 CPU 上运行」:同一 wakeup 换个 switch 时刻,延迟就变",
		pc1 == 1 && h1[3] == 1 && h2[6] == 1, fmt.Sprintf("%v / %v", h1, h2))
	minB, maxB := 1<<30, 0
	for i := range buckets {
		if i < minB {
			minB = i
		}
		if i > maxB {
			maxB = i
		}
	}
	check("它不回答「在 CPU 上待了多久」(那是 cpudist 的视角):重放里最短 0~1us、最长 32~65ms",
		minB == 0 && maxB == 15 && cpuCount == 8)

	if ok {
		fmt.Println("\n全部通过")
	} else {
		fmt.Println("\n存在失败项")
	}
	fmt.Println("\n-- 重放序列的直方图(双峰) --")
	for _, line := range RenderHist(buckets, "usecs") {
		fmt.Println("  " + line)
	}
	if !ok {
		os.Exit(1)
	}
}

func abs(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}
