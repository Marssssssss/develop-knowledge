// Package main 实现 W3C Trusted Types 最小模型 —— 注入汇点只接受类型化值。
//
// 依据 W3C Trusted Types 规范（https://www.w3.org/TR/trusted-types/ 全文实读）：
//   - §2.3 类型对象只能由 policy 创建，构造函数不暴露。
//   - §3.1 建 policy 先过 §4.2.5 的 CSP 闸门；"default" 只能建一次。
//   - §3.3 类型→回调映射：TrustedHTML→createHTML / TrustedScript→createScript /
//     TrustedScriptURL→createScriptURL；回调缺失且 throwIfMissing 时抛 TypeError。
//   - §3.4 核心：已是期望类型→直通；该 sink 组不要求 TT→直通；否则走 default
//     policy；default policy 产出 null → 报违规，强制模式抛错、report-only
//     模式返回原始值。
//   - §3.8 属性表：on* 事件处理器→TrustedScript（sink = "Element "+属性名）、
//     iframe srcdoc→TrustedHTML、script src / SVG script href→TrustedScriptURL。
//   - §4.2.4 违规 sample = sink + "|" + source 前 40 字符，
//     resource = "trusted-types-sink"。
//   - §4.2.5 policy 创建违规 resource = "trusted-types-policy"；'none' 与其它值
//     并存时被忽略；'allow-duplicates' 允许重名；'*' 是通配符。
//   - §4.2.1.1 javascript: 导航的 pre-navigation check。
package main

import "strings"

// 命名空间常量。
const (
	NSHTML   = "http://www.w3.org/1999/xhtml"
	NSSVG    = "http://www.w3.org/2000/svg"
	NSMathML = "http://www.w3.org/1998/Math/MathML"
	NSXLink  = "http://www.w3.org/1999/xlink"
)

// FunctionName 是类型名到 create* 回调名的映射（§3.3 的表）。
var FunctionName = map[string]string{
	"TrustedHTML": "createHTML", "TrustedScript": "createScript",
	"TrustedScriptURL": "createScriptURL",
}

// TrustedTypeError 模拟规范里的 TypeError。
type TrustedTypeError struct{ Msg string }

func (e *TrustedTypeError) Error() string { return e.Msg }

// TrustedType 是 TrustedHTML / TrustedScript / TrustedScriptURL 的统一表示。
type TrustedType struct {
	TypeName string
	Data     string
}

