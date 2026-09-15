// JMH `-prof perfnorm` 的归一化流水线复现(Go 版)。
//
// 与 python/perfnorm_report.py 一一对应, 逐步复现 LinuxPerfNormProfiler 的细节:
//  1. 事件探测(不支持的静默丢弃)
//  2. 增量解析(3 列/perf 3.13 与 >=4 列/perf >3.13 两种格式)
//  3. 时间窗裁剪 readFrom/readTo
//  4. 头尾过滤(丢最后 2 个样本)
//  5. 求和时跳过第 1 个样本
//  6. 归一化: 事件吞吐 / 操作吞吐(1000*ops/timeMs)
//  7. CPI/IPC 派生(cycles:u / instructions:u 回退)
//
// 另附归因判定与"近似零"记法(阈值取 1e-3, 与 JMH 示例输出中的 ≈ 10⁻⁴ 观察一致)。
package main

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
)

// interestingEvents: 源码里那张"非穷举但我们关心"的事件表(顺序即源码顺序)。
var interestingEvents = []string{
	"cycles", "instructions",
	"branches", "branch-misses",
	"L1-dcache-loads", "L1-dcache-load-misses",
	"L1-dcache-stores", "L1-dcache-store-misses",
	"L1-icache-loads", "L1-icache-load-misses",
	"LLC-loads", "LLC-load-misses",
	"LLC-stores", "LLC-store-misses",
	"dTLB-loads", "dTLB-load-misses",
	"dTLB-stores", "dTLB-store-misses",
	"iTLB-loads", "iTLB-load-misses",
	"stalled-cycles-frontend", "stalled-cycles-backend",
}

const approxZeroThreshold = 1e-3

type record struct {
	timeSec float64
	event   string
	count   float64
}

// probeSupported 模拟 `perf stat --event X echo 1` 探测: 不支持的候选事件被丢弃。
func probeSupported(candidates []string, available map[string]bool) []string {
	out := []string{}
	for _, ev := range candidates {
		if available[ev] {
			out = append(out, ev)
		}
	}
	return out
}

// parseIncremental 解析增量输出; 无法解析的行忽略。
func parseIncremental(lines []string) []record {
	out := []record{}
	for _, line := range lines {
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.Split(line, ",")
		var tTok, cTok, ev string
		switch {
		case len(parts) == 3: // perf 3.13: time,count,event
			tTok, cTok, ev = parts[0], parts[1], parts[2]
		case len(parts) >= 4: // perf >3.13: time,count,<other>,event,<others>
			tTok, cTok, ev = parts[0], parts[1], parts[3]
		default:
			continue
		}
		ts, err1 := strconv.ParseFloat(strings.TrimSpace(tTok), 64)
		cnt, err2 := strconv.ParseFloat(strings.TrimSpace(cTok), 64)
		if err1 != nil || err2 != nil {
			continue
		}
		out = append(out, record{ts, strings.TrimSpace(ev), cnt})
	}
	return out
}

// normalize 把原始增量计数换算成"每操作计数"。
func normalize(records []record, delayMs, lengthMs, intervalMs, ops, timeMs int,
	doFilter, skipFirst bool) map[string]float64 {
	res := map[string]float64{}
	if ops <= 0 || timeMs <= 0 {
		return res
	}
	readFrom := float64(delayMs) / 1000
	readTo := float64(delayMs+lengthMs+intervalMs) / 1000

	byEvent := map[string][]record{}
	for _, r := range records {
		if r.timeSec < readFrom || r.timeSec > readTo { // 3. 窗外丢弃
			continue
		}
		byEvent[r.event] = append(byEvent[r.event], r)
	}

	throughput := map[string]float64{}
	for ev, series := range byEvent {
		sort.Slice(series, func(i, j int) bool { return series[i].timeSec < series[j].timeSec })
		kept := series
		if doFilter && len(series)-2 > 0 { // 4. 丢弃最后 2 个样本
			kept = series[:len(series)-2]
		}
		sum := 0.0
		minT, maxT := math.Inf(1), math.Inf(-1)
		for i, r := range kept {
			if i != 0 || !skipFirst { // 5. 第 1 个样本的时间区间不含它自己
				sum += r.count
			}
			minT = math.Min(minT, r.timeSec)
			maxT = math.Max(maxT, r.timeSec)
		}
		if maxT <= minT {
			continue
		}
		throughput[ev] = sum / (maxT - minT)
	}

	opsThroughput := 1000 * float64(ops) / float64(timeMs) // 6. 操作吞吐
	for ev, thr := range throughput {
		res[ev] = thr / opsThroughput
	}
	return res
}

