// Package main 转写 perms.py：Backstage 权限框架的决策校验与条件求值（Go 侧）。
package main

import (
	"errors"
	"fmt"
	"strings"
)

// 三个结果字面量取自官方 d.ts：是大写字符串而不是枚举整数。
const (
	Allow       = "ALLOW"
	Deny        = "DENY"
	Conditional = "CONDITIONAL"
)

// PermissionError 表示非法权限/决策/规则。
var PermissionError = errors.New("permission error")

// Permission 对应官方 Permission / ResourcePermission。
type Permission struct {
	Name         string
	Action       string
	ResourceType string
}

var actions = []string{"create", "read", "update", "delete"}

// NewPermission 校验 action 合法性。
func NewPermission(name, action, resourceType string) (Permission, error) {
	ok := false
	for _, a := range actions {
		if a == action {
			ok = true
		}
	}
	if !ok {
		return Permission{}, fmt.Errorf("%w: bad action %q", PermissionError, action)
	}
	if name == "" {
		return Permission{}, fmt.Errorf("%w: name required", PermissionError)
	}
	return Permission{Name: name, Action: action, ResourceType: resourceType}, nil
}

// IsResourcePermission 判断是否为指定资源类型的资源权限。
func (p Permission) IsResourcePermission(resourceType string) bool {
	return p.ResourceType != "" && p.ResourceType == resourceType
}

// ValidateDecision 校验决策形状，返回错误描述列表。
func ValidateDecision(decision map[string]any) []string {
	result, _ := decision["result"].(string)
	switch result {
	case Allow, Deny:
		for _, key := range []string{"pluginId", "resourceType", "conditions"} {
			if _, ok := decision[key]; ok {
				return []string{fmt.Sprintf("definitive decision must not carry %s", key)}
			}
		}
		return nil
	case Conditional:
		var errs []string
		for _, key := range []string{"pluginId", "resourceType", "conditions"} {
			if _, ok := decision[key]; !ok {
				errs = append(errs, fmt.Sprintf("conditional decision requires %s", key))
			}
		}
		if c, ok := decision["conditions"]; ok {
			errs = append(errs, ValidateCriteria(c, 0)...)
		}
		return errs
	default:
		return []string{fmt.Sprintf("unknown result %q", result)}
	}
}

// ValidateCriteria 校验条件树：allOf/anyOf 必须是非空数组，叶子必须带 rule。
func ValidateCriteria(criteria any, depth int) []string {
	if depth > 32 {
		return []string{"criteria nested too deep"}
	}
	m, ok := criteria.(map[string]any)
	if !ok {
		return []string{"criteria must be an object"}
	}
	for _, key := range []string{"allOf", "anyOf"} {
		if raw, ok := m[key]; ok {
			children, ok := raw.([]any)
			if !ok || len(children) == 0 {
				return []string{fmt.Sprintf("%s must be a non-empty array", key)}
			}
			var errs []string
			for _, c := range children {
				errs = append(errs, ValidateCriteria(c, depth+1)...)
			}
			return errs
		}
	}
	if inner, ok := m["not"]; ok {
		return ValidateCriteria(inner, depth+1)
	}
	if rule, ok := m["rule"].(string); ok && rule != "" {
		return nil
	}
	return []string{"criteria must be one of allOf / anyOf / not / a condition with rule"}
}

// Rule 是插件注册的一条规则。
type Rule func(resource map[string]any, params map[string]any) bool

// CatalogRules 对应 catalog 插件的 isEntityOwner / hasAnnotation / isEntityKind。
var CatalogRules = map[string]Rule{
	"IS_ENTITY_OWNER": func(resource map[string]any, params map[string]any) bool {
		claims := toStringSet(params["claims"])
		if len(claims) == 0 {
			return false
		}
		for _, c := range toStringSet(resource["owners"]) {
			if claims[c] {
				return true
			}
		}
		return false
	},
	"HAS_ANNOTATION": func(resource map[string]any, params map[string]any) bool {
		key, _ := params["key"].(string)
		value, hasValue := params["value"]
		annotations, _ := resource["annotations"].(map[string]any)
		v, ok := annotations[key]
		if !ok {
			return false
		}
		if !hasValue {
			return true
		}
		return v == value
	},
	"IS_ENTITY_KIND": func(resource map[string]any, params map[string]any) bool {
		kinds := toStringSet(params["kinds"])
		kind, _ := resource["kind"].(string)
		return kinds[kind]
	},
}

func toStringSet(v any) map[string]bool {
	out := map[string]bool{}
	switch t := v.(type) {
	case []any:
		for _, x := range t {
			if s, ok := x.(string); ok {
				out[s] = true
			}
		}
	case []string:
		for _, s := range t {
			out[s] = true
		}
	}
	return out
}

// EvalCriteria 对资源求值条件树；未知规则返回错误。
func EvalCriteria(criteria map[string]any, resource map[string]any) (bool, error) {
	if raw, ok := criteria["allOf"]; ok {
		for _, c := range raw.([]any) {
			v, err := EvalCriteria(c.(map[string]any), resource)
			if err != nil {
				return false, err
			}
			if !v {
				return false, nil
			}
		}
		return true, nil
	}
	if raw, ok := criteria["anyOf"]; ok {
		for _, c := range raw.([]any) {
			v, err := EvalCriteria(c.(map[string]any), resource)
			if err != nil {
				return false, err
			}
			if v {
				return true, nil
			}
		}
		return false, nil
	}
	if inner, ok := criteria["not"]; ok {
		v, err := EvalCriteria(inner.(map[string]any), resource)
		return !v, err
	}
	rule, _ := criteria["rule"].(string)
	fn, ok := CatalogRules[rule]
	if !ok {
		return false, fmt.Errorf("%w: unknown rule %q", PermissionError, rule)
	}
	params, _ := criteria["params"].(map[string]any)
	return fn(resource, params), nil
}

// Authorize 决策 + 执行：CONDITIONAL 在此收敛成 ALLOW/DENY。
func Authorize(perm Permission, claims []string, resource map[string]any,
	policy func(Permission) map[string]any) (string, error) {
	decision := policy(perm)
	if errs := ValidateDecision(decision); len(errs) > 0 {
		return "", fmt.Errorf("%w: %s", PermissionError, strings.Join(errs, "; "))
	}
	if decision["result"] != Conditional {
		return decision["result"].(string), nil
	}
	if decision["resourceType"].(string) != perm.ResourceType {
		return "", fmt.Errorf("%w: resourceType mismatch", PermissionError)
	}
	params := map[string]any{"claims": claims}
	cond, _ := decision["conditions"].(map[string]any)
	if _, has := cond["params"]; !has {
		cond = map[string]any{"rule": cond["rule"], "params": params}
	}
	ok, err := EvalCriteria(cond, resource)
	if err != nil {
		return "", err
	}
	if ok {
		return Allow, nil
	}
	return Deny, nil
}

// OwnerOnlyPolicy 生成「只有 owner 能操作」的条件策略。
func OwnerOnlyPolicy(resourceType, pluginID string) func(Permission) map[string]any {
	return func(perm Permission) map[string]any {
		if !perm.IsResourcePermission(resourceType) {
			return map[string]any{"result": Allow}
		}
		return map[string]any{
			"result":       Conditional,
			"pluginId":     pluginID,
			"resourceType": resourceType,
			"conditions": map[string]any{
				"rule":   "IS_ENTITY_OWNER",
				"params": map[string]any{},
			},
		}
	}
}
