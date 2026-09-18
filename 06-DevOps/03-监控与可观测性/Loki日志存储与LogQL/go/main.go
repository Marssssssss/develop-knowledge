package main

// Loki 日志存储与 LogQL —— Go 版断言集（查询侧 A~F）。
//
// 用法：go run .    （本机无 Go 工具链，**未实跑**；已用 _docs/tools/go_sanity.py
// 做机械核查：未使用 import、断言函数实参个数、重复定义）
//
// 与 Python 版的差别：Go 版不实现 LogQL 词法/语法分析，管线阶段由调用方直接
// 构造（见 README「各语言实现范围」）。因此这里校验的是**语义**而不是解析。
// 存储/摄入侧的 G~J 见 checks_extra.go。

import (
	"fmt"
	"math"
	"os"
)

var (
	passed   int
	failures []string
	section  = "?"
)

func check(label string, cond bool, detail string) {
	if cond {
		passed++
		return
	}
	failures = append(failures, fmt.Sprintf("%s [%s] %s", label, section, detail))
}

func eqInt(label string, got, want int) {
	check(label, got == want, fmt.Sprintf("got=%d want=%d", got, want))
}

func eqStr(label, got, want string) {
	check(label, got == want, fmt.Sprintf("got=%q want=%q", got, want))
}

func eqFloat(label string, got, want float64) {
	check(label, math.Abs(got-want) < 1e-9, fmt.Sprintf("got=%v want=%v", got, want))
}

func eqBool(label string, got, want bool) {
	check(label, got == want, fmt.Sprintf("got=%v want=%v", got, want))
}

func eqStrs(label string, got, want []string) {
	check(label, EqualStrings(got, want), fmt.Sprintf("got=%v want=%v", got, want))
}

func wantErr(label string, err error) {
	check(label, err != nil, "期望报错，但没有")
}

func noErr(label string, err error) {
	check(label, err == nil, fmt.Sprintf("非预期错误: %v", err))
}

func mustMatcher(label, op, value string) Matcher {
	m, err := NewMatcher(label, op, value)
	if err != nil {
		panic(err)
	}
	return m
}

const (
	sec = int64(1000000000)
	t0  = int64(1700000000000000000)
)

func apiLabels() map[string]string { return map[string]string{"app": "api", "env": "prod"} }

func labels(pairs ...string) map[string]string {
	out := map[string]string{}
	for i := 0; i+1 < len(pairs); i += 2 {
		out[pairs[i]] = pairs[i+1]
	}
	return out
}

// repeatRune 生成 n 个重复字符。命名刻意不叫 strings —— 那会遮蔽标准库包名，
// 后续一旦真的 import "strings" 就会变成编译错误，且报错信息很难指回这里。
func repeatRune(count int, ch rune) string {
	buf := make([]rune, count)
	for i := range buf {
		buf[i] = ch
	}
	return string(buf)
}

