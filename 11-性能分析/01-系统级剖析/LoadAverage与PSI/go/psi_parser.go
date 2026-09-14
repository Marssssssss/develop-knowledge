// psi_parser.go — PSI(Pressure Stall Information)解析、窗口分析与 trigger 校验(Go 版,教学用)
//
// PSI 三条平均线只能看趋势;要判断"这段时间到底卡了多久",正确做法是取 total(累计停顿
// 微秒)的增量,自己按采样间隔算百分比 —— 本 demo 就这么做,并给出结论判定。
//
// 权威依据:docs.kernel.org/accounting/psi.html
//   some = 至少部分任务在该资源上停顿的时间占比
//   full = 所有非 idle 任务同时停顿的时间占比(此时 CPU 周期真的在浪费 = thrashing)
//   CPU full 在系统级未定义(5.13 起为兼容性填 0)
//   trigger 格式 "<some|full> <停顿量 us> <窗口 us>";窗口 500ms~10s;
//   非特权用户窗口须为 2 s 整数倍;同一 fd 再写会 EBUSY
//
// 运行: go run .
package main

import (
	"bufio"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
)

type Metrics struct {
	Avg10  float64
	Avg60  float64
	Avg300 float64
	Total  uint64 // 累计停顿微秒
}

type Snapshot struct {
	AtMs uint64
	Some map[string]Metrics // 资源 -> some 行
	Full map[string]Metrics // 资源 -> full 行
}

// parsePressure 解析一个 /proc/pressure/<res> 文件的内容
// 形如: some avg10=0.00 avg60=0.00 avg300=0.00 total=0
func parsePressure(text string) (Metrics, Metrics, error) {
	var some, full Metrics
	got := 0
	sc := bufio.NewScanner(strings.NewReader(text))
	for sc.Scan() {
		fields := strings.Fields(sc.Text())
		if len(fields) < 5 {
			continue
		}
		kind := fields[0]
		if kind != "some" && kind != "full" {
			continue
		}
		m := Metrics{}
		for _, f := range fields[1:] {
			kv := strings.SplitN(f, "=", 2)
			if len(kv) != 2 {
				continue
			}
			switch kv[0] {
			case "avg10", "avg60", "avg300":
				v, err := strconv.ParseFloat(kv[1], 64)
				if err != nil {
					return some, full, err
				}
				switch kv[0] {
				case "avg10":
					m.Avg10 = v
				case "avg60":
					m.Avg60 = v
				case "avg300":
					m.Avg300 = v
				}
			case "total":
				v, err := strconv.ParseUint(kv[1], 10, 64)
				if err != nil {
					return some, full, err
				}
				m.Total = v
			}
		}
		if kind == "some" {
			some, got = m, got|1
		} else {
			full, got = m, got|2
		}
	}
	if got != 3 {
		return some, full, errors.New("缺少 some 或 full 行")
	}
	return some, full, nil
}

// ValidateTrigger 复刻内核写 trigger 时的校验规则,返回 nil 表示可注册
func ValidateTrigger(kind string, thresholdUs, windowUs uint64, privileged bool) error {
	if kind != "some" && kind != "full" {
		return errors.New("kind 只能是 some 或 full")
	}
	if windowUs < 500_000 || windowUs > 10_000_000 {
		return fmt.Errorf("EINVAL: 窗口 %d us 超出 500 ms~10 s", windowUs)
	}
	if !privileged && windowUs%2_000_000 != 0 {
		return errors.New("EINVAL: 非特权用户要求窗口是 2 s 的整数倍")
	}
	if thresholdUs == 0 || thresholdUs > windowUs {
		return fmt.Errorf("EINVAL: 阈值 %d us 必须 >0 且 <窗口 %d us", thresholdUs, windowUs)
	}
	return nil
}

// WindowRate 用两次采样的 total 增量算"自定义窗口"的停顿百分比
func WindowRate(prev, cur Metrics, windowMs uint64) float64 {
	if cur.Total < prev.Total || windowMs == 0 {
		return 0
	}
	return 100.0 * float64(cur.Total-prev.Total) / float64(windowMs*1000)
}

func verdict(res string, someRate, fullRate float64) string {
	switch {
	case res == "cpu" && fullRate > 0:
		return "注意:CPU full 系统级未定义,不应出现非 0"
	case fullRate >= 20:
		return "thrashing:所有非 idle 任务同时被卡,CPU 周期在浪费 —— 立即扩容/限流"
	case fullRate > 0:
		return "存在整体停顿:部分时段全部任务被卡,需要关注回收/IO 拥塞"
	case someRate >= 30:
		return "部分任务长期停顿:吞吐受影响,但仍有有用功"
	case someRate > 0:
		return "有轻微停顿,趋势可接受"
	default:
		return "无停顿"
	}
}

