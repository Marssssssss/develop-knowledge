// 606 HATEOAS 三种表示形态对照 —— Go 版（仅标准库）。
//
// 口径来源（与 Python 版同一批实读资料）：
//   - HAL draft-kelly-json-hal-08：_links 的值是 Link Object 或数组；href REQUIRED；
//     href 为 URI Template 时 templated SHOULD 为 true；CURIE 由根资源上 rel=curies 的
//     一组 Link Object 建立，href 含 {rel} token；§8.3 hypertext cache pattern 允许
//     客户端优先读同 rel 的嵌入资源。
//   - Siren：class MUST 是字符串数组；link.rel MUST 是非空字符串数组；action.name
//     在实体内唯一；有 fields 且未给 type 时默认 application/x-www-form-urlencoded。
//   - JSON:API v1.1：成员名首尾必须是「全局允许字符」且不含保留字符（@ 仅可在首位）；
//     Content-Type 上除 ext/profile 之外的媒体类型参数 → 415；
//     sort 字段按给出顺序应用，`-` 前缀为降序。
//
// 运行：go run hateoas.go jsonapi.go（本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strings"
)

// ------------------------------------------------------------------ HAL

var uriTemplateRe = regexp.MustCompile(`\{[^{}]+\}`)

// IsURITemplate 判断 href 是否是 URI Template（§5.1）。
func IsURITemplate(href string) bool { return uriTemplateRe.MatchString(href) }

// Link 是 HAL 的 Link Object（§5）。
type Link struct {
	Href       string
	Templated  bool
	Name       string
	Type       string
	Deprecation string
}

// HalDoc 是 HAL 文档的最小表示：Links 与 Embedded 都以 rel 为键。
type HalDoc struct {
	Links    map[string][]Link
	Embedded map[string][]map[string]interface{}
	State    map[string]interface{}
}

// HasTemplateHref 报告该 rel 下是否存在未标 templated 的模板链接（§5.2 的 SHOULD）。
func (d HalDoc) HasTemplateHref(rel string) bool {
	for _, l := range d.Links[rel] {
		if IsURITemplate(l.Href) && !l.Templated {
			return true
		}
	}
	return false
}

// Curies 返回根资源上声明的 CURIE 映射（§8.2）。
func (d HalDoc) Curies() map[string]string {
	out := map[string]string{}
	for _, l := range d.Links["curies"] {
		if l.Name != "" {
			out[l.Name] = l.Href
		}
	}
	return out
}

// ExpandRel 把 `acme:widgets` 展开为完整 URI；未声明前缀时原样返回。
func (d HalDoc) ExpandRel(rel string) string {
	idx := strings.Index(rel, ":")
	if idx <= 0 {
		return rel
	}
	prefix, rest := rel[:idx], rel[idx+1:]
	if tpl, ok := d.Curies()[prefix]; ok {
		return strings.ReplaceAll(tpl, "{rel}", rest)
	}
	return rel
}

// Traverse 实现 §8.3 的 hypertext cache pattern：有嵌入就零请求，否则走链接。
func (d HalDoc) Traverse(rel string) (source string, target string, requests int) {
	if res, ok := d.Embedded[rel]; ok && len(res) > 0 {
		return "embedded", rel, 0
	}
	for key, ls := range d.Links {
		if key == rel || d.ExpandRel(key) == rel {
			for _, l := range ls {
				if IsURITemplate(l.Href) {
					continue
				}
				return "link", l.Href, 1
			}
		}
	}
	return "none", "", 0
}

// ---------------------------------------------------------------- Siren

// Action 是 Siren 的 action 对象。
type Action struct {
	Name   string
	Method string
	Href   string
	Type   string
	Fields []struct {
		Name  string
		Type  string
		Value string
	}
	HasFields bool
}

// EffectiveMethod 补上「省略 method 按 GET」的默认值。
func (a Action) EffectiveMethod() string {
	if a.Method == "" {
		return "GET"
	}
	return strings.ToUpper(a.Method)
}

// EffectiveType 只在「有 fields 且未给 type」时补表单默认值（规范给的默认值）。
func (a Action) EffectiveType() string {
	if a.Type != "" {
		return a.Type
	}
	if a.HasFields {
		return "application/x-www-form-urlencoded"
	}
	return ""
}