func stringify(v interface{}) string {
	if t, ok := v.(*TrustedType); ok {
		return t.Data
	}
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

// CreateFunc 是 policy 的 create* 回调。返回的 bool 表示是否产出了值
// （false 对应 JS 的 null/undefined），error 对应回调抛出的异常。
type CreateFunc func(value string, args []string) (string, bool, error)

// Policy 是用户定义的不可变策略。
type Policy struct {
	Name    string
	Options map[string]CreateFunc
}

// Factory 是 trustedTypes 对象。
type Factory struct {
	CreatedPolicyNames []string
	DefaultPolicy      *Policy
}

// Violation 是 CSP 违规对象（本模型只保留与 TT 相关的字段）。
type Violation struct {
	Directive   string
	Disposition string
	Resource    string
	Sample      string
}

// CSPPolicy 是一条 CSP（enforce 或 report）。
type CSPPolicy struct {
	Disposition string
	Directives  map[string]string
}

// NewCSP 构造一条 CSP。
func NewCSP(disposition string, kv map[string]string) *CSPPolicy {
	return &CSPPolicy{Disposition: disposition, Directives: kv}
}

// Global 是 realm 的全局对象。
type Global struct {
	CSPList  []*CSPPolicy
	Factory  *Factory
	Violations []*Violation
}

// NewGlobal 构造全局对象。
func NewGlobal(csps ...*CSPPolicy) *Global {
	return &Global{CSPList: csps, Factory: &Factory{}}
}

func (g *Global) report(v *Violation) { g.Violations = append(g.Violations, v) }

// DoesSinkTypeRequireTrustedTypes 实现 §4.2.3。
func DoesSinkTypeRequireTrustedTypes(g *Global, sinkGroup string, includeReportOnly bool) bool {
	for _, p := range g.CSPList {
		d, has := p.Directives["require-trusted-types-for"]
		if !has || !strings.Contains(d, sinkGroup) {
			continue
		}
		if p.Disposition == "enforce" {
			return true
		}
		if includeReportOnly {
			return true
		}
	}
	return false
}

// ShouldPolicyCreationBeBlocked 实现 §4.2.5。
func ShouldPolicyCreationBeBlocked(g *Global, policyName string) string {
	result := "Allowed"
	for _, p := range g.CSPList {
		d, has := p.Directives["trusted-types"]
		if !has {
			continue
		}
		tokens := strings.Fields(d)
		createViolation := false
		allNone := len(tokens) > 0
		for _, t := range tokens {
			if t != "'none'" {
				allNone = false
			}
		}
		if allNone {
			createViolation = true
		}
		allowDup := false
		names := []string{}
		for _, t := range tokens {
			if t == "'allow-duplicates'" {
				allowDup = true
				continue
			}
			if !strings.HasPrefix(t, "'") {
				names = append(names, t)
			}
		}
		seen := false
		for _, n := range g.Factory.CreatedPolicyNames {
			if n == policyName {
				seen = true
			}
		}
		if seen && !allowDup {
			createViolation = true
		}
		inNames := false
		for _, n := range names {
			if n == policyName || n == "*" {
				inNames = true
			}
		}
		if !inNames {
			createViolation = true
		}
		if !createViolation {
			continue
		}
		g.report(&Violation{Directive: "trusted-types", Disposition: p.Disposition,
			Resource: "trusted-types-policy", Sample: truncate(policyName, 40)})
		if p.Disposition == "enforce" {
			result = "Blocked"
		}
	}
	return result
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

// CreatePolicy 实现 §3.1。
func CreatePolicy(g *Global, name string, options map[string]CreateFunc) (*Policy, error) {
	if ShouldPolicyCreationBeBlocked(g, name) == "Blocked" {
		return nil, &TrustedTypeError{Msg: "policy creation blocked by CSP: " + name}
	}
	if name == "default" && g.Factory.DefaultPolicy != nil {
		return nil, &TrustedTypeError{Msg: "default policy already exists"}
	}
	p := &Policy{Name: name, Options: options}
	if name == "default" {
		g.Factory.DefaultPolicy = p
	}
	g.Factory.CreatedPolicyNames = append(g.Factory.CreatedPolicyNames, name)
	return p, nil
}

// GetPolicyValue 实现 §3.3。
func GetPolicyValue(p *Policy, typeName, value string, args []string,
	throwIfMissing bool) (string, bool, error) {
	fn, has := p.Options[FunctionName[typeName]]
	if !has {
		if throwIfMissing {
			return "", false, &TrustedTypeError{Msg: p.Name + " 未实现 " + FunctionName[typeName]}
		}
		return "", false, nil
	}
	return fn(value, args)
}

// CreateTrustedType 实现 §3.2。
func CreateTrustedType(p *Policy, typeName, value string,
	extra []string) (*TrustedType, error) {
	data, ok, err := GetPolicyValue(p, typeName, value, extra, true)
	if err != nil {
		return nil, err
	}
	if !ok {
		data = ""
	}
	return &TrustedType{TypeName: typeName, Data: data}, nil
}

// ProcessValueWithDefaultPolicy 实现 §3.5。
func ProcessValueWithDefaultPolicy(g *Global, typeName string, in interface{},
	sink string) (*TrustedType, error) {
	dp := g.Factory.DefaultPolicy
	if dp == nil {
		return nil, nil
	}
	data, ok, err := GetPolicyValue(dp, typeName, stringify(in),
		[]string{typeName, sink}, false)
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, nil
	}
	return &TrustedType{TypeName: typeName, Data: data}, nil
}

// GetTrustedTypeCompliantString 实现 §3.4。
func GetTrustedTypeCompliantString(g *Global, expected string, in interface{},
	sink, sinkGroup string) (string, error) {
	if t, ok := in.(*TrustedType); ok && t.TypeName == expected {
		return t.Data, nil
	}
	if !DoesSinkTypeRequireTrustedTypes(g, sinkGroup, true) {
		return stringify(in), nil
	}
	converted, err := ProcessValueWithDefaultPolicy(g, expected, in, sink)
	if err != nil {
		return "", err
	}
	if converted == nil {
		if ShouldSinkMismatchBeBlocked(g, sink, sinkGroup, stringify(in)) == "Allowed" {
			return stringify(in), nil
		}
		return "", &TrustedTypeError{Msg: "sink " + sink + " requires " + expected}
	}
	if converted.TypeName != expected {
		return "", &TrustedTypeError{Msg: "default policy produced wrong type"}
	}
	return converted.Data, nil
}