func main() {
	fmt.Println("=== 1) 解析内核格式(题头来自 docs.kernel.org/accounting/psi.html)===")
	someText := "some avg10=0.00 avg60=0.00 avg300=0.00 total=0"
	fullText := "full avg10=0.00 avg60=0.00 avg300=0.00 total=0"
	some, full, err := parsePressure(someText + "\n" + fullText + "\n")
	fmt.Printf("  some: avg10=%.2f avg60=%.2f avg300=%.2f total=%d\n",
		some.Avg10, some.Avg60, some.Avg300, some.Total)
	fmt.Printf("  full: avg10=%.2f avg60=%.2f avg300=%.2f total=%d  (err=%v)\n",
		full.Avg10, full.Avg60, full.Avg300, full.Total, err)

	fmt.Println("\n=== 2) trigger 校验规则(与内核 EINVAL 条件一致)===")
	cases := []struct {
		kind       string
		thr, win   uint64
		privileged bool
	}{
		{"some", 150_000, 1_000_000, true},   // 官方示例:合法
		{"full", 50_000, 1_000_000, true},    // 官方示例:合法
		{"some", 100_000, 300_000, true},     // 窗口 < 500ms:非法
		{"some", 100_000, 20_000_000, true},  // 窗口 > 10s:非法
		{"some", 100_000, 3_000_000, false},  // 非特权 + 非 2s 倍数:非法
		{"some", 100_000, 4_000_000, false},  // 非特权 + 2s 倍数:合法
		{"some", 900_000, 500_000, true},     // 阈值 > 窗口:非法
	}
	for _, c := range cases {
		err := ValidateTrigger(c.kind, c.thr, c.win, c.privileged)
		status := "OK   "
		if err != nil {
			status = "REJECT"
		}
		fmt.Printf("  %-6s thr=%-9d win=%-10d priv=%-5v -> %s %v\n",
			c.kind, c.thr, c.win, c.privileged, status, err)
	}

	fmt.Println("\n=== 3) 窗口分析:用 total 增量识别瞬时停顿尖峰 ===")
	// 模拟 6 个 500 ms 采样点,memory 出现一次 full 停顿尖峰
	series := []Snapshot{}
	base := uint64(0)
	for i := 0; i < 6; i++ {
		someTot := base + 100_000*uint64(i)               // 每 500ms 累计 100 ms 的 some 停顿
		fullTot := base + 60_000*uint64(i)*(uint64(i)/4)  // 后段出现 full 停顿
		series = append(series, Snapshot{
			AtMs: uint64(i) * 500,
			Some: map[string]Metrics{"memory": {Total: someTot,
				Avg10: float64(i), Avg60: float64(i) / 2, Avg300: float64(i) / 4}},
			Full: map[string]Metrics{"memory": {Total: fullTot}},
		})
	}
	fmt.Printf("  %-8s %-14s %-14s %s\n", "t(ms)", "some 停顿%", "full 停顿%", "判定")
	for i := 1; i < len(series); i++ {
		sr := WindowRate(series[i-1].Some["memory"], series[i].Some["memory"], 500)
		fr := WindowRate(series[i-1].Full["memory"], series[i].Full["memory"], 500)
		if i == len(series)-1 { // 最后一段刻意给一个大 full 增量,演示 thrashing 判定
			fr = 34.0
		}
		fmt.Printf("  %-8d %-14.2f %-14.2f %s\n",
			series[i].AtMs, sr, fr, verdict("memory", sr, fr))
	}
	fmt.Println("  注意:avg10/60/300 是内核按自己的窗口平滑过的;要做\"这一段时间到底卡了多久\"")
	fmt.Println("        的结论,必须用 total 的增量 —— 这才是能对齐自己采样间隔的口径。")

	fmt.Println("\n=== 4) cgroup v2 归因 ===")
	resources := []string{"cpu", "memory", "io"}
	sort.Strings(resources)
	fmt.Printf("  系统级:/proc/pressure/{cpu,memory,io}  阻塞源:%s\n", strings.Join(resources, ", "))
	fmt.Println("  容器级:<cgroup>/cpu.pressure / memory.pressure / io.pressure,格式完全相同;")
	fmt.Println("  这是 loadavg 做不到的:loadavg 只有系统级全局值,无法归因到单个容器。")
	fmt.Println("\n结论:loadavg 回答\"需求有多少\",PSI 回答\"有多少时间真的被卡住了\"。")
}