// deriveCPI 返回 {CPI, IPC}(优先 cycles/instructions, 缺失时用 :u 变体)。
func deriveCPI(perOp map[string]float64) map[string]float64 {
	pick := func(a, b string) float64 {
		if v, ok := perOp[a]; ok && v != 0 {
			return v
		}
		return perOp[b]
	}
	cycles := pick("cycles", "cycles:u")
	instr := pick("instructions", "instructions:u")
	out := map[string]float64{}
	if cycles != 0 && instr != 0 {
		out["CPI"] = cycles / instr
		out["IPC"] = instr / cycles
	}
	return out
}

// attribute 按微架构证据给出"受限类型"(阈值是本 demo 的经验线, 不来自 JMH 源码)。
func attribute(perOp, cpiIpc map[string]float64) (string, []string) {
	evidence := []string{}
	cycles := perOp["cycles"]
	if cpiIpc["IPC"] > 2.0 {
		evidence = append(evidence, fmt.Sprintf("IPC=%.2f 高于 2: 发射宽度未被打满", cpiIpc["IPC"]))
	}
	var front, back, l1Miss, brMiss float64
	if cycles != 0 {
		front = perOp["stalled-cycles-frontend"] / cycles
		back = perOp["stalled-cycles-backend"] / cycles
	}
	if v := perOp["L1-dcache-loads"]; v != 0 {
		l1Miss = perOp["L1-dcache-load-misses"] / v
	}
	if v := perOp["branches"]; v != 0 {
		brMiss = perOp["branch-misses"] / v
	}
	evidence = append(evidence, fmt.Sprintf(
		"stalled-frontend/cycles=%.1f%% stalled-backend/cycles=%.1f%% L1-load命中率=%.1f%% 分支误预测率=%.2f%% LLC-loads/op=%.4g",
		front*100, back*100, (1-l1Miss)*100, brMiss*100, perOp["LLC-loads"]))
	switch {
	case front >= 0.30:
		return "前端受限(取指/译码/分支预测)", evidence
	case back >= 0.30:
		return "后端受限(执行端口/依赖链)", evidence
	case l1Miss >= 0.10 || (perOp["LLC-loads"] != 0 && l1Miss >= 0.03):
		return "数据缓存受限(L1/LLC 未命中)", evidence
	case brMiss >= 0.02:
		return "分支误预测受限", evidence
	}
	return "未见明显微架构受限: 请回到墙钟口径与系统级证据", evidence
}

// formatValue 复制 JMH 对极小逐操作计数的近似零记法。
func formatValue(x float64) string {
	if math.Abs(x) < approxZeroThreshold {
		return "≈ 10⁻⁴"
	}
	for _, p := range []struct {
		div float64
		suf string
	}{{1e9, "G"}, {1e6, "M"}, {1e3, "k"}} {
		if math.Abs(x) >= p.div {
			return fmt.Sprintf("%.3f%s", x/p.div, p.suf)
		}
	}
	return fmt.Sprintf("%.3f", x)
}

// ------------------------------------------------------------------- 自检

var fixtureRates = map[string]float64{
	"cycles": 1.0e8, "instructions": 2.5e8,
	"L1-dcache-loads": 5.0e7, "L1-dcache-load-misses": 5.0e5,
	"branches": 2.0e7, "branch-misses": 1.0e5,
	"LLC-loads": 1.0e5, "stalled-cycles-frontend": 1.0e7,
	"stalled-cycles-backend": 5.0e6, "nonexistent-event": 1.0e9,
}

// fixture 生成 12 个 100ms 增量样本; mult[0] 含启动开销, 末两个含 ramp-down。
func fixture(missRate float64) []string {
	rates := map[string]float64{}
	for k, v := range fixtureRates {
		rates[k] = v
	}
	rates["L1-dcache-load-misses"] = rates["L1-dcache-loads"] * missRate
	mult := []float64{3.0, 1, 1, 1, 1, 1, 1, 1, 1, 2.5, 2.5, 1}
	lines := []string{"# time,count,event"}
	for i := 0; i < 12; i++ {
		t := 0.1 + float64(i)*0.1
		for ev, rate := range rates {
			lines = append(lines, fmt.Sprintf("%.1f,%.0f,%s", t, rate*0.1*mult[i], ev))
		}
	}
	return lines
}

