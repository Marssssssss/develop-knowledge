// Package semgrep 是 semgrep/semgrep@develop 中 src/matching/Matching_generic.ml
// 与 Normalize_generic.ml 里若干核心函数的 Go 转写，用于与 python/main.py 对拍。
//
// 对应源码位置：
//   m_list_with_dots                      -> matchListWithDots
//   m_list_with_dots_and_metavar_ellipsis -> matchListDotsMetavarEllipsis
//   m_list_in_any_order                   -> matchListInAnyOrder
//   check_and_add_metavar_binding         -> checkAndAddBinding
//   equal_ast_bound_code                  -> equalBoundCode
//   full_module_names                     -> fullModuleNames
package semgrep

import "strings"

// ---------------------------------------------------------------- 节点模型

// IdInfo 对应 AST_generic 的 id_info。Nil 表示「没有 id_info」，
// Resolved 为 "" 且 HasResolved 为 true 表示「已建 info 但未解析」。
type IdInfo struct {
	HasResolved     bool
	Resolved        string
	SID             int
	CaseInsensitive bool
}

// Node 是极简 AST 节点，Kind 取值：Id / E / Text。
type Node struct {
	Kind  string
	Name  string
	Value interface{}
	Info  *IdInfo
}

// Pat 是模式节点，Kind 取值：Id / E / Call / Metavar / Dots / MetavarEllipsis。
type Pat struct {
	Kind    string
	Name    string
	Value   interface{}
	Info    *IdInfo
	Sub     []Pat
	IsMetav bool
}

// Env 是元变量绑定环境（对应 tin.mv）。
type Env map[string]Node

func (e Env) clone() Env {
	out := make(Env, len(e))
	for k, v := range e {
		out[k] = v
	}
	return out
}

// Config 对应 Rule_options.t 里与安全匹配相关的开关。
type Config struct {
	UnifyIDsStrictly bool
}

func isDots(p Pat) bool { return p.Kind == "Dots" }

func isMetavarEllipsis(p Pat) (string, bool) {
	if p.Kind == "MetavarEllipsis" {
		return p.Name, true
	}
	return "", false
}

func isAnonymous(name string) bool { return name == "_" }

func addCapture(env Env, key string, val Node) Env {
	// 匿名元变量不进环境，因此不参与合一。
	if isAnonymous(key) {
		return env
	}
	out := env.clone()
	out[key] = val
	return out
}

func unresolved(i *IdInfo) bool { return i != nil && i.HasResolved && i.Resolved == "" }

// equalBoundCode 对应 equal_ast_bound_code，只比较「已绑定的代码」。
func EqualBoundCode(a, b Node, cfg Config) bool {
	if a.Kind == "Id" && b.Kind == "Id" {
		nameEq := a.Name == b.Name
		if a.Info != nil && b.Info != nil && a.Info.CaseInsensitive && b.Info.CaseInsensitive {
			nameEq = strings.EqualFold(a.Name, b.Name)
		}
		scopeOK := false
		switch {
		case unresolved(a.Info) || unresolved(b.Info) || a.Info == nil:
			scopeOK = true
		case a.Info != nil && b.Info != nil:
			scopeOK = !cfg.UnifyIDsStrictly ||
				(a.Info.Resolved == b.Info.Resolved && a.Info.SID == b.Info.SID)
		default: // Some resolved vs 无 id_info —— 源码里是 false
			scopeOK = false
		}
		return nameEq && scopeOK
	}
	if (a.Kind == "Id" && b.Kind == "Text") || (a.Kind == "Text" && b.Kind == "Id") {
		ident := a
		if ident.Kind != "Id" {
			ident = b
		}
		if ident.Info != nil && ident.Info.HasResolved && ident.Info.Resolved == "" {
			return a.Name == b.Name
		}
		return false
	}
	return a.Kind == b.Kind && a.Name == b.Name && a.Value == b.Value
}

// checkAndAddBinding 对应 check_and_add_metavar_binding；nil 表示 fail。
func CheckAndAddBinding(env Env, mvar string, val Node, cfg Config) Env {
	if old, ok := env[mvar]; ok {
		if !equalBoundCode(old, val, cfg) {
			return nil
		}
		return env
	}
	return addCapture(env, mvar, val)
}

// ---------------------------------------------------------------- 切分枚举

// initsAndRestEmptyOK 对应 inits_and_rest_of_list_empty_ok：长度 n 得到 n+1 个切分。
func initsAndRestEmptyOK(xs []Node) [][2][]Node {
	out := [][2][]Node{{[]Node{}, xs}}
	for i := 1; i <= len(xs); i++ {
		out = append(out, [2][]Node{xs[:i], xs[i:]})
	}
	return out
}

func allElemAndRest(xs []Node) []struct {
	Elem Node
	Rest []Node
} {
	out := make([]struct {
		Elem Node
		Rest []Node
	}, 0, len(xs))
	for i, x := range xs {
		rest := append([]Node{}, xs[:i]...)
		rest = append(rest, xs[i+1:]...)
		out = append(out, struct {
			Elem Node
			Rest []Node
		}{x, rest})
	}
	return out
}

// ---------------------------------------------------------------- 列表匹配

