// Package main 实现 DOM Clobbering 与 window 命名属性访问最小模型。
//
// 依据 HTML 规范 §7.2.2.3「Named access on the Window object」
// （https://html.spec.whatwg.org/multipage/window-object.html 817681 B 实读）：
//   - supported property names（tree order，忽略后出现的重复）：
//     1) document-tree child navigable target name property set
//     2) embed/form/img/object 四种标签的非空 name 内容属性
//     3) 所有带 ID 的元素的 ID
//   - property set 是两段循环：先按名去重（只留第一个），再按同源过滤。
//     跨源 iframe 会占掉重名再被过滤掉，把同源那个一起带走（spices 例子）。
//   - named objects **不做**同源过滤，与 supported property names 不是一回事。
//   - 取值优先级：navigable > 单元素 > HTMLCollection。
//   - Window 带 [Global]，命名属性按 named properties object 规则处理。
package main

import "sort"

// NameAttrTags 是 name 内容属性参与命名访问的四种标签。
var NameAttrTags = map[string]bool{
	"embed": true, "form": true, "img": true, "object": true,
}

// Node 是一个贡献者：navigable 或元素。Index 决定 tree order。
type Node struct {
	IsNav      bool
	Index      int
	TargetName string // navigable 的 target name（会被子文档 window.name 改写）
	Origin     string // navigable active document 的 origin
	Tag        string
	ID         string
	Name       string
	Attrs      map[string]string
}

// Window 是一个窗口。
type Window struct {
	Origin string
	Nodes  []*Node
}

// ContributesName 判断元素的 name 内容属性是否参与命名访问。
func (n *Node) ContributesName() bool {
	return !n.IsNav && NameAttrTags[n.Tag] && n.Name != ""
}

// Ordered 按 tree order 返回全部贡献者。
func (w Window) Ordered() []*Node {
	out := make([]*Node, len(w.Nodes))
	copy(out, w.Nodes)
	sort.SliceStable(out, func(i, j int) bool { return out[i].Index < out[j].Index })
	return out
}

// TargetNamePropertySet 两段循环得到的 property set（同源过滤后）。
func TargetNamePropertySet(w Window) []string {
	var firstNamed []*Node
	for _, n := range w.Ordered() {
		if !n.IsNav || n.TargetName == "" {
			continue
		}
		dup := false
		for _, m := range firstNamed {
			if m.TargetName == n.TargetName {
				dup = true
				break
			}
		}
		if dup {
			continue
		}
		firstNamed = append(firstNamed, n)
	}
	var names []string
	for _, n := range firstNamed {
		if n.Origin == w.Origin {
			names = append(names, n.TargetName)
		}
	}
	return names
}

func elementNames(n *Node) []string {
	var names []string
	if n.ContributesName() {
		names = append(names, n.Name)
	}
	if n.ID != "" {
		names = append(names, n.ID)
	}
	return names
}

// SupportedPropertyNames 按 tree order 求并集，忽略后出现的重复。
func SupportedPropertyNames(w Window) []string {
	navNames := map[string]bool{}
	for _, s := range TargetNamePropertySet(w) {
		navNames[s] = true
	}
	var out []string
	seen := map[string]bool{}
	for _, n := range w.Ordered() {
		var candidates []string
		if n.IsNav {
			if navNames[n.TargetName] {
				candidates = []string{n.TargetName}
			}
		} else {
			candidates = elementNames(n)
		}
		for _, c := range candidates {
			if !seen[c] {
				seen[c] = true
				out = append(out, c)
			}
		}
	}
	return out
}

func elMatches(n *Node, name string) bool {
	return (NameAttrTags[n.Tag] && n.Name == name) || n.ID == name
}

// NamedObjects 不做同源过滤的 named objects 列表。
// 同一对象只记一次：规范三条目列举未明说 <img id=x name=x> 是否计两次，
// 本 demo 按对象同一性去重，README 已标注该口径。
func NamedObjects(w Window, name string) []*Node {
	var out []*Node
	add := func(n *Node) {
		for _, m := range out {
			if m == n {
				return
			}
		}
		out = append(out, n)
	}
	for _, n := range w.Ordered() {
		if n.IsNav {
			if n.TargetName == name {
				add(n)
			}
		} else if elMatches(n, name) {
			add(n)
		}
	}
	return out
}

// Value 是取值结果。
type Value struct {
	Kind     string // windowproxy / element / collection / ""（undefined）
	Nav      *Node
	Elements []*Node
}

// DetermineValue 实现 §7.2.2.3 的取值算法。
func DetermineValue(w Window, name string) Value {
	objects := NamedObjects(w, name)
	if len(objects) == 0 {
		return Value{}
	}
	hasNav := false
	for _, o := range objects {
		if o.IsNav {
			hasNav = true
			break
		}
	}
	if hasNav {
		for _, n := range w.Ordered() {
			if !n.IsNav {
				continue
			}
			for _, o := range objects {
				if o == n {
					return Value{Kind: "windowproxy", Nav: n}
				}
			}
		}
	}
	if len(objects) == 1 {
		return Value{Kind: "element", Elements: objects}
	}
	return Value{Kind: "collection", Elements: objects}
}

// WindowGet 求 window[name]：未列入 supported property names 时是 undefined。
func WindowGet(w Window, name string) Value {
	found := false
	for _, s := range SupportedPropertyNames(w) {
		if s == name {
			found = true
			break
		}
	}
	if !found {
		return Value{}
	}
	return DetermineValue(w, name)
}
