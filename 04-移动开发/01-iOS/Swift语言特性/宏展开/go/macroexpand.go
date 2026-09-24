// Swift 宏展开模型的 Go 侧转写。
// 对照 apple/swift-syntax@main 的
// Sources/SwiftSyntaxMacroExpansion/MacroExpansion.swift 与 MacroSystem.swift。
package main

import (
	"fmt"
	"strings"
)

// MacroRole 的十一个角色
const (
	roleExpression      = "expression"
	roleDeclaration     = "declaration"
	roleAccessor        = "accessor"
	roleMemberAttribute = "memberAttribute"
	roleMember          = "member"
	rolePeer            = "peer"
	roleConformance     = "conformance"
	roleCodeItem        = "codeItem"
	roleExtension       = "extension"
	rolePreamble        = "preamble"
	roleBody            = "body"
)

// protocolName 对应 MacroRole.protocolName
var protocolName = map[string]string{
	roleExpression:      "ExpressionMacro",
	roleDeclaration:     "DeclarationMacro",
	roleAccessor:        "AccessorMacro",
	roleMemberAttribute: "MemberAttributeMacro",
	roleMember:          "MemberMacro",
	rolePeer:            "PeerMacro",
	roleConformance:     "ConformanceMacro",
	roleCodeItem:        "CodeItemMacro",
	roleExtension:       "ExtensionMacro",
	rolePreamble:        "PreambleMacro",
	roleBody:            "BodyMacro",
}

// freestandingRoleOrder inferFreestandingMacroRole 的判定顺序
var freestandingRoleOrder = []string{roleExpression, roleDeclaration, roleCodeItem}

var freestandingRoles = map[string]bool{
	roleExpression: true, roleDeclaration: true, roleCodeItem: true,
}

const defaultIndent = 4

// expansion states
const (
	notAMacro = "notAMacro"
	failure   = "failure"
	success   = "success"
)

// MacroSpec 一条宏注册项
type MacroSpec struct {
	Type       string
	ModuleName string
}

// MacroSystem var macros: [String: MacroSpec]
type MacroSystem struct {
	Macros map[string]MacroSpec
}

func newMacroSystem() *MacroSystem {
	return &MacroSystem{Macros: map[string]MacroSpec{}}
}

// Add 同名已存在则报错,已注册的那条保持不变
func (s *MacroSystem) Add(name string, spec MacroSpec) error {
	if existing, ok := s.Macros[name]; ok {
		return fmt.Errorf("alreadyDefined(new: %s, existing: %s)", spec.Type, existing.Type)
	}
	s.Macros[name] = spec
	return nil
}

// Lookup 名字命中后还要校验模块
func (s *MacroSystem) Lookup(name, moduleName string) (MacroSpec, bool) {
	spec, ok := s.Macros[name]
	if !ok {
		return MacroSpec{}, false
	}
	if moduleName != "" && spec.ModuleName != moduleName {
		return MacroSpec{}, false
	}
	return spec, true
}

// attachedMacroReference 解析 @Name、@Module.Name、@Module::Name。
// MemberTypeSyntax 分支要求基类型不带 moduleSelector 也不带泛型参数。
func attachedMacroReference(text string) (string, string, bool) {
	if strings.ContainsAny(text, "<>") || text == "" {
		return "", "", false
	}
	if strings.Contains(text, "::") {
		parts := strings.Split(text, "::")
		if len(parts) != 2 || parts[0] == "" || parts[1] == "" {
			return "", "", false
		}
		return parts[1], parts[0], true
	}
	segs := strings.Split(text, ".")
	switch len(segs) {
	case 1:
		return segs[0], "", true
	case 2:
		if segs[0] == "" || segs[1] == "" {
			return "", "", false
		}
		return segs[1], segs[0], true
	}
	return "", "", false
}

// inferFreestandingMacroRole 按 expression -> declaration -> codeItem 试
func inferFreestandingMacroRole(conforms map[string]bool) (string, error) {
	for _, r := range freestandingRoleOrder {
		if conforms[r] {
			return r, nil
		}
	}
	return "", fmt.Errorf("noFreestandingMacroRoles")
}