func main() {
	section = "A"
	limits := DefaultLimits()
	eqFloat("A1 ingestion_rate_mb 默认 4MB/s", limits.IngestionRateMB, 4)
	eqFloat("A2 ingestion_burst_size_mb 默认 6MB", limits.IngestionBurstSizeMB, 6)
	eqStr("A3 限流策略默认 global", limits.IngestionRateStrategy, "global")
	eqInt("A4 每序列标签数默认 30", limits.MaxLabelNamesPerSeries, 30)
	eqInt("A5 max_streams_per_user 默认 0(本机不限)", limits.MaxStreamsPerUser, 0)
	eqInt("A6 max_global_streams_per_user 默认 5000", limits.MaxGlobalStreamsPerUser, 5000)
	eqStr("A7 活跃流窗口 = chunk_idle_period 30m", limits.ChunkIdlePeriod, "30m")

	section = "B"
	rate, err := DistributorRateMB(4, 10, "global")
	noErr("B1 均摊不报错", err)
	eqFloat("B1b global 策略按实例均摊 4/10", rate, 0.4)
	rate20, _ := DistributorRateMB(4, 20, "global")
	eqFloat("B2 扩容后每实例额度再降 4/20", rate20, 0.2)
	rateLocal, _ := DistributorRateMB(4, 10, "local")
	eqFloat("B3 local 策略不做均摊", rateLocal, 4)
	clusterGlobal, _ := ClusterEffectiveRateMB(4, 10, "global")
	eqFloat("B4 global 集群总额 = 配置值", clusterGlobal, 4)
	clusterLocal, _ := ClusterEffectiveRateMB(4, 10, "local")
	eqFloat("B5 local 集群总额 = 配置值 x N(坑)", clusterLocal, 40)
	eqFloat("B6 burst 不随实例数均摊(官方原文)", DistributorBurstMB(6, 10, "global"), 6)
	rate5, _ := DistributorRateMB(4, 5, "global")
	eqBool("B7 实例减少反而抬高每实例阈值", rate5 > rate, true)
	_, errZero := DistributorRateMB(4, 0, "global")
	wantErr("B8 实例数为 0 直接报错", errZero)

	section = "C"
	prod := labels("app", "api-server", "env", "prod", "cluster", "c1")
	noenv := labels("app", "api-server")
	exact := StreamSelector{Matchers: []Matcher{mustMatcher("app", "=", "api")}}
	eqBool("C1 精确匹配不做前缀", exact.Matches(prod), false)
	exactHit := StreamSelector{Matchers: []Matcher{mustMatcher("app", "=", "api-server")}}
	eqBool("C2 精确匹配命中", exactHit.Matches(prod), true)
	// Go 的 MatchString 是搜索语义，若忘了加 ^(?:...)$ 这里会命中 → 断言会挂。
	anchored := StreamSelector{Matchers: []Matcher{mustMatcher("app", "=~", "api")}}
	eqBool("C3 流选择器的正则完全锚定(坑)", anchored.Matches(prod), false)
	wildcard := StreamSelector{Matchers: []Matcher{mustMatcher("app", "=~", "api.*")}}
	eqBool("C4 需要 .* 才能命中", wildcard.Matches(prod), true)
	neProd := StreamSelector{Matchers: []Matcher{mustMatcher("env", "!=", "prod")}}
	eqBool("C5 缺失标签 == 空串故 env!=prod 命中", neProd.Matches(noenv), true)
	missingRe := StreamSelector{Matchers: []Matcher{mustMatcher("env", "!~", ".+")}}
	eqBool("C6 env!~\".+\" 同样命中缺失标签(反直觉)", missingRe.Matches(noenv), true)
	eqBool("C6b env!~\".+\" 对已有 env 不命中", missingRe.Matches(prod), false)
	both := StreamSelector{Matchers: []Matcher{
		mustMatcher("app", "=", "api-server"), mustMatcher("env", "=", "prod"),
	}}
	eqBool("C7 多 matcher 之间是 AND", both.Matches(prod), true)
	_, errRe := NewMatcher("app", "=~", "(")
	wantErr("C8 非法正则提前报错", errRe)

	section = "D"
	line := `level=error msg="disk full" path=/api/v1/users`
	contains := LineFilter{Op: "|=", Value: "error"}
	eqBool("D1 |= 是子串包含", contains.Apply(NewEntry(t0, line, nil)), true)
	upper := LineFilter{Op: "|=", Value: "ERROR"}
	eqBool("D2 行过滤大小写敏感", upper.Apply(NewEntry(t0, line, nil)), false)
	// 行过滤是**搜索**语义（非锚定）—— 与流选择器 =~ 的完全锚定刻意相反。
	searchMode := LineFilter{Op: "|~", Value: "err"}
	eqBool("D3 |~ 是搜索语义(非锚定)", searchMode.Apply(NewEntry(t0, line, nil)), true)
	anchoredLine := LineFilter{Op: "|~", Value: "^err"}
	eqBool("D4 ^err 不命中说明确实非锚定", anchoredLine.Apply(NewEntry(t0, line, nil)), false)
	notDebug := LineFilter{Op: "!=", Value: "debug"}
	eqBool("D5 != 是不包含", notDebug.Apply(NewEntry(t0, line, nil)), true)
	notErr := LineFilter{Op: "!~", Value: "err.*"}
	eqBool("D6 !~ 取反", notErr.Apply(NewEntry(t0, line, nil)), false)

	section = "E"
	good := `{"level":"error","status":500,"d":12}`
	bad := "not a json line"
	afterJSON := RunPipeline(NewEntry(t0, good, nil), []Stage{JSONParser{}})
	eqStr("E1 json 提取标量字段", afterJSON.Labels["level"], "error")
	eqStr("E1b 数字字段转成标签", afterJSON.Labels["status"], "500")
	_, hasErrLabel := afterJSON.Labels[ErrorLabel]
	eqBool("E2 合法行没有 __error__", hasErrLabel, false)
	afterBad := RunPipeline(NewEntry(t0, bad, nil), []Stage{JSONParser{}})
	eqBool("E3 解析失败不丢行(官方原文)", afterBad != nil, true)
	eqStr("E3b 错误标签名", afterBad.Labels[ErrorLabel], JSONParserErr)
	kept := RunPipeline(NewEntry(t0, bad, nil), []Stage{JSONParser{}, ErrorFilter{KeepErrors: false}})
	eqBool("E4 想丢错误行必须显式过滤", kept == nil, true)
	onlyErr := RunPipeline(NewEntry(t0, bad, nil), []Stage{JSONParser{}, ErrorFilter{KeepErrors: true}})
	eqBool("E5 只想看错误行", onlyErr != nil, true)
	nested := RunPipeline(NewEntry(t0, `{"a":{"b":1}}`, nil), []Stage{JSONParser{}})
	eqStr("E6 嵌套 JSON 用 _ 摊平", nested.Labels["a_b"], "1")
	noStatus := RunPipeline(NewEntry(t0, `{"other":1}`, nil), []Stage{
		JSONParser{}, LabelCompare{Label: "status", Op: ">=", Value: "500", Numeric: true},
	})
	eqBool("E7 数值比较取不到数字时不丢行(坑)", noStatus != nil, true)
	_, marked := noStatus.Labels[ErrorLabel]
	eqBool("E7b 且被打了错误标签", marked, true)

	// 指标查询遇错即失败：这是 unwrap 之后必须补 `| __error__ = ""` 的原因。
	errEntries := []*Entry{
		RunPipeline(NewEntry(t0-2*sec, good, apiLabels()), []Stage{JSONParser{}}),
		RunPipeline(NewEntry(t0-sec, bad, apiLabels()), []Stage{JSONParser{}}),
	}
	_, gateErr := EvaluateMetric("rate", 300, 0, false, errEntries)
	wantErr("E8 指标查询含错误直接失败(官方原文)", gateErr)
	clean := []*Entry{RunPipeline(NewEntry(t0-sec, good, apiLabels()), []Stage{
		JSONParser{}, ErrorFilter{KeepErrors: false},
	})}
	cleanResult, cleanErr := EvaluateMetric("rate", 300, 0, false, clean)
	noErr("E8b 补上 __error__ 过滤后查询可用", cleanErr)
	eqFloat("E8c 且分子只剩合法行", cleanResult[SeriesKey(clean[0].Labels)], 1.0/300.0)
	unwrapBad := RunPipeline(NewEntry(t0, `{"d":"abc"}`, apiLabels()), []Stage{
		JSONParser{}, UnwrapStage{Field: "d"},
	})
	eqStr("E9 unwrap 遇非数字打错误标签", unwrapBad.Labels[ErrorLabel], SampleExtractionErr)
	unwrapOK := RunPipeline(NewEntry(t0, `{"d":7}`, apiLabels()), []Stage{
		JSONParser{}, UnwrapStage{Field: "d"},
	})
	eqFloat("E10 unwrap 取到数值", unwrapOK.Value, 7)
	_, stillThere := unwrapOK.Labels["d"]
	eqBool("E10b unwrap 会消费掉被 unwrap 的标签", stillThere, false)

	section = "F"
	apiEntries := []*Entry{}
	for i := 0; i < 100; i++ {
		apiEntries = append(apiEntries, NewEntry(t0-int64(100-i)*sec, "hello", apiLabels()))
	}
	count, _ := EvaluateMetric("count_over_time", 300, 0, false, apiEntries)
	eqFloat("F1 count_over_time 数行数", count[SeriesKey(apiLabels())], 100)
	rateVal, _ := EvaluateMetric("rate", 300, 0, false, apiEntries)
	eqFloat("F2 rate = 行数/窗口秒数", rateVal[SeriesKey(apiLabels())], 100.0/300.0)
	bytesVal, _ := EvaluateMetric("bytes_over_time", 300, 0, false, apiEntries)
	eqFloat("F3 bytes_over_time 累加行字节", bytesVal[SeriesKey(apiLabels())], 500)
	absent, _ := rangeValue("absent_over_time", 300, 0, nil)
	eqFloat("F4 absent_over_time 空窗口返回 1", absent, 1)
	_, mismatch := EvaluateMetric("count_over_time", 300, 0, true, apiEntries)
	wantErr("F5 count_over_time 不接受 unwrap 后的数值", mismatch)
	_, needUnwrap := EvaluateMetric("avg_over_time", 300, 0, false, apiEntries)
	wantErr("F6 avg_over_time 必须配合 unwrap", needUnwrap)
	// 分位数走线性插值：[10,20,30,40] 的 0.5 分位是 25，不是 20 也不是 30。
	eqFloat("F7 分位数线性插值", PromQuantile(0.5, []float64{10, 20, 30, 40}), 25)
	eqFloat("F7b φ=0.25 插值", PromQuantile(0.25, []float64{10, 20, 30, 40}), 17.5)
	eqFloat("F7c 奇数样本落在元素上不插值", PromQuantile(0.5, []float64{10, 20, 30}), 20)
	eqFloat("F8 总体方差(除以 N)", PopulationStdVar([]float64{1, 2, 3, 4}), 1.25)
	eqFloat("F8b stddev = sqrt(总体方差)", math.Sqrt(PopulationStdVar([]float64{10, 20, 30, 40})), math.Sqrt(125))

	runExtraChecks()

	fmt.Printf("PASSED %d  FAILED %d\n", passed, len(failures))
	for _, item := range failures {
		fmt.Println("  FAIL", item)
	}
	if len(failures) > 0 {
		os.Exit(1)
	}
}