// FormBody 按 application/x-www-form-urlencoded 序列化：field 的 value 打底，
// 调用方传入值覆盖之，hidden 字段同样出现在请求体里。
func (a Action) FormBody(values map[string]string) string {
	if a.EffectiveType() != "application/x-www-form-urlencoded" {
		return ""
	}
	payload := map[string]string{}
	for _, f := range a.Fields {
		if f.Value != "" {
			payload[f.Name] = f.Value
		}
	}
	for k, v := range values {
		payload[k] = v
	}
	parts := []string{}
	seen := map[string]bool{}
	for _, f := range a.Fields {
		if v, ok := payload[f.Name]; ok && !seen[f.Name] {
			parts = append(parts, url.QueryEscape(f.Name)+"="+url.QueryEscape(v))
			seen[f.Name] = true
		}
	}
	keys := []string{}
	for k := range values {
		if !seen[k] {
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)
	for _, k := range keys {
		parts = append(parts, url.QueryEscape(k)+"="+url.QueryEscape(values[k]))
	}
	return strings.Join(parts, "&")
}

// ------------------------------------------------------------------ 演示

func must(cond bool, label string) {
	if !cond {
		panic("断言失败: " + label)
	}
}

func main() {
	doc := HalDoc{
		Links: map[string][]Link{
			"self":    {{Href: "/orders/523"}},
			"curies":  {{Href: "http://docs.acme.com/relations/{rel}", Name: "acme"}},
			"acme:widgets": {{Href: "/widgets"}},
		},
		State: map[string]interface{}{"total": 10.20},
	}
	must(doc.ExpandRel("acme:widgets") == "http://docs.acme.com/relations/widgets", "CURIE 展开")
	must(doc.ExpandRel("zz:widgets") == "zz:widgets", "未声明前缀不展开")
	src, tgt, n := doc.Traverse("http://docs.acme.com/relations/widgets")
	must(src == "link" && tgt == "/widgets" && n == 1, "无嵌入时走链接")

	cached := doc
	cached.Embedded = map[string][]map[string]interface{}{
		"http://docs.acme.com/relations/widgets": {{"name": "w"}},
	}
	src2, _, n2 := cached.Traverse("http://docs.acme.com/relations/widgets")
	must(src2 == "embedded" && n2 == 0, "有嵌入时零请求")
	must(doc.HasTemplateHref("self") == false, "非模板链接不报 templated 缺失")
	tpl := HalDoc{Links: map[string][]Link{"find": {{Href: "/orders{?id}"}}}}
	must(tpl.HasTemplateHref("find"), "模板链接未标 templated 应被标记")

	act := Action{Name: "add-item", Method: "POST", Href: "/orders/42/items", HasFields: true}
	act.Fields = append(act.Fields, struct {
		Name  string
		Type  string
		Value string
	}{"orderNumber", "hidden", "42"})
	act.Fields = append(act.Fields, struct {
		Name  string
		Type  string
		Value string
	}{"productCode", "text", ""})
	act.Fields = append(act.Fields, struct {
		Name  string
		Type  string
		Value string
	}{"quantity", "number", ""})
	must(act.EffectiveMethod() == "POST", "action.method")
	must(act.EffectiveType() == "application/x-www-form-urlencoded", "表单默认 Content-Type")
	body := act.FormBody(map[string]string{"productCode": "X-1", "quantity": "2"})
	must(body == "orderNumber=42&productCode=X-1&quantity=2", "Siren 表单序列化: "+body)

	must(IsLegalMemberName("first-name"), "中间连字符")
	must(!IsLegalMemberName("-first"), "连字符不得开头")
	must(!IsLegalMemberName("first.name"), "点号保留")
	must(IsLegalMemberName("@member"), "@ 可作首字符")
	must(!IsLegalMemberName("a@b"), "@ 不得在非首位")

	decision, code := NegotiateContentType("application/vnd.api+json; charset=\"utf-8\"")
	must(decision == "unsupported-param" && code == 415, "带 charset → 415")
	decision2, code2 := NegotiateContentType("application/vnd.api+json;profile=\"https://p\"")
	must(decision2 == "ok" && code2 == 200, "profile 参数允许")

	fields := ParseSort("-created,title")
	must(len(fields) == 2 && fields[0][0] == "created" && fields[0][1] == true, "sort 降序标记")
	must(fields[1][0] == "title" && fields[1][1] == false, "sort 升序默认")

	must(ValidateQueryParam("myParam"), "含大写的自定义参数合法")
	must(!ValidateQueryParam("myparam"), "纯 a-z 参数名被规范保留")
	must(!IsValidFamilyName("filter[_]"), "filter[_] 非法")

	fmt.Println("HAL ExpandRel:", doc.ExpandRel("acme:widgets"))
	fmt.Println("HAL Traverse(embedded):", src2, n2)
	fmt.Println("Siren body:", body)
	fmt.Println("JSON:API 协商:", decision, code, "/", decision2, code2)
	fmt.Println("JSON:API sort:", fields)
	fmt.Println("all go assertions passed")
}
