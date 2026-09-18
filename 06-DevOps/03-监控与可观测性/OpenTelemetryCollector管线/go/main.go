package main

import (
	"fmt"
	"math"
	"os"
)

var nPass int
var nFail int
var failures []string

// check 三参形式:label、条件、失败时打印的细节。
func check(label string, cond bool, detail string) {
	if cond {
		nPass++
		return
	}
	nFail++
	failures = append(failures, label+"  "+detail)
}

func expectErr(label string, err error) {
	check(label, err != nil, "期望报错但没有")
}

func near(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

func mkRecs(n int, kv ...string) []Record {
	out := make([]Record, 0, n)
	for i := 0; i < n; i++ {
		out = append(out, newRecord(kv...))
	}
	return out
}

func rawConfig() *Config {
	cfg := NewConfig()
	cfg.Sections["receivers"] = []string{"otlp", "prometheus"}
	cfg.Sections["processors"] = []string{"memory_limiter", "batch", "batch/logs",
		"attributes", "filter/drop_health", "resourcedetection"}
	cfg.Sections["exporters"] = []string{"otlp", "debug", "otlphttp"}
	cfg.Sections["connectors"] = []string{"spanmetrics"}
	cfg.Sections["extensions"] = []string{"file_storage", "health_check"}
	cfg.ServiceExt = []string{"file_storage", "health_check"}
	cfg.Pipelines = []Pipeline{
		{Signal: "traces", Receivers: []string{"otlp"},
			Processors: []string{"memory_limiter", "batch", "attributes"},
			Exporters:  []string{"spanmetrics", "debug"}},
		{Signal: "traces", Name: "primary", Receivers: []string{"otlp"},
			Processors: []string{"memory_limiter", "batch"}, Exporters: []string{"otlp"}},
		{Signal: "metrics", Receivers: []string{"spanmetrics"},
			Processors: []string{"memory_limiter", "batch"}, Exporters: []string{"otlphttp"}},
		{Signal: "logs", Receivers: []string{"otlp"},
			Processors: []string{"batch/logs", "filter/drop_health", "memory_limiter"},
			Exporters:  []string{"otlp"}},
	}
	return cfg
}

func main() {
	// ------------------------------------------------------------ A 组件 ID
	t, n, err := ParseComponentID("batch/traces")
	check("A1 type/name 拆分", t == "batch" && n == "traces" && err == nil,
		fmt.Sprintf("%s %s %v", t, n, err))
	_, n2, err2 := ParseComponentID("otlp")
	check("A2 无 name 形式", n2 == "" && err2 == nil, n2)
	check("A3 同 type 不同 name", IsSameType("batch", "batch/logs"), "")
	check("A4 不同 type", !IsSameType("batch", "otlp"), "")
	check("A5 提取 type", ComponentType("filter/drop_health") == "filter", "")
	for _, bad := range []string{"batch/", "/traces", "batch/traces/x", "1batch",
		"", "batch//x", "a b", "batch/2x"} {
		_, _, e := ParseComponentID(bad)
		expectErr("A6 非法 ID "+bad, e)
	}
	_, _, e7 := ParseComponentID("traces/2")
	check("A7 name 也须以字母开头(坑)", e7 != nil, "traces/2 应被拒")

	// ------------------------------------------------------------ B 配置校验
	cfg := rawConfig()
	warns, _ := cfg.Validate()
	check("B1 pipeline 数量", len(cfg.Pipelines) == 4, fmt.Sprintf("%d", len(cfg.Pipelines)))
	check("B2 未使用组件告警", len(warns) == 2 &&
		warns[0] == "unused receiver: prometheus" &&
		warns[1] == "unused processor: resourcedetection", fmt.Sprintf("%v", warns))
	check("B3 同 section 各自命名空间",
		has(cfg.Sections["receivers"], "otlp") && has(cfg.Sections["exporters"], "otlp"), "")
	_, okB4 := cfg.Pipeline("traces/primary")
	check("B4 pipeline key 形态", okB4, "")
	_, errB5 := NewConfig().Validate()
	expectErr("B5 空 pipelines 非法", errB5)
	badR := rawConfig()
	badR.Pipelines[0].Receivers = []string{"nope"}
	_, errB6 := badR.Validate()
	expectErr("B6 未定义 receiver 非法", errB6)
	badP := rawConfig()
	badP.Pipelines[0].Processors = []string{"memory_limiter", "nope"}
	_, errB7 := badP.Validate()
	expectErr("B7 未定义 processor 非法", errB7)
	badX := rawConfig()
	badX.ServiceExt = []string{"fs"}
	_, errB8 := badX.Validate()
	expectErr("B8 未定义 extension 非法", errB8)
	dup := rawConfig()
	dup.Pipelines = append(dup.Pipelines, dup.Pipelines[0])
	_, errB9 := dup.Validate()
	expectErr("B9 重复 pipeline 非法", errB9)

	// ------------------------------------------------------------ C fanout
	rf := cfg.ReceiverFanout()
	check("C1 receiver 扇出多路", len(rf["otlp"]) == 3 && rf["otlp"][0] == "traces" &&
		rf["otlp"][1] == "traces/primary" && rf["otlp"][2] == "logs",
		fmt.Sprintf("%v", rf["otlp"]))
	check("C2 connector 作 receiver", len(rf["spanmetrics"]) == 1 && rf["spanmetrics"][0] == "metrics", "")
	ef := cfg.ExporterFanout()
	check("C3 exporter 侧广播", len(ef["traces"]) == 2 && ef["traces"][1] == "debug", "")
	check("C4 串行扇出求和", FanoutLatencyMs([]int{3, 5, 1}, false) == 9, "")
	check("C5 并行扇出取最慢", FanoutLatencyMs([]int{3, 5, 1}, true) == 5, "")
	check("C6 空分支", FanoutLatencyMs(nil, true) == 0, "")

	// ------------------------------------------------------------ D connector 图
	edges := cfg.ConnectorEdges()
	check("D1 connector 边", len(edges) == 1 && edges[0][0] == "traces" && edges[0][1] == "metrics",
		fmt.Sprintf("%v", edges))
	order, errD2 := cfg.TopoOrder()
	check("D2 拓扑序", errD2 == nil && len(order) == 4 && order[0] == "logs" &&
		order[3] == "metrics", fmt.Sprintf("%v", order))
	cyc := rawConfig()
	cyc.Sections["connectors"] = []string{"spanmetrics", "forward"}
	cyc.Pipelines[2].Exporters = []string{"otlphttp", "forward"}
	cyc.Pipelines[0].Receivers = []string{"otlp", "forward"}
	_, errD3 := cyc.TopoOrder()
	expectErr("D3 环检测报错", errD3)

	// ------------------------------------------------------------ E limiter
	lm := NewMemoryLimiter(4000, -1, 0)
	check("E1 spike 默认 20%", lm.SpikeLimitMib == 800, fmt.Sprintf("%d", lm.SpikeLimitMib))
	check("E2 软限 = 硬限 - 尖峰", lm.SoftLimitMib() == 3200, "")
	v, h := lm.Check(3000)
	check("E3 低于软限放行", v == "ok" && h == 200, fmt.Sprintf("%s %d", v, h))
	v, h = lm.Check(3200)
	check("E4 恰等于软限仍放行", v == "ok" && h == 0, fmt.Sprintf("%s %d", v, h))
	v, h = lm.Check(3201)
	check("E5 刚过软限拒绝", v == "refused" && h == -1, fmt.Sprintf("%s %d", v, h))
	v, h = lm.Check(4000)
	check("E6 等于硬限仍拒绝", v == "refused" && h == -800, fmt.Sprintf("%s %d", v, h))
	v, h = lm.Check(4001)
	check("E7 超硬限触发 GC", v == "gc" && h == -801, fmt.Sprintf("%s %d", v, h))
	v, _ = NewMemoryLimiter(0, -1, 0).Check(99999)
	check("E8 limit=0 不设限", v == "ok", v)
	check("E9 显式 spike", NewMemoryLimiter(4096, 1024, 0).SoftLimitMib() == 3072, "")
	check("E10 默认 check_interval", lm.CheckInterval == 0, "")
	check("E11 首位判定为真", cfg.MemoryLimiterFirst("traces"), "")
	check("E12 末位判定为假", !cfg.MemoryLimiterFirst("logs"), "")
	fp, _ := NewFilterProcessor([]string{`x == "1"`}, "ignore")
	bpX, _ := NewBatchProcessor(8192, 200, 0)
	check("E13 末位白做工作量", WastedWork([]interface{}{bpX, fp}, 100) == 200,
		fmt.Sprintf("%d", WastedWork([]interface{}{bpX, fp}, 100)))

	// ------------------------------------------------------------ F batch
	b0, _ := NewBatchProcessor(8192, 200, 0)
	check("F1 默认值 8192/200ms/0", b0.SendBatchSize == 8192 && b0.TimeoutMs == 200 &&
		b0.SendBatchMax == 0, "")
	bx, _ := NewBatchProcessor(8192, 200, 0)
	check("F2 未达阈值不发送", len(bx.Extend(mkRecs(8191), 0)) == 0, "")
	b1, _ := NewBatchProcessor(8192, 200, 0)
	out1 := b1.Extend(mkRecs(8192), 0)
	check("F3 达到阈值立即发送", len(out1) == 1 && len(out1[0]) == 8192 &&
		len(b1.Emitted) == 1 && b1.Emitted[0] == "size[8192]", fmt.Sprintf("%v", b1.Emitted))
	b2, _ := NewBatchProcessor(8192, 200, 0)
	out2 := b2.Extend(mkRecs(10000), 0)
	check("F4 max=0 不切分", len(out2) == 1 && len(out2[0]) == 8192 && b2.BufLen() == 1808, "")
	b3, _ := NewBatchProcessor(8192, 200, 10000)
	out3 := b3.Push(mkRecs(25000), 0)
	check("F5 push 按 max 切分且尾批可小", len(out3) == 3 && len(out3[0]) == 10000 &&
		len(out3[1]) == 10000 && len(out3[2]) == 5000 && b3.BufLen() == 0, "")
	b4, _ := NewBatchProcessor(1000, 200, 2000)
	out4 := b4.Push(mkRecs(5000), 0)
	check("F6 小规模切分", len(out4) == 3 && len(out4[2]) == 1000, "")
	b4b, _ := NewBatchProcessor(8192, 200, 0)
	out4b := b4b.Push(mkRecs(25000), 0)
	check("F7 max=0 时单批全发", len(out4b) == 1 && len(out4b[0]) == 25000, "")
	b5, _ := NewBatchProcessor(8192, 200, 0)
	b5.Extend(mkRecs(5), 1000)
	check("F8 未到 timeout 不发送", len(b5.Tick(1199)) == 0, "")
	tk := b5.Tick(1200)
	check("F9 timeout 兜底发送", len(tk) == 1 && len(tk[0]) == 5 &&
		len(b5.Emitted) == 1 && b5.Emitted[0] == "timeout[5]", fmt.Sprintf("%v", b5.Emitted))
	check("F10 flush 后计时器复位", len(b5.Tick(9999)) == 0, "")
	_, errF11 := NewBatchProcessor(8192, 200, 4096)
	expectErr("F11 max < size 非法", errF11)
	b12, errF12 := NewBatchProcessor(8192, 200, 8192)
	check("F12 max == size 合法", errF12 == nil && b12.SendBatchMax == 8192, "")
	b6, _ := NewBatchProcessor(8192, 200, 0)
	check("F13 空 push 不启动计时", len(b6.Push(nil, 500)) == 0 && !b6.hasFirst, "")

	// ------------------------------------------------------------ G 变换与过滤
	ap, _ := NewAttributesProcessor([]Action{
		{Act: "insert", Key: "env", Value: "prod"},
		{Act: "update", Key: "svc", Value: "api"},
		{Act: "upsert", Key: "env", Value: "stage"},
		{Act: "extract", Key: "route", From: "http.route"},
		{Act: "hash", Key: "user.email"},
		{Act: "delete", Key: "secret"},
	})
	r := ap.Apply(newRecord("env", "dev", "http.route", "/health",
		"user.email", "a@b.c", "secret", "x", "duration_ms", "3"))
	ins, _ := NewAttributesProcessor([]Action{{Act: "insert", Key: "k", Value: "2"}})
	check("G1 insert 不覆盖已有值", ins.Apply(newRecord("k", "1")).Attrs["k"] == "1", "")
	check("G2 upsert 覆盖 insert 写过的值", r.Attrs["env"] == "stage", r.Attrs["env"])
	_, okSvc := r.Attrs["svc"]
	check("G3 update 缺 key 不新增", !okSvc, "")
	check("G4 extract 拷贝", r.Attrs["route"] == "/health", "")
	check("G5 hash 为 sha1 长度", len(r.Attrs["user.email"]) == 40, "")
	_, okSec := r.Attrs["secret"]
	check("G6 delete 移除", !okSec, "")
	upd, _ := NewAttributesProcessor([]Action{{Act: "upsert", Key: "k", Value: "2"}})
	check("G7 upsert 覆盖存在值", upd.Apply(newRecord("k", "1")).Attrs["k"] == "2", "")
	upd2, _ := NewAttributesProcessor([]Action{{Act: "update", Key: "k", Value: "2"}})
	check("G8 update 覆盖存在值", upd2.Apply(newRecord("k", "1")).Attrs["k"] == "2", "")
	_, errG9 := NewAttributesProcessor([]Action{{Act: "boom", Key: "k"}})
	expectErr("G9 未知动作非法", errG9)
	check("G10 hash 可复现", HashValue("a@b.c") == HashValue("a@b.c"), "")
	f, _ := NewFilterProcessor([]string{`http.route == "/health"`,
		`http.route =~ "/(ready|live)"`}, "ignore")
	m1, _ := f.Matches(newRecord("http.route", "/health"))
	check("G11 等值命中", m1, "")
	m2, _ := f.Matches(newRecord("http.route", "/ready"))
	check("G12 正则整串匹配", m2, "")
	m3, _ := f.Matches(newRecord("http.route", "/readyz"))
	check("G13 前缀不算命中", !m3, "")
	fn, _ := NewFilterProcessor([]string{"duration_ms < 5"}, "ignore")
	m4, _ := fn.Matches(newRecord("duration_ms", "3"))
	check("G14 数值比较", m4, "")
	m5, _ := fn.Matches(newRecord())
	check("G15 缺失属性不等于命中", !m5, "")
	fne, _ := NewFilterProcessor([]string{`env != "prod"`}, "ignore")
	m6, _ := fne.Matches(newRecord())
	check("G16 缺失属性使 != 命中(坑)", m6, "")
	m7, _ := f.Matches(newRecord("http.route", "/api"))
	check("G17 不匹配则放行", !m7, "")
	_, errG18 := EvalCondition("duration_ms ~~ 3", newRecord())
	expectErr("G18 非法条件抛错", errG18)
	_, errG19 := NewFilterProcessor(nil, "silent")
	expectErr("G19 error_mode 校验", errG19)
	fi, _ := NewFilterProcessor([]string{"bad ~~ 1"}, "ignore")
	m20, err20 := fi.Matches(newRecord())
	check("G20 ignore 吞掉坏条件", !m20 && err20 == nil, "")

	checkExporter()

	if nFail > 0 {
		fmt.Printf("FAILED %d / %d\n", nFail, nFail+nPass)
		for _, s := range failures {
			fmt.Println("  - " + s)
		}
		os.Exit(1)
	}
	fmt.Printf("ALL PASS  %d assertions\n", nPass)
}
