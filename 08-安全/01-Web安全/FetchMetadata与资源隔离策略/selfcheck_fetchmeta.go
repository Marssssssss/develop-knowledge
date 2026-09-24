package main

// FetchMetadata与资源隔离策略 自检（与 selfcheck_fetchmeta.py 同一套断言）。
// 依据 https://w3c.github.io/webappsec-fetch-metadata/ 全文实读。

import "fmt"

var okCount int
var failed []string

func ck(name string, cond bool, detail string) {
	if cond {
		okCount++
	} else {
		failed = append(failed, name+" "+detail)
	}
}

func eq(name string, got, want interface{}) {
	ck(name, fmt.Sprintf("%v", got) == fmt.Sprintf("%v", want),
		fmt.Sprintf("got=%v want=%v", got, want))
}

func ex() Origin  { return Origin{"https", "example.com", 0} }
func sub() Origin { return Origin{"https", "subdomain.example.com", 0} }
func net() Origin { return Origin{"https", "example.net", 0} }

func siteOf(urls []Origin, nav, userInit bool) string {
	v, _ := SetSecFetchSite(Req{Origin: ex(), URLList: urls,
		Navigation: nav, UserInitiated: userInit})
	return v
}

func main() {
	// registrable domain
	eq("apex 的 RD", RegistrableDomain("example.com"), "example.com")
	eq("子域的 RD 是 apex", RegistrableDomain("subdomain.example.com"), "example.com")
	eq("多级后缀 co.uk", RegistrableDomain("a.b.co.uk"), "b.co.uk")
	eq("单标签主机名", RegistrableDomain("localhost"), "localhost")

	// same origin / same site
	ck("同源", ex().SameOrigin(Origin{"https", "example.com", 0}), "")
	ck("显式 443 与默认 443 同源", ex().SameOrigin(Origin{"https", "example.com", 443}), "")
	ck("端口不同不同源", !ex().SameOrigin(Origin{"https", "example.com", 8443}), "")
	ck("scheme 不同不同源", !ex().SameOrigin(Origin{"http", "example.com", 0}), "")
	ck("子域同站", ex().SameSite(sub()), "")
	ck("不同 registrable 不同站", !ex().SameSite(net()), "")

	// §2.3
	eq("空 url list → same-origin", siteOf(nil, false, false), "same-origin")
	eq("单一同源 url", siteOf([]Origin{ex()}, false, false), "same-origin")
	eq("单一同站跨源 url → same-site", siteOf([]Origin{sub()}, false, false), "same-site")
	eq("单一跨站 url → cross-site", siteOf([]Origin{net()}, false, false), "cross-site")
	eq("用户显式触发的导航 → none", siteOf([]Origin{ex()}, true, true), "none")
	eq("导航但非用户触发仍走遍历", siteOf([]Origin{ex()}, true, false), "same-origin")
	eq("用户触发优先于跨站 url", siteOf([]Origin{net()}, true, true), "none")

	// §4.1 重定向链
	eq("链 (apex, 子域) → same-site",
		siteOf([]Origin{ex(), sub()}, false, false), "same-site")
	eq("链 (apex, 子域, 异站) → cross-site",
		siteOf([]Origin{ex(), sub(), net()}, false, false), "cross-site")
	eq("链末尾绕回 apex 仍是 cross-site",
		siteOf([]Origin{ex(), sub(), net(), ex()}, false, false), "cross-site")
	v, n := SetSecFetchSite(Req{Origin: ex(), URLList: []Origin{ex(), sub(), net(), ex()}})
	eq("绕回时只检查了 3 个 url", fmt.Sprintf("%s/%d", v, n), "cross-site/3")
	eq("链 (子域, 异站, 子域) 在异站处 break",
		siteOf([]Origin{sub(), net(), sub()}, false, false), "cross-site")

	// §2.4
	eq("非导航请求不发 User",
		SetSecFetchUser(Req{Navigation: false, UserActivation: true}), "")
	eq("导航但无激活不发 User",
		SetSecFetchUser(Req{Navigation: true, UserActivation: false}), "")
	eq("导航且有激活发 ?1",
		SetSecFetchUser(Req{Navigation: true, UserActivation: true}), "?1")

	// §3
	eq("非可信 URL 一个头都不发",
		len(AppendFetchMetadata(Req{Origin: ex(), URLList: []Origin{ex()}})), 0)
	nav4 := AppendFetchMetadata(Req{Origin: ex(), URLList: []Origin{ex()},
		Dest: "document", Mode: "navigate", Navigation: true,
		UserActivation: true, Trustworthy: true})
	eq("顶层导航 Dest", nav4["Sec-Fetch-Dest"], "document")
	eq("顶层导航 Mode", nav4["Sec-Fetch-Mode"], "navigate")
	eq("顶层导航 Site", nav4["Sec-Fetch-Site"], "same-origin")
	eq("顶层导航 User", nav4["Sec-Fetch-User"], "?1")
	img := AppendFetchMetadata(Req{Origin: ex(), URLList: []Origin{net()},
		Dest: "image", Mode: "no-cors", Trustworthy: true})
	eq("跨站 img 的 Site", img["Sec-Fetch-Site"], "cross-site")
	_, hasUser := img["Sec-Fetch-User"]
	ck("子资源请求不带 User", !hasUser, "")

	// §2.2/§2.3 非法值忽略
	eq("非法 Mode 被忽略", len(NormalizeIncoming(map[string]string{
		"Sec-Fetch-Mode": "weird", "Sec-Fetch-Site": "same-origin"})), 1)
	eq("Dest 原样透传", NormalizeIncoming(map[string]string{
		"Sec-Fetch-Dest": "brand-new-thing"})["dest"], "brand-new-thing")

	// §4.2
	ck("Sec-Fetch-Site 不可伪造", IsForbiddenResponseHeaderName("Sec-Fetch-Site"), "")
	ck("前缀大小写不敏感", IsForbiddenResponseHeaderName("sec-fetch-site"), "")
	ck("普通头不禁止", !IsForbiddenResponseHeaderName("X-Custom"), "")
	hdr := map[string]string{}
	ck("JS 写 Sec-Fetch-Site 失败",
		!JSSetHeader(hdr, "Sec-Fetch-Site", "same-origin"), "")
	eq("伪造未落进头部", len(hdr), 0)

	// 资源隔离策略（工程惯例）
	eq("无元数据 fail-open", IsolationPolicy(map[string]string{}), "allow")
	eq("顶层导航跨站拦截", IsolationPolicy(map[string]string{
		"Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate",
		"Sec-Fetch-Site": "cross-site"}), "block")
	eq("子资源同站放行", IsolationPolicy(map[string]string{
		"Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
		"Sec-Fetch-Site": "same-site"}), "allow")

	fmt.Println("OK =", okCount)
	if len(failed) > 0 {
		fmt.Println("FAILED =", len(failed))
		for _, s := range failed {
			fmt.Println("  -", s)
		}
	} else {
		fmt.Println("ALL OK")
	}
}