// matchListWithDots 对应 m_list_with_dots（含 [..., P, ...] 优化）。
func MatchListWithDots(pat []Pat, tgt []Node, env Env, lessIsOK bool,
	f func(Pat, Node, Env) []Env) []Env {
	if len(pat) == 3 && isDots(pat[0]) && isDots(pat[2]) {
		var out []Env
		for _, xb := range tgt {
			out = append(out, f(pat[1], xb, env)...)
		}
		return out
	}
	return rawListWithDots(pat, tgt, env, lessIsOK, f)
}

func rawListWithDots(pat []Pat, tgt []Node, env Env, lessIsOK bool,
	f func(Pat, Node, Env) []Env) []Env {
	switch {
	case len(pat) == 0 && len(tgt) == 0:
		return []Env{env}
	case len(pat) == 0 && len(tgt) > 0:
		if lessIsOK {
			return []Env{env}
		}
		return nil
	case len(pat) == 1 && isDots(pat[0]) && len(tgt) == 0:
		return []Env{env}
	case len(pat) > 0 && len(tgt) > 0 && isDots(pat[0]):
		out := rawListWithDots(pat[1:], tgt, env, lessIsOK, f)
		out = append(out, rawListWithDots(pat, tgt[1:], env, lessIsOK, f)...)
		return out
	case len(pat) > 0 && len(tgt) > 0:
		var out []Env
		for _, e := range f(pat[0], tgt[0], env) {
			out = append(out, rawListWithDots(pat[1:], tgt[1:], e, lessIsOK, f)...)
		}
		return out
	}
	return nil
}

// matchListDotsMetavarEllipsis 对应 m_list_with_dots_and_metavar_ellipsis。
// 注意源码里针对 `not less_is_ok` 的短路优化是被注释掉的，这里同样不短路。
func MatchListDotsMetavarEllipsis(pat []Pat, tgt []Node, env Env, lessIsOK bool,
	f func(Pat, Node, Env) []Env, cfg Config) []Env {
	var aux func(p []Pat, t []Node, e Env) []Env
	aux = func(p []Pat, t []Node, e Env) []Env {
		switch {
		case len(p) == 0 && len(t) == 0:
			return []Env{e}
		case len(p) == 0 && len(t) > 0:
			if lessIsOK {
				return []Env{e}
			}
			return nil
		case len(p) == 1 && isDots(p[0]) && len(t) == 0:
			return []Env{e}
		}
		if name, ok := isMetavarEllipsis(p[0]); ok && len(p) > 0 {
			var out []Env
			for _, split := range initsAndRestEmptyOK(t) {
				bound := Node{Kind: "List", Value: split[0]}
				ne := checkAndAddBinding(e, name, bound, cfg)
				if ne == nil {
					continue
				}
				out = append(out, aux(p[1:], split[1], ne)...)
			}
			return out
		}
		if len(p) > 0 && len(t) > 0 && isDots(p[0]) {
			out := aux(p[1:], t, e)
			out = append(out, aux(p, t[1:], e)...)
			return out
		}
		if len(p) > 0 && len(t) > 0 {
			var out []Env
			for _, e2 := range f(p[0], t[0], e) {
				out = append(out, aux(p[1:], t[1:], e2)...)
			}
			return out
		}
		return nil
	}
	return aux(pat, tgt, env)
}

// matchListInAnyOrder 对应 m_list_in_any_order（AC 匹配）。
func MatchListInAnyOrder(pat []Pat, tgt []Node, env Env, lessIsOK bool,
	f func(Pat, Node, Env) []Env) []Env {
	switch {
	case len(pat) == 0 && len(tgt) == 0:
		return []Env{env}
	case len(pat) == 0 && len(tgt) > 0:
		if lessIsOK {
			return []Env{env}
		}
		return nil
	}
	if len(pat) > 0 {
		var out []Env
		for _, cand := range allElemAndRest(tgt) {
			for _, e := range f(pat[0], cand.Elem, env) {
				out = append(out, matchListInAnyOrder(pat[1:], cand.Rest, e, lessIsOK, f)...)
			}
		}
		return out
	}
	return nil
}

// ---------------------------------------------------------------- import 归一化

// ModuleName 对应 Normalize_generic 里的 module_name。
type ModuleName struct {
	IsFileName bool
	Idents     []string
	Path       string
}

// fullModuleNames 对应 full_module_names；nil 表示该 import 不参与匹配。
func FullModuleNames(isPattern bool, m ModuleName, imports []string) []ModuleName {
	if !m.IsFileName {
		if imports != nil {
			var out []ModuleName
			for _, n := range imports {
				out = append(out, ModuleName{Idents: append(append([]string{}, m.Idents...), n)})
			}
			return out
		}
		return []ModuleName{{Idents: append([]string{}, m.Idents...)}}
	}
	if imports == nil {
		return []ModuleName{{IsFileName: true, Path: m.Path}}
	}
	// bugfix：JS 的 import x from "path" 不应退化成 "path"
	if !isPattern {
		return []ModuleName{{IsFileName: true, Path: m.Path}}
	}
	return nil
}

// RenderModule 把归一化结果渲染成可读字符串，便于与 Python 侧对拍。
func RenderModule(m ModuleName) string {
	if m.IsFileName {
		return "FileName(" + m.Path + ")"
	}
	out := ""
	for i, s := range m.Idents {
		if i > 0 {
			out += "."
		}
		out += s
	}
	return out
}
