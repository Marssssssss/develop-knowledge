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

// IsInteger §4.4：整数按数学定义，1 与 1.0 都是整数；bool 不是。
func IsInteger(v interface{}) bool {
	switch x := v.(type) {
	case bool:
		return false
	case int:
		return true
	case int64:
		return true
	case float64:
		return x == float64(int64(x))
	}
	return false
}

// IsNumber number 含整数与浮点；bool 不是 number。
func IsNumber(v interface{}) bool {
	switch x := v.(type) {
	case bool:
		return false
	case int, int64, float64:
		return true
	}
	return false
}

// TypeMatches 六种 JSON 类型加 integer 别名（§4.4）。
func TypeMatches(v interface{}, typ string) bool {
	switch typ {
	case "null":
		return v == nil
	case "boolean":
		_, ok := v.(bool)
		return ok
	case "object":
		_, ok := v.(map[string]interface{})
		return ok
	case "array":
		_, ok := v.([]interface{})
		return ok
	case "string":
		_, ok := v.(string)
		return ok
	case "integer":
		return IsInteger(v)
	case "number":
		return IsNumber(v)
	}
	return false
}

// TypeOK type 可以是单值也可以是数组（3.1 用 type 数组表达可空）。
func TypeOK(v interface{}, declared []string) bool {
	for _, t := range declared {
		if TypeMatches(v, t) {
			return true
		}
	}
	return false
}

// MinimalOAD 判定是否满足 §1.1 的下限。
func MinimalOAD(hasPaths, hasComponents, hasWebhooks bool) bool {
	return hasPaths || hasComponents || hasWebhooks
}

// LicenseIssue §4.8.2.1：identifier 与 url 互斥，url 必须是 URI。
func LicenseIssue(hasIdentifier, hasURL bool, url string) string {
	if hasIdentifier && hasURL {
		return "identifier 与 url 互斥"
	}
	if hasURL && !IsURI(url) {
		return "url 必须是 URI 形式"
	}
	return ""
}

// UniqueStrings 判定字符串列表是否无重复（operationId / tag 名都是大小写敏感）。
func UniqueStrings(values []string) (bool, string) {
	seen := map[string]bool{}
	for _, v := range values {
		if seen[v] {
			return false, v
		}
		seen[v] = true
	}
	return true, ""
}

// ReferenceExtraMembers §4.8.23：除 $ref / summary / description 外一律忽略。
func ReferenceExtraMembers(members []string) []string {
	out := []string{}
	for _, m := range members {
		if m == "$ref" || m == "summary" || m == "description" {
			continue
		}
		out = append(out, m)
	}
	return out
}

// SecuritySatisfied §4.8.1：security 是备选，任一满足即可；空对象表示可不鉴权。
func SecuritySatisfied(requirements []map[string][]string,
	have func(scheme string) map[string]bool) (bool, string) {
	for _, req := range requirements {
		if len(req) == 0 {
			return true, "empty requirement"
		}
		ok := true
		for scheme, scopes := range req {
			got := have(scheme)
			if got == nil {
				ok = false
				break
			}
			for _, s := range scopes {
				if !got[s] {
					ok = false
					break
				}
			}
			if !ok {
				break
			}
		}
		if ok {
			for scheme := range req {
				return true, scheme
			}
		}
	}
	return false, ""
}

// BinarySchema §4.4.2：raw 与 encoded 两种二进制描述。
func BinarySchema(mediaType string, encoded bool) map[string]string {
	if encoded {
		return map[string]string{"type": "string", "contentMediaType": mediaType,
			"contentEncoding": "base64"}
	}
	return map[string]string{"contentMediaType": mediaType}
}

// ContentMediaTypeEffective 与 Media Type Object 的 key 矛盾时 SHALL 被忽略。
func ContentMediaTypeEffective(declared, mediaTypeObjectKey string) string {
	if mediaTypeObjectKey == "" || declared == "" {
		return declared
	}
	if declared != mediaTypeObjectKey {
		return ""
	}
	return declared
}

// DiscriminatorIssues §4.8.24.4.1：propertyName 指向的属性 MUST 是 required。
func DiscriminatorIssues(propertyName string, required []string,
	hasOneOf, hasAnyOf bool) []string {
	out := []string{}
	if propertyName == "" {
		return append(out, "缺 propertyName")
	}
	found := false
	for _, r := range required {
		if r == propertyName {
			found = true
			break
		}
	}
	if !found {
		out = append(out, "discriminator 属性 MUST 是 required 字段")
	}
	if !hasOneOf && !hasAnyOf {
		out = append(out, "discriminator 脱离 oneOf/anyOf 时规范未定义")
	}
	return out
}
