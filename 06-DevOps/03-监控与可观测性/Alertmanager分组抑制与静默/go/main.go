package main

import (
	"fmt"
	"math"
	"os"
	"strings"
)

var nPass, nFail int
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

func expectErrString(label string, err error) bool {
	if err == nil {
		nFail++
		failures = append(failures, label+"  期望报错但没有")
		return false
	}
	nPass++
	return true
}

func near(a, b float64) bool { return math.Abs(a-b) < 1e-9 }

// lbl 从 k,v 交替的参数构造标签集。
func lbl(kv ...string) map[string]string {
	m := map[string]string{}
	for i := 0; i+1 < len(kv); i += 2 {
		m[kv[i]] = kv[i+1]
	}
	return m
}

func equalStrings(a, b []string) bool {
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

func hitsDetail(hits []Hit) string {
	parts := make([]string, 0, len(hits))
	for _, h := range hits {
		parts = append(parts, h.Receiver+"@"+strings.Join(h.Path, "/"))
	}
	return strings.Join(parts, ",")
}

func routeTree() (*Route, *Route) {
	db := &Route{Name: "db", Receiver: "database-pager", GroupWait: 10,
		Matchers: MustMatchers(`service=~"mysql|cassandra"`)}
	fe := &Route{Name: "fe", Receiver: "frontend-pager",
		GroupBy: []string{"product", "environment"}, Matchers: MustMatchers(`team="frontend"`)}
	crit := &Route{Name: "crit", Receiver: "pager-rotation",
		Matchers: MustMatchers(`severity="critical"`), Continue: true}
	pay := &Route{Name: "pay", Receiver: "payments-slack", Matchers: MustMatchers(`team="payments"`)}
	plat := &Route{Name: "plat", Receiver: "platform-slack", Matchers: MustMatchers(`team="platform"`)}
	root := &Route{Name: "root", Receiver: "default-receiver",
		GroupBy: []string{"alertname", "cluster"}, Routes: []*Route{db, fe, crit, pay, plat}}
	return root, crit
}

func main() {
	// ---------------------------------------------------------- A matcher
	m, err := ParseMatcher(`severity="critical"`)
	check("A1 解析结果", err == nil && m.Key == "severity" && m.Op == "=" && m.Value == "critical",
		fmt.Sprintf("%v %v", m, err))
	check("A2 等值命中", m.Matches(lbl("severity", "critical")), "")
	check("A3 等值不命中", !m.Matches(lbl("severity", "warning")), "")
	mne, _ := NewMatcher("severity", "!=", "critical")
	check("A4 不等值命中", mne.Matches(lbl("severity", "warning")), "")
	mre, _ := NewMatcher("team", "=~", "payments")
	check("A5 正则整串锚定(坑)", !mre.Matches(lbl("team", "payments-eu")), "")
	mmulti, _ := NewMatcher("severity", "=~", "critical|warning")
	check("A6 正则多值用竖线", mmulti.Matches(lbl("severity", "warning")), "")
	mcomma, _ := NewMatcher("severity", "=~", "critical,warning")
	check("A7 逗号不是多值分隔符", !mcomma.Matches(lbl("severity", "critical")), "")
	mnv, _ := NewMatcher("severity", "!~", "info|debug")
	check("A8 反向正则", mnv.Matches(lbl("severity", "critical")), "")
	check("A9 缺失标签使 = 不命中", !m.Matches(lbl()), "")
	mne2, _ := NewMatcher("env", "!=", "prod")
	check("A10 缺失标签使 != 命中(坑)", mne2.Matches(lbl()), "")
	mall, _ := NewMatcher("env", "=~", ".*")
	check("A11 缺失标签使 .* 命中", mall.Matches(lbl()), "")
	mnall, _ := NewMatcher("env", "!~", ".*")
	check("A12 缺失标签使 !~ 不命中", !mnall.Matches(lbl()), "")
	check("A13 AND 语义", LabelsMatch(MustMatchers(`a="1"`, `b="2"`), lbl("a", "1", "b", "2")), "")
	check("A14 AND 缺一不可", !LabelsMatch(MustMatchers(`a="1"`, `b="2"`), lbl("a", "1")), "")
	_, errA15 := ParseMatcher("severity=critical")
	expectErrString("A15 无引号非法", errA15)
	for _, bad := range []string{"severity==critical", `severity~"x"`, `severity="x`, `1bad="x"`, `="x"`} {
		_, e := ParseMatcher(bad)
		expectErrString("A17 非法 matcher "+bad, e)
	}
	_, errA16 := NewMatcher("a", "==", "x")
	expectErrString("A16 非法运算符", errA16)
	check("A18 空 matcher 列表恒真", LabelsMatch(nil, lbl("anything", "1")), "")
	f1 := Fingerprint(lbl("alertname", "A", "cluster", "c1"))
	check("A19 指纹稳定", f1 == Fingerprint(lbl("cluster", "c1", "alertname", "A")), f1)
	check("A20 不同标签不同指纹", f1 != Fingerprint(lbl("alertname", "A", "cluster", "c2")), "")

	// ---------------------------------------------------------- B 分组键
	check("B1 空 group_by 全部一组", GroupKey(lbl("a", "1"), nil) == "", "")
	check("B2 '...' 表示不聚合",
		GroupKey(lbl("a", "1"), []string{GroupByAll}) != GroupKey(lbl("a", "2"), []string{GroupByAll}), "")
	check("B3 '...' 与指纹一致",
		GroupKey(lbl("a", "1"), []string{GroupByAll}) == Fingerprint(lbl("a", "1")), "")
	k1 := GroupKey(lbl("alertname", "A", "cluster", "c1"), []string{"alertname", "cluster"})
	k3 := GroupKey(lbl("alertname", "A", "cluster", "c2"), []string{"alertname", "cluster"})
	check("B4 同值同组",
		k1 == GroupKey(lbl("cluster", "c1", "alertname", "A"), []string{"alertname", "cluster"}), k1)
	check("B5 异值异组", k1 != k3, "")
	check("B6 组键含标签名", strings.Contains(k1, "cluster=c1"), k1)
	check("B7 缺标签取空值", GroupKey(lbl(), []string{"cluster"}) == "cluster=", "")

	// ---------------------------------------------------------- C 路由树
	root, crit := routeTree()
	check("C1 无子路由命中落到 root",
		equalStrings(Receivers(Dispatch(root, lbl("team", "x"), nil, nil)), []string{"default-receiver"}), "")
	check("C2 命中子路由",
		equalStrings(Receivers(Dispatch(root, lbl("service", "mysql"), nil, nil)), []string{"database-pager"}), "")
	feHits := Dispatch(root, lbl("team", "frontend"), nil, nil)
	check("C3 命中子路由时不落 root",
		len(feHits) == 1 && feHits[0].Receiver == "frontend-pager", hitsDetail(feHits))
	check("C4 子路由 group_by 覆盖",
		equalStrings(feHits[0].Params.GroupBy, []string{"product", "environment"}), "")
	dbHits := Dispatch(root, lbl("service", "mysql"), nil, nil)
	check("C5 子路由 group_wait 覆盖", near(dbHits[0].Params.GroupWait, 10.0), "")
	check("C6 未覆盖项继承父值",
		near(feHits[0].Params.GroupWait, 30.0) && near(feHits[0].Params.RepeatInterval, 14400.0), "")
	both := Dispatch(root, lbl("severity", "critical", "team", "payments"), nil, nil)
	check("C7 continue=true 继续兄弟匹配",
		equalStrings(Receivers(both), []string{"pager-rotation", "payments-slack"}), hitsDetail(both))
	only := Dispatch(root, lbl("severity", "critical", "team", "unknown"), nil, nil)
	check("C8 兄弟不匹配则只命中一个",
		equalStrings(Receivers(only), []string{"pager-rotation"}), hitsDetail(only))
	crit.Continue = false
	blocked := Dispatch(root, lbl("severity", "critical", "team", "payments"), nil, nil)
	check("C9 continue=false 阻断兄弟(重复打扰的根因)",
		equalStrings(Receivers(blocked), []string{"pager-rotation"}), hitsDetail(blocked))
	crit.Continue = true
	check("C10 路径记录", equalStrings(feHits[0].Path, []string{"fe"}), hitsDetail(feHits))
	def := DefaultParams()
	check("C12 默认值落地", near(def.GroupWait, 30) && near(def.GroupInterval, 300) &&
		near(def.RepeatInterval, 14400), "")
	check("C13 root 带 matchers 即不再匹配全部",
		len(Dispatch(&Route{Receiver: "r", Matchers: MustMatchers(`a="1"`)}, lbl("a", "2"), nil, nil)) == 0, "")
	check("C13b 未设置项回落默认值",
		near(ResolveParams(&Route{}, nil).GroupInterval, 300), "")
	d30, _ := ParseDuration("30s")
	d5m, _ := ParseDuration("5m")
	d4h, _ := ParseDuration("4h")
	dms, _ := ParseDuration("100ms")
	check("C14 时长解析", near(d30, 30) && near(d5m, 300) && near(d4h, 14400) && near(dms, 0.1), "")
	_, errC18 := ParseDuration("5x")
	expectErrString("C18 非法时长", errC18)

	// ---------------------------------------------------------- D 抑制
	critAlert := Alert{Labels: lbl("alertname", "LatencyHigh", "severity", "critical",
		"cluster", "c1", "service", "api")}
	warnSame := Alert{Labels: lbl("alertname", "LatencyHigh", "severity", "warning",
		"cluster", "c1", "service", "api")}
	warnOther := Alert{Labels: lbl("alertname", "LatencyHigh", "severity", "warning",
		"cluster", "c1", "service", "web")}
	rule := InhibitRule{Source: MustMatchers(`severity="critical"`),
		Target: MustMatchers(`severity="warning"`), Equal: []string{"alertname", "cluster", "service"}}
	check("D1 同类告警被抑制", len(InhibitedBy(warnSame, []Alert{critAlert}, []InhibitRule{rule})) == 1, "")
	check("D2 equal 不同则不抑制", len(InhibitedBy(warnOther, []Alert{critAlert}, []InhibitRule{rule})) == 0, "")
	check("D3 告警不抑制自己", len(InhibitedBy(critAlert, []Alert{critAlert}, []InhibitRule{rule})) == 0, "")
	infoSrc := Alert{Labels: lbl("alertname", "X", "severity", "info")}
	warnX := Alert{Labels: lbl("alertname", "X", "severity", "warning")}
	check("D4 源未命中则不抑制", len(InhibitedBy(warnX, []Alert{infoSrc}, []InhibitRule{rule})) == 0, "")
	check("D5 target 未命中则不抑制",
		len(InhibitedBy(Alert{Labels: lbl("alertname", "X", "severity", "info")},
			[]Alert{critAlert}, []InhibitRule{rule})) == 0, "")
	inhib := InhibitedBy(warnSame, []Alert{critAlert}, []InhibitRule{rule})
	check("D6 equal 返回规则下标与源指纹",
		len(inhib) == 1 && inhib[0].RuleIndex == 0 && inhib[0].SourceFingerprint == critAlert.Fingerprint(), "")
	check("D7 equal 要求两边都有",
		!EqualLabelsHold(lbl("a", "1", "b", "2"), lbl("a", "1"), []string{"a", "b"}), "")
	check("D8 两边都缺该标签算相等(坑)",
		EqualLabelsHold(lbl("a", "1"), lbl("a", "1"), []string{"b"}), "")
	check("D9 空 equal 恒相等", EqualLabelsHold(lbl("a", "1"), lbl("b", "2"), nil), "")
	emptyEqual := InhibitRule{Source: MustMatchers(`severity="critical"`), Target: MustMatchers(`severity="warning"`)}
	check("D10 空 equal 时同 alertname 的告警互相抑制(经典误配)",
		len(InhibitedBy(warnOther, []Alert{critAlert}, []InhibitRule{emptyEqual})) == 1, "")
	check("D11 多规则各记一次", len(InhibitedBy(warnSame, []Alert{critAlert},
		[]InhibitRule{rule, rule})) == 2, "")
	check("D12 无活跃源告警则不抑制", len(InhibitedBy(warnSame, nil, []InhibitRule{rule})) == 0, "")

	// ---------------------------------------------------------- E 静默
	sil := Silence{ID: "s1", Matchers: MustMatchers(`alertname="NodeDown"`, `cluster=~"c1|c2"`),
		StartsAt: 100, EndsAt: 400, CreatedBy: "alice", Comment: "维护窗口"}
	nodeC1 := Alert{Labels: lbl("alertname", "NodeDown", "cluster", "c1")}
	nodeC3 := Alert{Labels: lbl("alertname", "NodeDown", "cluster", "c3")}
	check("E1 窗口内命中", equalStrings(SilencedBy(nodeC1, []Silence{sil}, 200), []string{"s1"}), "")
	check("E2 起始时刻生效(左闭)", equalStrings(SilencedBy(nodeC1, []Silence{sil}, 100), []string{"s1"}), "")
	check("E3 结束时刻失效(右开)", len(SilencedBy(nodeC1, []Silence{sil}, 400)) == 0, "")
	check("E4 窗口过期", len(SilencedBy(nodeC1, []Silence{sil}, 500)) == 0, "")
	check("E5 正则未命中", len(SilencedBy(nodeC3, []Silence{sil}, 200)) == 0, "")
	check("E6 告警名不匹配",
		len(SilencedBy(Alert{Labels: lbl("alertname", "DiskFull", "cluster", "c1")}, []Silence{sil}, 200)) == 0, "")
	sil2 := Silence{ID: "s2", Matchers: MustMatchers(`cluster="c1"`), StartsAt: 0, EndsAt: 1000}
	check("E7 多静默同时命中",
		equalStrings(SilencedBy(nodeC1, []Silence{sil, sil2}, 200), []string{"s1", "s2"}), "")
	check("E8 无静默", len(SilencedBy(nodeC1, nil, 200)) == 0, "")

	// ---------------------------------------------------------- F 状态机
	check("F1 初始 inactive", NewAlertState().State == StateInactive, "")
	st0 := NewAlertState()
	check("F2 for=0 首次评估即 firing", st0.Evaluate(0, true, 0, 0) == StateFiring, "")
	st := NewAlertState()
	check("F3 for=10 首次为 pending", st.Evaluate(0, true, 10, 0) == StatePending, "")
	check("F4 未满 for 仍 pending", st.Evaluate(9, true, 10, 0) == StatePending, "")
	check("F5 满 for 转 firing", st.Evaluate(10, true, 10, 0) == StateFiring, "")
	check("F6 active_at 记录进入 pending 的时刻", st.HasActive && near(st.ActiveAt, 0), "")
	check("F7 firing_at 记录转 firing 的时刻", st.HasFiring && near(st.FiringAt, 10), "")
	check("F8 firing 后继续命中保持", st.Evaluate(20, true, 10, 0) == StateFiring, "")
	st2 := NewAlertState()
	st2.Evaluate(0, true, 10, 0)
	check("F9 中途不命中使计时归零",
		st2.Evaluate(5, false, 10, 0) == StateInactive && !st2.HasActive, "")
	check("F10 归零后重新计时", st2.Evaluate(6, true, 10, 0) == StatePending, "")
	check("F11 重新计时满 for 才 firing", st2.Evaluate(16, true, 10, 0) == StateFiring, "")
	st3 := NewAlertState()
	st3.Evaluate(0, true, 0, 0)
	check("F12 firing 后不命中立即 resolved", st3.Evaluate(1, false, 0, 0) == StateInactive, "")
	st4 := NewAlertState()
	st4.Evaluate(0, true, 0, 0)
	check("F13 keep_firing_for 窗口内保持 firing", st4.Evaluate(1, false, 0, 30) == StateFiring, "")
	check("F14 keep_firing_for 期内再次命中则续期", st4.Evaluate(5, true, 0, 30) == StateFiring, "")
	check("F15 超期后 resolved", st4.Evaluate(40, false, 0, 30) == StateInactive, "")
	stp := NewAlertState()
	stp.Evaluate(0, true, 10, 0)
	check("F16 pending 也视为 active", stp.Active(), "")
	v18, ok18 := stp.SampleValue()
	check("F17 活跃样本值为 1", v18 == 1 && ok18, "")
	al18, okA18 := AlertsLabels("A", stp)
	check("F18 ALERTS 序列标签含 alertstate",
		okA18 && al18["alertname"] == "A" && al18["alertstate"] == StatePending, "")
	stf := NewAlertState()
	stf.Evaluate(0, true, 0, 0)
	al19, _ := AlertsLabels("A", stf)
	check("F19 firing 时 alertstate=firing", al19["alertstate"] == StateFiring, "")
	_, ok20 := AlertsLabels("A", NewAlertState())
	check("F20 inactive 时序列 stale", !ok20, "")
	check("F21 评估计数", stf.Evaluations == 1, "")

	checkNotifications()

	if nFail > 0 {
		fmt.Printf("FAILED %d / %d\n", nFail, nFail+nPass)
		for _, s := range failures {
			fmt.Println("  - " + s)
		}
		os.Exit(1)
	}
	fmt.Printf("ALL PASS  %d assertions\n", nPass)
}