func closeTo(a, b, tol float64) bool { return math.Abs(a-b) <= tol }

func main() {
	// 1) 事件探测
	available := map[string]bool{}
	for k := range fixtureRates {
		if k != "nonexistent-event" {
			available[k] = true
		}
	}
	cand := append(append([]string{}, interestingEvents...), "nonexistent-event")
	supported := probeSupported(cand, available)
	if len(supported) != 9 {
		panic(fmt.Sprintf("probe count = %d", len(supported)))
	}
	fmt.Printf("[探测] 候选 %d 个 -> 受支持 %d 个(不支持的事件静默丢弃)\n", len(cand), len(supported))

	// 2) 解析: 两种列数格式; 千分位分组符会造成列错位
	fmt.Println("[解析] 示例:", parseIncremental([]string{"0.1,1234,cycles", "0.1,1234,,cycles,extra", "0.1,abc,cycles"}))

	// 3) 完整流水线: 常量速率下每操作计数 = 速率/操作吞吐
	records := parseIncremental(fixture(0.01))
	perOp := normalize(records, 0, 1000, 100, 1_000_000, 1000, true, true)
	if !closeTo(perOp["cycles"], 100, 1e-6) || !closeTo(perOp["instructions"], 250, 1e-6) ||
		!closeTo(perOp["L1-dcache-loads"], 50, 1e-6) {
		panic(fmt.Sprintf("pipeline broken: %v", perOp))
	}
	cpiIpc := deriveCPI(perOp)
	if !closeTo(cpiIpc["CPI"], 0.4, 1e-9) || !closeTo(cpiIpc["IPC"], 2.5, 1e-9) {
		panic(fmt.Sprintf("CPI/IPC broken: %v", cpiIpc))
	}

	// 4) 裁剪步骤的影响
	noFilter := normalize(records, 0, 1000, 100, 1_000_000, 1000, false, true)
	keepFirst := normalize(records, 0, 1000, 100, 1_000_000, 1000, true, false)
	if closeTo(noFilter["cycles"], perOp["cycles"], 1e-6) ||
		closeTo(keepFirst["cycles"], perOp["cycles"], 1e-6) {
		panic("filter/skipFirst had no effect")
	}
	fmt.Printf("[裁剪] 完整流水线 cycles/op=%.3f; 跳过头尾=%.3f(偏差 %.1f%%); 计入首样本=%.3f(偏差 %.1f%%)\n",
		perOp["cycles"], noFilter["cycles"],
		math.Abs(noFilter["cycles"]/perOp["cycles"]-1)*100,
		keepFirst["cycles"], math.Abs(keepFirst["cycles"]/perOp["cycles"]-1)*100)

	// 5) 归因: 低未命中率 -> 无明显受限; 高未命中率 -> 数据缓存受限
	tag1, ev1 := attribute(perOp, cpiIpc)
	missPerOp := normalize(parseIncremental(fixture(0.25)), 0, 1000, 100, 1_000_000, 1000, true, true)
	tag2, ev2 := attribute(missPerOp, deriveCPI(missPerOp))
	if !strings.HasPrefix(tag1, "未见明显") || !strings.HasPrefix(tag2, "数据缓存受限") {
		panic(fmt.Sprintf("attribution wrong: %q %q", tag1, tag2))
	}
	fmt.Printf("[归因] 1%% 未命中 -> %s\n[归因] 25%% 未命中 -> %s\n", tag1, tag2)
	for _, e := range append(ev1, ev2...) {
		fmt.Println("    证据", e)
	}

	// 6) 近似零记法
	if formatValue(1e-5) != "≈ 10⁻⁴" || formatValue(0.001) != "0.001" || formatValue(12345) != "12.345k" {
		panic("formatValue broken")
	}

	// 7) 时间 vs PMU 的误差量级(jmh-dev 邮件实测输出)
	timeScore, timeErr := 0.252, 0.002
	cycScore, cycErr := 1.073, 0.043
	if !closeTo(cycScore/timeScore, 4.26, 0.02) || !closeTo(cycErr/timeErr, 21.5, 0.2) {
		panic("score/error ratio mismatch")
	}
	fmt.Printf("[口径] 时间 %.3f±%.3f ns/op 与 cycles %.3f±%.3f #/op: 分数差 %.2fx, 绝对误差差 %.1fx\n",
		timeScore, timeErr, cycScore, cycErr, cycScore/timeScore, cycErr/timeErr)

	fmt.Println("\nperfnorm_report: 全部自检通过")
}