// collapse 按角色决定分隔符,必要时把结果包进一对花括号
func collapse(expansions []string, role string, declarationHasAccessor bool, indent int) string {
	if len(expansions) == 0 {
		return ""
	}
	exps := append([]string{}, expansions...)
	separator := "\n\n"
	wrapInBraces := func() string {
		pad := strings.Repeat(" ", indent)
		for i, e := range exps {
			exps[i] = pad + strings.ReplaceAll(e, "\n", "\n"+pad)
		}
		exps[0] = "{\n" + exps[0]
		exps[len(exps)-1] += "\n}"
		return "\n"
	}
	switch role {
	case roleAccessor:
		// 只有声明本身没有 accessorBlock 时才补花括号
		if !declarationHasAccessor {
			separator = wrapInBraces()
		}
	case roleMemberAttribute:
		separator = " "
	case roleBody:
		separator = wrapInBraces()
	case rolePreamble:
		separator = "\n"
	}
	collapsed := ""
	for _, e := range exps {
		if collapsed == "" || strings.HasPrefix(e, separator) {
			collapsed += e
		} else {
			collapsed += separator + e
		}
	}
	return collapsed
}

// Context 最小的 MacroExpansionContext
type Context struct {
	LexicalContext []string
	Counter        int
	Diagnostics    []string
}

func (c *Context) MakeUniqueName(name string) string {
	c.Counter++
	return fmt.Sprintf("%s_%d", name, c.Counter)
}

func (c *Context) Diagnose(msg string) {
	c.Diagnostics = append(c.Diagnostics, msg)
}

// WrapperContext 对应 PrependLexicalContextWrapperContext
type WrapperContext struct {
	Prepend []string
	Wrapped *Context
}

func (w WrapperContext) LexicalContext() []string {
	return append(append([]string{}, w.Prepend...), w.Wrapped.LexicalContext...)
}

// MakeUniqueName 直接转发给被包装的 context
func (w WrapperContext) MakeUniqueName(name string) string {
	return w.Wrapped.MakeUniqueName(name)
}

// MacroApplication 维护正在展开的独立宏栈以检测递归
type MacroApplication struct {
	System    *MacroSystem
	Context   *Context
	Expanding []string
}

// ExpandFreestanding 返回 (状态, 展开结果)
func (a *MacroApplication) ExpandFreestanding(name, module string, expand func(string) (string, error)) (string, string) {
	spec, ok := a.System.Lookup(name, module)
	if !ok {
		return notAMacro, ""
	}
	for _, m := range a.Expanding {
		if m == spec.Type {
			a.Context.Diagnose(fmt.Sprintf("recursiveExpansion(%s)", spec.Type))
			return failure, ""
		}
	}
	a.Expanding = append(a.Expanding, spec.Type)
	defer func() { a.Expanding = a.Expanding[:len(a.Expanding)-1] }()
	expanded, err := expand(spec.Type)
	if err != nil {
		a.Context.Diagnose(err.Error())
		return failure, ""
	}
	return success, expanded
}

// WithExpandedNode push 与 pop 精确包住 body
func (a *MacroApplication) WithExpandedNode(macroType string, body func() string) string {
	a.Expanding = append(a.Expanding, macroType)
	defer func() { a.Expanding = a.Expanding[:len(a.Expanding)-1] }()
	return body()
}

// AttachedAttr 一个附着宏属性
type AttachedAttr struct {
	Name       string
	Module     string
	ConformsTo map[string]bool
	Expansion  []string
	Raises     bool
}

// ExpandAttached 对应 expandMacros:抛错的那个只记诊断,不影响其余属性
func (a *MacroApplication) ExpandAttached(attrs []AttachedAttr, role string) []string {
	var out []string
	for _, attr := range attrs {
		if _, ok := a.System.Lookup(attr.Name, attr.Module); !ok {
			continue
		}
		if !attr.ConformsTo[role] {
			continue
		}
		if attr.Raises {
			a.Context.Diagnose("macro threw")
			continue
		}
		out = append(out, attr.Expansion...)
	}
	return out
}
