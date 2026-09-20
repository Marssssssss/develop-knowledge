// CSP strict-dynamic与违规报告 自检（Go 侧）。
package main

import (
	"crypto/sha256"
	"encoding/base64"
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

const origin = "https://example.com"

func main() {
	// §6.7.3.2
	ck("只看 'unsafe-inline' → script 允许全部内联",
		AllowsAllInline([]string{"'unsafe-inline'"}, "script"), "")
	ck("'unsafe-inline' 'strict-dynamic' 在 script 下不允许全部内联",
		!AllowsAllInline([]string{"'unsafe-inline'", "'strict-dynamic'"}, "script"), "")
	ck("'strict-dynamic' 'unsafe-inline' 在 script 下同样不允许",
		!AllowsAllInline([]string{"'strict-dynamic'", "'unsafe-inline'"}, "script"), "")
	ck("同一列表对 style 仍允许全部内联",
		AllowsAllInline([]string{"'unsafe-inline'", "'strict-dynamic'"}, "style"), "")
	ck("同一列表对 'script attribute' 不允许",
		!AllowsAllInline([]string{"'unsafe-inline'", "'strict-dynamic'"},
			"script attribute"), "")
	ck("host-source 'strict-dynamic' 在 script 下不允许",
		!AllowsAllInline([]string{"http://example.com", "'strict-dynamic'"}, "script"), "")
	ck("nonce-source 出现即不允许全部内联",
		!AllowsAllInline([]string{"'unsafe-inline'", "'nonce-abc'"}, "script"), "")
	ck("hash-source 出现即不允许全部内联",
		!AllowsAllInline([]string{"'unsafe-inline'", "'sha256-abc'"}, "script"), "")
	ck("只有 'unsafe-inline' 时 style 也允许",
		AllowsAllInline([]string{"'unsafe-inline'"}, "style"), "")

	// §6.7.3.3
	nonce := "DhcnhD3khTMePgXwdayK9BsMqXjhguVV"
	dyn := []string{"'nonce-" + nonce + "'", "'strict-dynamic'"}
	ck("parser-inserted 且 nonce 匹配 → Matches",
		DoesElementMatch(NewElement(nonce, true), dyn, "script", ""), "")
	ck("nonce 不匹配且 parser-inserted → Does Not Match",
		!DoesElementMatch(NewElement("wrong", true), dyn, "script", ""), "")
	ck("非 parser-inserted 的 script 元素被 strict-dynamic 放行",
		DoesElementMatch(NewElement("", false), dyn, "script", ""), "")
	ck("document.write 产生的（parser-inserted）元素被 strict-dynamic 拒绝",
		!DoesElementMatch(NewElement("", true), dyn, "script", ""), "")
	ck("type 为 style 时 strict-dynamic 不起作用",
		!DoesElementMatch(NewElement("", false), dyn, "style", ""), "")

	src := "alert(1);"
	sum := sha256.Sum256([]byte(src))
	dig := base64.StdEncoding.EncodeToString(sum[:])
	urlsafe := strings.NewReplacer("+", "-", "/", "_").Replace(dig)
	hashList := []string{"'sha256-" + urlsafe + "'"}
	ck("base64url 写的 hash 也能匹配（'-'→'+' '_'→'/' 归一化）",
		DoesElementMatch(NewElement("", true), hashList, "script", src), "")
	unpadded := strings.TrimRight(urlsafe, "=")
	ck("口径：去 padding 的写法在本模型下不匹配",
		!DoesElementMatch(NewElement("", true),
			[]string{"'sha256-" + unpadded + "'"}, "script", src), "")
	ck("hash 不匹配 → Does Not Match",
		!DoesElementMatch(NewElement("", true), hashList, "script", "alert(2);"), "")
	ck("事件处理器属性不吃 nonce（规范 note）",
		!DoesElementMatch(NewElement(nonce, true), dyn, "script attribute", ""), "")

	// §6.7.1.1
	eqStr("strict-dynamic + 非 parser-inserted 请求 → Allowed",
		ScriptPreRequestCheck(ScriptRequest{URL: "https://cdn.example/a.js",
			ParserMetadata: "not-parser-inserted"}, dyn, origin), "Allowed")
	eqStr("strict-dynamic + parser-inserted 请求 → Blocked",
		ScriptPreRequestCheck(ScriptRequest{URL: "https://cdn.example/a.js",
			ParserMetadata: "parser-inserted"}, dyn, origin), "Blocked")
	eqStr("host-source 在没有 strict-dynamic 时正常工作",
		ScriptPreRequestCheck(ScriptRequest{URL: "https://cdn.example/a.js",
			ParserMetadata: "not-parser-inserted"},
			[]string{"https://cdn.example"}, origin), "Allowed")
	eqStr("strict-dynamic 下 host-source 被忽略",
		ScriptPreRequestCheck(ScriptRequest{URL: "https://evil.example/a.js",
			ParserMetadata: "not-parser-inserted"}, dyn, origin), "Allowed")
	eqStr("'none' 单独出现 → Blocked",
		ScriptPreRequestCheck(ScriptRequest{URL: "https://cdn.example/a.js"},
			[]string{"'none'"}, origin), "Blocked")
	eqStr("空源列表等价于 'none' → Blocked",
		ScriptPreRequestCheck(ScriptRequest{URL: "https://cdn.example/a.js"},
			nil, origin), "Blocked")

	// §6.7.2.6
	ck("'none' 与其它表达式并存时不起作用",
		DoesURLMatchSourceList("https://example.com/a.js",
			[]string{"'none'", "https://example.com"}, origin), "")
	ck("只有 'none' 一项 → 不匹配",
		!DoesURLMatchSourceList("https://example.com/a.js",
			[]string{"'none'"}, origin), "")
	ck("空列表 → 不匹配",
		!DoesURLMatchSourceList("https://example.com/a.js", nil, origin), "")
	ck("'self' 匹配本站",
		DoesURLMatchSourceList("https://example.com/a.js", []string{"'self'"}, origin), "")
	ck("'self' 不匹配他站",
		!DoesURLMatchSourceList("https://evil.example/a.js", []string{"'self'"}, origin), "")
	ck("scheme-source 'https:' 匹配任意 https 源",
		DoesURLMatchSourceList("https://cdn.example/a.js", []string{"https:"}, origin), "")

	// §2.4 sample 与报告体
	g := &Global{DocumentURL: "https://example.com/page", StatusCode: 200,
		SourceFile: "https://example.com/page", LineNumber: 7, ColumnNumber: 13}
	pol := &Policy{Serialized: "script-src 'nonce-abc' 'strict-dynamic'",
		Disposition: "enforce", Directives: map[string]string{
			"script-src": "'nonce-abc' 'strict-dynamic'", "report-to": "csp-endpoint"}}
	v := CreateViolation(g, pol, "script-src", strings.Repeat("y", 90), "")
	eqStr("sample 截断到前 40 字符", v.Sample, strings.Repeat("y", 40))
	eqStr("disposition 为 enforce", v.Disposition, "enforce")
	body := v.AsReportBody()
	ck("报告体带 sourceFile / lineNumber / columnNumber",
		body["sourceFile"] == "https://example.com/page" &&
			body["lineNumber"] == 7 && body["columnNumber"] == 13, fmt.Sprint(body))

	ext := CreateViolation(g, pol, "script-src", "", "https://cdn.example/a.js")
	eqStr("外部文件违规不带 sample", ext.Sample, "")
	ck("外部文件违规带 blockedURL", ext.AsReportBody()["blockedURL"] ==
		"https://cdn.example/a.js", "")

	// §1 report-uri 弃用
	ck("report-uri 被标记弃用", len(DeprecatedDirectives) > 0 &&
		DeprecatedDirectives[0] == "report-uri", "")
	eqStr("report-to 端点被识别", pol.ReportingEndpoints()[0], "csp-endpoint")
	pol2 := &Policy{Serialized: "script-src 'self'; report-uri /r", Disposition: "enforce",
		Directives: map[string]string{"script-src": "'self'", "report-uri": "/r"}}
	eqStr("report-uri 标注为 deprecated", pol2.ReportingEndpoints()[0], "deprecated")

	// §8.5 Strict CSP
	strict := &Policy{Serialized: "s", Disposition: "enforce",
		Directives: map[string]string{
			"script-src": "'strict-dynamic' 'nonce-X'", "base-uri": "'self'"}}
	ck("nonce + strict-dynamic + base-uri 'self' 是 Strict CSP", IsStrictCSP(strict), "")
	h := &Policy{Serialized: "s", Disposition: "enforce", Directives: map[string]string{
		"script-src": "'strict-dynamic' 'sha256-abc'", "base-uri": "'none'"}}
	ck("hash + strict-dynamic + base-uri 'none' 也是 Strict CSP", IsStrictCSP(h), "")
	ns := &Policy{Serialized: "s", Disposition: "enforce", Directives: map[string]string{
		"script-src": "'strict-dynamic' 'nonce-X' https://cdn.example",
		"base-uri":   "'self'"}}
	ck("混了 host-source 就不是 Strict CSP", !IsStrictCSP(ns), "")
	nb := &Policy{Serialized: "s", Disposition: "enforce",
		Directives: map[string]string{"script-src": "'strict-dynamic' 'nonce-X'"}}
	ck("缺 base-uri 不是 Strict CSP", !IsStrictCSP(nb), "")
	nd := &Policy{Serialized: "s", Disposition: "enforce",
		Directives: map[string]string{"script-src": "'nonce-X'", "base-uri": "'self'"}}
	ck("只有 nonce 没有 strict-dynamic 不是 Strict CSP", !IsStrictCSP(nd), "")

	// §8.2 原文警告的可观测后果
	ck("strict-dynamic 下运行时脚本 URL 可被控制 → 任意脚本被放行",
		ScriptPreRequestCheck(ScriptRequest{URL: "https://attacker.example/x.js",
			ParserMetadata: "not-parser-inserted"}, dyn, origin) == "Allowed", "")

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
