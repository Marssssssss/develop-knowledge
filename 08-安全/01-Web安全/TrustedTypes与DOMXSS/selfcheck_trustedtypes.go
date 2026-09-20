// TrustedTypes与DOMXSS 自检（Go 侧）。
package main

import (
	"fmt"
	"strings"
)

var okCount int
var failures []string

func ck(name string, cond bool, detail string) {
	if cond {
		okCount++
		return
	}
	failures = append(failures, fmt.Sprintf("%s  %s", name, detail))
}

func eqStr(name, got, want string) {
	ck(name, got == want, fmt.Sprintf("got=%q want=%q", got, want))
}

func raises(name string, fn func() (string, error)) {
	if _, err := fn(); err != nil {
		ck(name, true, "")
		return
	}
	ck(name, false, "没有抛 TypeError")
}

func rtt(disposition string, extra map[string]string) *Global {
	kv := map[string]string{"require-trusted-types-for": "'script'"}
	for k, v := range extra {
		kv[k] = v
	}
	return NewGlobal(NewCSP(disposition, kv))
}

func passthrough(s string, a []string) (string, bool, error) { return s, true, nil }

func main() {
	// 不强制时字符串直通
	g0 := NewGlobal()
	s0, err0 := SetInnerHTML(g0, "<b>hi</b>")
	ck("无 CSP 时 innerHTML 接受裸字符串", err0 == nil && s0 == "<b>hi</b>", s0)
	ck("无 CSP 时无任何违规报告", len(g0.Violations) == 0, "")

	// §3.4 强制模式
	g1 := rtt("enforce", nil)
	raises("强制模式下 innerHTML 拒绝裸字符串", func() (string, error) {
		return SetInnerHTML(g1, "<b>x</b>")
	})
	found := false
	for _, v := range g1.Violations {
		if v.Resource == "trusted-types-sink" {
			found = true
		}
	}
	ck("且产生一条 trusted-types-sink 违规", found, "")

	noop, _ := CreatePolicy(g1, "sanitizer",
		map[string]CreateFunc{"createHTML": passthrough})
	html, _ := CreateTrustedType(noop, "TrustedHTML", "<b>ok</b>", nil)
	s1, _ := SetInnerHTML(g1, html)
	eqStr("TrustedHTML 直接进入汇点", s1, "<b>ok</b>")

	full, _ := CreatePolicy(g1, "full", map[string]CreateFunc{
		"createHTML": passthrough, "createScript": passthrough,
		"createScriptURL": passthrough})
	scr, _ := CreateTrustedType(full, "TrustedScript", "1+1", nil)
	raises("类型错配：TrustedScript 给期望 TrustedHTML 的汇点", func() (string, error) {
		return SetInnerHTML(g1, scr)
	})
	scr2, _ := CreateTrustedType(full, "TrustedScript", "alert(1)", nil)
	s2, _ := SetScriptText(g1, scr2)
	eqStr("类型正确的 TrustedScript 走 script 文本汇点", s2, "alert(1)")

	// §4.2.4 sample 格式与截断
	g1b := rtt("enforce", nil)
	SetInnerHTML(g1b, strings.Repeat("x", 55))
	var sample string
	for _, v := range g1b.Violations {
		if v.Resource == "trusted-types-sink" {
			sample = v.Sample
		}
	}
	eqStr("sample = sink 名 + '|' + 前 40 字符", sample,
		"Element innerHTML|"+strings.Repeat("x", 40))

	// §3.5 default policy 三条分支
	sanitize := func(s string, a []string) (string, bool, error) {
		return strings.ReplaceAll(strings.ReplaceAll(s, "<script>", ""), "</script>", ""), true, nil
	}
	g2 := rtt("enforce", map[string]string{"trusted-types": "default"})
	CreatePolicy(g2, "default", map[string]CreateFunc{"createHTML": sanitize})
	s3, _ := SetInnerHTML(g2, "<script>alert(1)</script><b>ok</b>")
	eqStr("default policy 净化后的值被采用", s3, "alert(1)<b>ok</b>")

	g3 := rtt("enforce", map[string]string{"trusted-types": "default"})
	CreatePolicy(g3, "default", map[string]CreateFunc{
		"createHTML": func(s string, a []string) (string, bool, error) {
			return "", false, nil
		}})
	raises("default policy 返回 null 且强制模式 → 抛 TypeError", func() (string, error) {
		return SetInnerHTML(g3, "<img src=x onerror=alert(1)>")
	})
	found3 := false
	for _, v := range g3.Violations {
		if v.Resource == "trusted-types-sink" {
			found3 = true
		}
	}
	ck("null 返回也照样报违规（先报告再决定）", found3, "")

	g4 := rtt("report", map[string]string{"trusted-types": "default"})
	CreatePolicy(g4, "default", map[string]CreateFunc{
		"createHTML": func(s string, a []string) (string, bool, error) {
			return "", false, nil
		}})
	s4, _ := SetInnerHTML(g4, "<b>raw</b>")
	eqStr("report-only 下 default policy 拒绝 → 返回原始值", s4, "<b>raw</b>")
	hasReport := false
	for _, v := range g4.Violations {
		if v.Disposition == "report" {
			hasReport = true
		}
	}
	ck("report-only 下仍产生违规报告", hasReport, "")

	g5 := rtt("report", nil)
	s5, _ := SetInnerHTML(g5, "<b>raw</b>")
	eqStr("report-only 且无 default policy → 原始值直通", s5, "<b>raw</b>")
	ck("report-only 直通时也有报告", len(g5.Violations) > 0, "")

	// §4.2.5 policy 创建闸门
	gA := NewGlobal(NewCSP("enforce", map[string]string{"trusted-types": "one two"}))
	eqStr("白名单内的名字允许创建", ShouldPolicyCreationBeBlocked(gA, "one"), "Allowed")
	eqStr("白名单外的名字被 Blocked", ShouldPolicyCreationBeBlocked(gA, "three"), "Blocked")
	foundA := false
	for _, v := range gA.Violations {
		if v.Resource == "trusted-types-policy" {
			foundA = true
		}
	}
	ck("被 Blocked 时资源是 trusted-types-policy", foundA, "")

	gB := NewGlobal(NewCSP("enforce", map[string]string{"trusted-types": "one two"}))
	CreatePolicy(gB, "one", nil)
	_, errB := CreatePolicy(gB, "one", nil)
	ck("重名且无 allow-duplicates → TypeError", errB != nil, "")

	gC := NewGlobal(NewCSP("enforce", map[string]string{
		"trusted-types": "one two 'allow-duplicates'"}))
	CreatePolicy(gC, "one", nil)
	_, errC := CreatePolicy(gC, "one", nil)
	ck("'allow-duplicates' 允许重名", errC == nil, "")

	gD := NewGlobal(NewCSP("enforce", map[string]string{"trusted-types": "*"}))
	eqStr("'*' 允许任意新名字", ShouldPolicyCreationBeBlocked(gD, "anything"), "Allowed")
	gD.Factory.CreatedPolicyNames = []string{"anything"}
	eqStr("'*' 也不允许重名", ShouldPolicyCreationBeBlocked(gD, "anything"), "Blocked")

	gE := NewGlobal(NewCSP("enforce", map[string]string{"trusted-types": "'none'"}))
	eqStr("'none' 单独出现 → 任何 policy 都不能建",
		ShouldPolicyCreationBeBlocked(gE, "any"), "Blocked")

	gF := NewGlobal(NewCSP("enforce", map[string]string{"trusted-types": "one 'none'"}))
	eqStr("'none' 与其它名字并存时被忽略（规范 note）",
		ShouldPolicyCreationBeBlocked(gF, "one"), "Allowed")

	gG := NewGlobal(NewCSP("enforce", map[string]string{"trusted-types": ""}))
	eqStr("空指令值 → 一个 policy 都不能建",
		ShouldPolicyCreationBeBlocked(gG, "one"), "Blocked")

	gH := NewGlobal(NewCSP("enforce", map[string]string{"trusted-types": "default"}))
	CreatePolicy(gH, "default", map[string]CreateFunc{"createHTML": sanitize})
	_, errH := CreatePolicy(gH, "default", nil)
	ck("default policy 不能建第二次", errH != nil, "")

	// §3.3 回调缺失 / 异常传播
	gI := NewGlobal()
	pol, _ := CreatePolicy(gI, "html-only",
		map[string]CreateFunc{"createHTML": passthrough})
	_, errI := CreateTrustedType(pol, "TrustedScript", "1+1", nil)
	ck("未实现的 createScript 在 throwIfMissing 下抛错", errI != nil, "")
	_, okI, _ := GetPolicyValue(pol, "TrustedScript", "1+1", nil, false)
	ck("throwIfMissing=false 时返回空", !okI, "")

	pol2, _ := CreatePolicy(gI, "cdn", map[string]CreateFunc{
		"createScriptURL": func(s string, a []string) (string, bool, error) {
			return "", false, &TrustedTypeError{Msg: "invalid URL"}
		}})
	_, errI2 := CreateTrustedType(pol2, "TrustedScriptURL", "https://x/a.js", nil)
	ck("policy 回调抛出的异常传播到调用点", errI2 != nil, "")

	// §3.8 属性映射
	tn, sink, has := GetTrustedTypeDataForAttribute(NSHTML, "div", "onclick", "")
	ck("onclick → TrustedScript, sink = 'Element onclick'",
		has && tn == "TrustedScript" && sink == "Element onclick", sink)
	tn2, sink2, _ := GetTrustedTypeDataForAttribute(NSHTML, "iframe", "srcdoc", "")
	ck("iframe srcdoc → TrustedHTML",
		tn2 == "TrustedHTML" && sink2 == "HTMLIFrameElement srcdoc", sink2)
	tn3, sink3, _ := GetTrustedTypeDataForAttribute(NSHTML, "script", "src", "")
	ck("script src → TrustedScriptURL",
		tn3 == "TrustedScriptURL" && sink3 == "HTMLScriptElement src", sink3)
	tn4, sink4, _ := GetTrustedTypeDataForAttribute(NSSVG, "script", "href", NSXLink)
	ck("SVG script href(xlink) → TrustedScriptURL",
		tn4 == "TrustedScriptURL" && sink4 == "SVGScriptElement href", sink4)
	_, _, has5 := GetTrustedTypeDataForAttribute(NSHTML, "div", "id", "")
	ck("普通属性不在表里 → 不强制", !has5, "")

	gJ := rtt("enforce", nil)
	raises("强制模式下 onclick 拒绝裸字符串", func() (string, error) {
		return SetAttribute(gJ, NSHTML, "div", "onclick", "alert(1)", "")
	})
	urlPol, _ := CreatePolicy(gJ, "cdn", map[string]CreateFunc{
		"createHTML": passthrough, "createScript": passthrough,
		"createScriptURL": passthrough})
	ts, _ := CreateTrustedType(urlPol, "TrustedScript", "handle()", nil)
	s6, _ := SetAttribute(gJ, NSHTML, "div", "onclick", ts, "")
	eqStr("TrustedScript 可写入 onclick", s6, "handle()")
	s7, _ := SetAttribute(gJ, NSHTML, "div", "id", "main", "")
	eqStr("不在表里的属性即使强制也直通", s7, "main")

	gK := rtt("enforce", nil)
	raises("强制模式下 script.src 拒绝裸字符串", func() (string, error) {
		return SetScriptSrc(gK, "https://cdn.example/a.js")
	})
	raises("强制模式下 script.text 拒绝裸字符串", func() (string, error) {
		return SetScriptText(gK, "alert(1)")
	})

	// §4.2.1.1 javascript: 预导航检查
	gL := rtt("enforce", nil)
	d1, _ := RequireTTPreNavigationCheck(gL, "https://example.com/")
	eqStr("非 javascript: URL 直接 Allowed", d1, "Allowed")
	d2, _ := RequireTTPreNavigationCheck(gL, "javascript:alert(1)")
	eqStr("无 default policy 时 javascript: 导航被 Blocked", d2, "Blocked")

	gM := rtt("enforce", map[string]string{"trusted-types": "default"})
	CreatePolicy(gM, "default", map[string]CreateFunc{
		"createScript": func(s string, a []string) (string, bool, error) {
			return strings.ToUpper(s), true, nil
		}})
	d3, u3 := RequireTTPreNavigationCheck(gM, "javascript:alert(1)")
	eqStr("有 default policy 时 javascript: 导航 Allowed", d3, "Allowed")
	eqStr("且 URL 被 default policy 改写", u3, "javascript:ALERT(1)")

	// §4.2.3 两种口径
	gN := rtt("report", nil)
	ck("report-only 也算‘要求 TT’（include_report_only=true）",
		DoesSinkTypeRequireTrustedTypes(gN, "script", true), "")
	ck("但只看强制策略时不算",
		!DoesSinkTypeRequireTrustedTypes(gN, "script", false), "")

	fmt.Println("OK =", okCount)
	if len(failures) > 0 {
		fmt.Println("FAILED =", len(failures))
		for _, f := range failures {
			fmt.Println("  -", f)
		}
	} else {
		fmt.Println("ALL OK")
	}
}
