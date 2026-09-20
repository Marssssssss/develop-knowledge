// Terraform 声明式状态重构模型：moved / removed / import 块的解析与计划期地址搬迁。
// 口径与 Python 版完全一致，均来自实际读过的官方原文（见 README 参考资料）。
package main

import (
	"fmt"
	"sort"
	"strings"
)

// ---- 地址 ----

type ModSeg struct {
	Name string
	Key  *string
}

type Addr struct {
	Modules []ModSeg
	RType   string
	RName   string
	Key     *string
	IsData  bool
}

func sptr(s string) *string { return &s }

func bracket(k *string) string {
	if k == nil {
		return ""
	}
	t := strings.TrimPrefix(*k, "-")
	if t != "" && strings.IndexFunc(t, func(r rune) bool { return r < '0' || r > '9' }) < 0 {
		return "[" + *k + "]"
	}
	return "[\"" + *k + "\"]"
}

func (a Addr) String() string {
	var sb strings.Builder
	for _, m := range a.Modules {
		sb.WriteString("module." + m.Name + bracket(m.Key) + ".")
	}
	s := sb.String()
	if a.RType == "" {
		return strings.TrimSuffix(s, ".")
	}
	head := ""
	if a.IsData {
		head = "data."
	}
	return s + head + a.RType + "." + a.RName + bracket(a.Key)
}

func (a Addr) IsModuleOnly() bool { return a.RType == "" }

func (a Addr) HasAnyKey() bool {
	if a.Key != nil {
		return true
	}
	for _, m := range a.Modules {
		if m.Key != nil {
			return true
		}
	}
	return false
}

// splitTop 按顶层 '.' 切分，尊重 [] 嵌套与引号内的点。
func splitTop(s string) []string {
	var parts []string
	var buf []rune
	depth := 0
	var quote rune
	for _, ch := range s {
		switch {
		case quote != 0:
			buf = append(buf, ch)
			if ch == quote {
				quote = 0
			}
		case ch == '"' || ch == '\'':
			quote = ch
			buf = append(buf, ch)
		case ch == '[':
			depth++
			buf = append(buf, ch)
		case ch == ']':
			depth--
			buf = append(buf, ch)
		case ch == '.' && depth == 0:
			parts = append(parts, string(buf))
			buf = nil
		default:
			buf = append(buf, ch)
		}
	}
	parts = append(parts, string(buf))
	out := parts[:0]
	for _, p := range parts {
		if p != "" {
			out = append(out, p)
		}
	}
	return out
}

func splitKey(seg string) (string, *string) {
	i := strings.Index(seg, "[")
	if i < 0 {
		return seg, nil
	}
	name := seg[:i]
	k := strings.TrimSpace(seg[i+1 : strings.LastIndex(seg, "]")])
	if len(k) >= 2 && k[0] == k[len(k)-1] && (k[0] == '"' || k[0] == '\'') {
		k = k[1 : len(k)-1]
		return name, sptr(k)
	}
	if k == "" {
		return name, nil
	}
	return name, sptr(k)
}

func ParseAddr(s string) (Addr, error) {
	parts := splitTop(strings.TrimSpace(s))
	var a Addr
	i := 0
	// "module" 与其名字之间的点在方括号外，会被 splitTop 切开，这里成对取回
	for i+1 < len(parts) && parts[i] == "module" {
		n, k := splitKey(parts[i+1])
		a.Modules = append(a.Modules, ModSeg{n, k})
		i += 2
	}
	rest := parts[i:]
	if len(rest) == 0 {
		return a, nil
	}
	if rest[0] == "data" && len(rest) >= 3 {
		a.IsData = true
		rest = rest[1:]
	}
	if len(rest) != 2 {
		return a, fmt.Errorf("无法解析地址: %q", s)
	}
	name, k := splitKey(rest[1])
	a.RType, a.RName, a.Key = rest[0], name, k
	return a, nil
}

func mustAddr(s string) Addr {
	a, err := ParseAddr(s)
	if err != nil {
		panic(err)
	}
	return a
}

// ---- moved 块 ----

type Move struct {
	From Addr
	To   Addr
}

func NewMove(f, t string) Move {
	mv := Move{mustAddr(f), mustAddr(t)}
	if mv.From.IsData != mv.To.IsData {
		panic("moved 不能在托管资源与 data 资源之间搬迁: " + f + " -> " + t)
	}
	return mv
}

func (mv Move) InstanceLevel() bool { return mv.From.HasAnyKey() || mv.To.HasAnyKey() }

func modPrefixMatches(a Addr, prefix []ModSeg) bool {
	if len(a.Modules) < len(prefix) {
		return false
	}
	for i, p := range prefix {
		if a.Modules[i].Name != p.Name {
			return false
		}
		if (a.Modules[i].Key == nil) != (p.Key == nil) {
			return false
		}
		if p.Key != nil && *a.Modules[i].Key != *p.Key {
			return false
		}
	}
	return true
}

func (mv Move) Matches(a Addr) bool {
	f := mv.From
	if f.IsModuleOnly() {
		if !modPrefixMatches(a, f.Modules) {
			return false
		}
		if mv.InstanceLevel() {
			return len(a.Modules) == len(f.Modules) && eqKey(a.Key, f.Key)
		}
		return true
	}
	if mv.InstanceLevel() {
		return a.String() == f.String()
	}
	if len(a.Modules) != len(f.Modules) || !modPrefixMatches(a, f.Modules) {
		return false
	}
	return a.RType == f.RType && a.RName == f.RName && a.IsData == f.IsData
}

func eqKey(x, y *string) bool {
	if x == nil || y == nil {
		return x == nil && y == nil
	}
	return *x == *y
}

// Rewrite 把 addr 中匹配 From 的部分替换成 To。
// 整资源级保留原实例键（a[0]→b[0]）；实例级以 To 的键为准，To 无键则丢弃键（d[2]→d）。
func (mv Move) Rewrite(a Addr) Addr {
	f, t := mv.From, mv.To
	if f.IsModuleOnly() {
		mods := append([]ModSeg{}, t.Modules...)
		mods = append(mods, a.Modules[len(f.Modules):]...)
		key := a.Key
		if mv.InstanceLevel() {
			key = t.Key
		}
		return Addr{mods, a.RType, a.RName, key, a.IsData}
	}
	key := a.Key
	if mv.InstanceLevel() {
		key = t.Key
	}
	return Addr{t.Modules, t.RType, t.RName, key, t.IsData}
}

func ApplyMoves(state map[string]map[string]string, moves []Move) map[string]map[string]string {
	cur := map[string]map[string]string{}
	for k, v := range state {
		cur[mustAddr(k).String()] = v
	}
	for _, mv := range moves {
		next := map[string]map[string]string{}
		for s, obj := range cur {
			a := mustAddr(s)
			if mv.Matches(a) {
				next[mv.Rewrite(a).String()] = obj
			} else {
				next[s] = obj
			}
		}
		cur = next
	}
	return cur
}

