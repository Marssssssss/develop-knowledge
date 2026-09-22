// 608 OpenAPI 3.1 契约优先 —— Go 版（仅标准库）。
//
// 口径来源（与 Python 版同一批实读资料，spec.openapis.org/oas/v3.1.1）：
//   - §1.1：OAD MUST 至少含 paths / components / webhooks 之一。
//   - §1.2：major.minor 决定特性集，patch 只纠错（3.1.0 与 3.1.1 同一特性集）。
//   - §4.4：整数是数学定义的，1 与 1.0 都是 integer；JSON 只有六种类型。
//   - §4.4.1：format 默认是注解，不参与断言。
//   - §4.4.2：raw 二进制用 contentMediaType，encoded 用 type: string +
//     contentMediaType + contentEncoding；与 Media Type Object 的 key 矛盾时
//     contentMediaType SHALL 被忽略。
//   - §4.8.1：security 是备选，任一满足即可；空对象表示可不鉴权。
//   - §4.8.2.1：license.identifier 与 license.url 互斥。
//   - §4.8.9.1：operationId MUST 全局唯一且大小写敏感。
//   - §4.8.23：Reference Object 只允许 $ref / summary / description。
//   - §4.8.24.4.1：discriminator 的属性 MUST 是 required，且 MUST NOT 改变校验结论。
//   - §4.8.24.5：$schema（资源根）> jsonSchemaDialect > OAS 方言。
//
// 运行：go run oas31.go（本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

// OAS 方言 id（§4.8.24.5）。
const OASDialect = "https://spec.openapis.org/oas/3.1/dialect/base"

var semverRe = regexp.MustCompile(`^(\d+)\.(\d+)\.(\d+)$`)
var uriRe = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9+.\-]*:`)

// ParseVersion 解析 major.minor.patch（§1.2）。
func ParseVersion(value string) (int, int, int, bool) {
	m := semverRe.FindStringSubmatch(strings.TrimSpace(value))
	if m == nil {
		return 0, 0, 0, false
	}
	major, _ := strconv.Atoi(m[1])
	minor, _ := strconv.Atoi(m[2])
	patch, _ := strconv.Atoi(m[3])
	return major, minor, patch, true
}

// SameFeatureSet 只比 major.minor（§1.2：patch SHOULD NOT 被区分）。
func SameFeatureSet(a, b string) bool {
	am, an, _, okA := ParseVersion(a)
	bm, bn, _, okB := ParseVersion(b)
	if !okA || !okB {
		return false
	}
	return am == bm && an == bn
}

// IsURI 判定形如 URI 的字符串（§4.8.24.5）。
func IsURI(value string) bool { return uriRe.MatchString(value) }

// EffectiveDialect $schema > jsonSchemaDialect > OAS 方言。
func EffectiveDialect(jsonSchemaDialect string, schemaRootDialect string) string {
	if schemaRootDialect != "" {
		return schemaRootDialect
	}
	if jsonSchemaDialect != "" {
		return jsonSchemaDialect
	}
	return OASDialect
}

func must3(cond bool, label string) {
	if !cond {
		panic("断言失败: " + label)
	}
}

func main() {
	_, _, _, ok := ParseVersion("3.1.1")
	must3(ok, "三段版本号合法")
	_, _, _, bad := ParseVersion("3.1")
	must3(!bad, "两段版本号非法")
	must3(SameFeatureSet("3.1.0", "3.1.1"), "patch 不区分")
	must3(!SameFeatureSet("3.0.4", "3.1.1"), "minor 变化即不同特性集")

	must3(!MinimalOAD(false, false, false), "三者皆无不是合法 OAD")
	must3(MinimalOAD(true, false, false), "只有 paths 即可")

	must3(EffectiveDialect("", "") == OASDialect, "未声明时用 OAS 方言")
	must3(EffectiveDialect("urn:x", "") == "urn:x", "jsonSchemaDialect 生效")
	must3(EffectiveDialect("urn:x", "urn:y") == "urn:y", "$schema 覆盖默认值")

	must3(LicenseIssue(true, true, "https://x") == "identifier 与 url 互斥", "license 互斥")
	must3(LicenseIssue(false, true, "example.com/l") == "url 必须是 URI 形式", "license.url 是 URI")
	must3(LicenseIssue(false, true, "https://x") == "", "只用 url 合法")

	okIDs, dupID := UniqueStrings([]string{"list", "List"})
	must3(okIDs, "operationId 大小写敏感（list ≠ List）")
	_, dupID2 := UniqueStrings([]string{"same", "same"})
	must3(dupID2 == "same", "operationId 重复应报出重复值")
	must3(dupID == "", "无重复时重复值为空")

	must3(len(ReferenceExtraMembers([]string{"$ref", "x-foo"})) == 1,
		"Reference Object 的额外成员被忽略")
	must3(len(ReferenceExtraMembers([]string{"$ref", "summary", "description"})) == 0,
		"三个合法成员不被忽略")

	scopes := map[string]map[string]bool{
		"oauth":  {"read": true, "write": true},
		"apiKey": {},
	}
	satisfied, which := SecuritySatisfied(
		[]map[string][]string{{"oauth": {"admin"}}, {"apiKey": {}}},
		func(s string) map[string]bool { return scopes[s] })
	must3(satisfied && which == "apiKey", "security 只需满足其一")
	_, bad2 := SecuritySatisfied([]map[string][]string{{"oauth": {"admin"}}},
		func(s string) map[string]bool { return scopes[s] })
	must3(!bad2, "scope 不足即不满足")
	opt, _ := SecuritySatisfied([]map[string][]string{{}}, func(s string) map[string]bool { return nil })
	must3(opt, "空对象表示可不鉴权")

	must3(IsInteger(1) && IsInteger(float64(1)), "1 与 1.0 都是整数")
	must3(!IsInteger(true), "true 不是整数")
	must3(!IsNumber(true), "true 不是 number")
	must3(TypeMatches(nil, "null"), "null")
	must3(TypeOK(nil, []string{"string", "null"}), "type 数组表达可空")
	must3(!TypeOK(float64(3), []string{"string", "null"}), "type 数组全不命中")

	must3(BinarySchema("image/png", false)["contentMediaType"] == "image/png", "raw 二进制")
	must3(BinarySchema("image/png", true)["contentEncoding"] == "base64", "encoded 二进制")
	must3(ContentMediaTypeEffective("image/png", "application/json") == "", "矛盾时被忽略")
	must3(ContentMediaTypeEffective("image/png", "image/png") == "image/png", "一致时保留")

	issues := DiscriminatorIssues("petType", []string{"petType"}, true, false)
	must3(len(issues) == 0, "属性是 required 时合法")
	must3(len(DiscriminatorIssues("petType", nil, true, false)) == 1, "非 required 应报错")
	must3(len(DiscriminatorIssues("petType", []string{"petType"}, false, false)) == 1,
		"脱离 oneOf/anyOf 应报错")

	fmt.Println("dialect:", EffectiveDialect("", ""))
	fmt.Println("binary encoded:", BinarySchema("image/png", true))
	fmt.Println("all go assertions passed")
}
