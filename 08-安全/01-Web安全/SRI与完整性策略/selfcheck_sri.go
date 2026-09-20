// SRI与完整性策略 自检（Go 侧）。
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

func eqInt(name string, got, want int) {
	ck(name, got == want, fmt.Sprintf("got=%d want=%d", got, want))
}

func main() {
	payload := []byte("alert('Hello, world.');")

	// 官方向量（§3.1 / §3.2.1）
	eqStr("sha384 官方摘要复现", DigestOf(payload, "sha384"),
		"H8BRh8j48O9oYatfu5AZzq6A9RINhZO5H16dQZngK7T62em8MUt1FLm52t+eX6xO")
	eqStr("sha512 官方摘要复现", DigestOf(payload, "sha512"),
		"Q2bFTOhEALkN8hOms2FKTDLy7eugP2zFZ1T8LCvX42Fp3WoNr3bjZSAHeOsHrbV1Fu9/A0EzCinRE7Af1ofPrw==")
	ck("sha384 官方值含 '+'（标准 base64 而非 base64url）",
		strings.Contains(DigestOf(payload, "sha384"), "+"), "")
	ck("sha512 官方值以 '==' 结尾（保留 padding）",
		strings.HasSuffix(DigestOf(payload, "sha512"), "=="), "")
	eqStr("三算法有序 = sha256 < sha384 < sha512",
		strings.Join(ValidAlgos, ","), "sha256,sha384,sha512")

	// §3.3.2
	p := ParseMetadata("sha384-AAA?opt=1 sha512-BBB md5-CCC")
	eqInt("不认识的算法被跳过", len(p), 2)
	eqStr("第 1 项算法", p[0].Alg, "sha384")
	eqStr("第 1 项取值去掉了 ? 选项", p[0].Val, "AAA")
	eqStr("第 2 项算法", p[1].Alg, "sha512")
	eqInt("空字符串解析为空集", len(ParseMetadata("")), 0)
	eqInt("纯空白也解析为空集", len(ParseMetadata("   ")), 0)
	eqStr("缺 base64 部分时 val 为空串", ParseMetadata("sha256-")[0].Val, "")
	eqStr("连字符后只切第一段", ParseMetadata("sha256-X")[0].Val, "X")

	// §3.3.3
	strong := GetStrongestMetadata([]Metadata{{"sha256", "a"}, {"sha512", "c"},
		{"sha384", "b"}})
	eqInt("强弱混合时只留最强的", len(strong), 1)
	eqStr("最强那批的算法", strong[0].Alg, "sha512")
	eqStr("最强那批的取值", strong[0].Val, "c")
	two := GetStrongestMetadata([]Metadata{{"sha384", "a"}, {"sha384", "b"}})
	eqInt("同强度的两份都保留", len(two), 2)
	three := GetStrongestMetadata([]Metadata{{"sha512", "a"}, {"sha256", "b"},
		{"sha512", "c"}})
	eqInt("顺序不影响：两份 sha512 都留下", len(three), 2)

	// §3.3.4
	good384 := IntegrityMetadata(payload, "sha384")
	good512 := IntegrityMetadata(payload, "sha512")
	bad384 := "sha384-" + strings.Repeat("Z", 64)
	bad512 := "sha512-" + strings.Repeat("Z", 88)
	ck("正确的 sha384 通过", DoBytesMatch(payload, good384), "")
	ck("错误的 sha384 不通过", !DoBytesMatch(payload, bad384), "")
	ck("没有 integrity 元数据 → 直接通过", DoBytesMatch(payload, ""), "")
	ck("官方双 sha384 例子：命中任意一份即通过",
		DoBytesMatch(payload, bad384+" "+good384), "")
	ck("弱的对 + 强的错 → 失败", !DoBytesMatch(payload, good384+" "+bad512), "")
	ck("弱的错 + 强的对 → 通过", DoBytesMatch(payload, bad384+" "+good512), "")
	last := good384[len(good384)-1:]
	flip := "A"
	if last == "A" {
		flip = "B"
	}
	ck("大小写敏感匹配", !DoBytesMatch(payload, good384[:len(good384)-1]+flip), "")

	// §3.3.4 note：SRI 需要 CORS
	okA, whyA := VerifySubresource(payload, good384, "https://cdn.example",
		"https://example.com", true)
	ck("跨源 + crossorigin → 通过", okA, whyA)
	okB, _ := VerifySubresource(payload, good384, "https://cdn.example",
		"https://example.com", false)
	ck("跨源 + 无 crossorigin → 失败（SRI 需要 CORS）", !okB, "")
	okC, _ := VerifySubresource(payload, good384, "https://example.com",
		"https://example.com", false)
	ck("同源 + 无 crossorigin → 通过", okC, "")

	// §3.8
	pol := ProcessIntegrityPolicy(map[string][]string{
		"blocked-destinations": {"script"},
		"endpoints":            {"integrity-endpoint"}})
	eqStr("sources 缺省即 inline", strings.Join(pol.Sources, ","), "inline")
	eqStr("blocked-destinations 解析", strings.Join(pol.BlockedDestinations, ","), "script")
	eqStr("endpoints 解析", strings.Join(pol.Endpoints, ","), "integrity-endpoint")
	polSty := ProcessIntegrityPolicy(map[string][]string{
		"sources": {"inline"}, "blocked-destinations": {"script", "style"}})
	eqStr("两个 destination 都能收", strings.Join(polSty.BlockedDestinations, ","),
		"script,style")
	emptyPol := ProcessIntegrityPolicy(map[string][]string{})
	ck("空字典 → sources 仍 inline 但无 blocked destinations",
		len(emptyPol.Sources) == 1 && len(emptyPol.BlockedDestinations) == 0, "")

	// §3.8.2
	container := &PolicyContainer{IntegrityPolicy: pol,
		ReportOnlyIntegrityPolicy: &IntegrityPolicy{}}
	var v []IntegrityViolation
	ext := IntegrityRequest{URL: "https://cdn.example/a.js", Destination: "script",
		Mode: "no-cors"}
	eqStr("外部脚本无 integrity 且被策略点名 → Blocked",
		ShouldRequestBeBlocked(ext, container, "https://example.com/", &v), "Blocked")
	eqInt("并产生一条违规", len(v), 1)
	eqStr("违规的 blockedURL", v[0].BlockedURL, "https://cdn.example/a.js")

	withInt := IntegrityRequest{URL: "https://cdn.example/a.js",
		Destination: "script", Mode: "cors", Integrity: good384}
	eqStr("带 integrity 且 mode=cors → Allowed",
		ShouldRequestBeBlocked(withInt, container, "https://example.com/", &v), "Allowed")

	noCors := IntegrityRequest{URL: "https://cdn.example/a.js",
		Destination: "script", Mode: "no-cors", Integrity: good384}
	eqStr("有 integrity 但 mode=no-cors → 不被豁免，仍 Blocked",
		ShouldRequestBeBlocked(noCors, container, "https://example.com/", &v), "Blocked")

	styleReq := IntegrityRequest{URL: "https://cdn.example/a.css",
		Destination: "style", Mode: "no-cors"}
	eqStr("destination 不在 blocked-destinations → Allowed",
		ShouldRequestBeBlocked(styleReq, container, "https://example.com/", &v), "Allowed")

	localReq := IntegrityRequest{URL: "data:text/javascript,1",
		Destination: "script", Mode: "no-cors", Local: true}
	eqStr("local URL → Allowed",
		ShouldRequestBeBlocked(localReq, container, "https://example.com/", &v), "Allowed")

	emptyContainer := &PolicyContainer{IntegrityPolicy: &IntegrityPolicy{},
		ReportOnlyIntegrityPolicy: &IntegrityPolicy{}}
	eqStr("两个策略都空 → Allowed",
		ShouldRequestBeBlocked(ext, emptyContainer, "https://example.com/", &v), "Allowed")

	ro := &PolicyContainer{IntegrityPolicy: &IntegrityPolicy{},
		ReportOnlyIntegrityPolicy: pol}
	var v2 []IntegrityViolation
	eqStr("report-only 命中 → 不阻断",
		ShouldRequestBeBlocked(ext, ro, "https://example.com/", &v2), "Allowed")
	eqInt("report-only 仍产生违规", len(v2), 1)
	ck("违规带 reportOnly=true", len(v2) > 0 && v2[0].ReportOnly, "")

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
