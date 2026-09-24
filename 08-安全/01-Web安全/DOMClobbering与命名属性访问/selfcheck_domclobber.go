package main

// DOMClobbering 自检（与 selfcheck_domclobber.py 同一套断言）。
// 依据 https://html.spec.whatwg.org/multipage/window-object.html §7.2.2.3 实读。

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

const org = "example.org"
const els = "elsewhere.example.com"

func el(i int, tag, id, name string) *Node {
	return &Node{IsNav: false, Index: i, Tag: tag, ID: id, Name: name}
}

func nav(i int, target, origin string) *Node {
	return &Node{IsNav: true, Index: i, TargetName: target, Origin: origin}
}

func main() {
	// 哪些标签的 name 参与
	eq("div 的 id 参与", SupportedPropertyNames(Window{org, []*Node{el(0, "div", "x", "")}}), "[x]")
	eq("div 的 name 不参与",
		len(SupportedPropertyNames(Window{org, []*Node{el(0, "div", "", "x")}})), 0)
	eq("img 的 name 参与",
		SupportedPropertyNames(Window{org, []*Node{el(0, "img", "", "x")}}), "[x]")
	eq("a 的 name 不参与",
		len(SupportedPropertyNames(Window{org, []*Node{el(0, "a", "", "x")}})), 0)
	eq("a 的 id 参与",
		SupportedPropertyNames(Window{org, []*Node{el(0, "a", "x", "")}}), "[x]")

	// tree order 与去重
	eq("tree order 而非字典序",
		SupportedPropertyNames(Window{org, []*Node{el(0, "div", "b", ""), el(1, "div", "a", "")}}),
		"[b a]")
	eq("重复 ID 只算一次",
		SupportedPropertyNames(Window{org, []*Node{el(0, "div", "x", ""), el(1, "span", "x", "")}}),
		"[x]")

	// property set 两段循环 / 规范 spices 例子
	w := Window{org, []*Node{nav(0, "spices", els), nav(1, "spices", org)}}
	eq("跨源占掉重名后 property set 为空", len(TargetNamePropertySet(w)), 0)
	eq("supported names 为空", len(SupportedPropertyNames(w)), 0)
	eq("spices 求值为 undefined（规范原文例子）", WindowGet(w, "spices").Kind, "")
	w = Window{org, []*Node{nav(0, "spices", org), nav(1, "spices", els)}}
	eq("同源的排在前 → property set 有值", TargetNamePropertySet(w), "[spices]")
	eq("named objects 不过滤同源", len(NamedObjects(w, "spices")), 2)
	eq("取到 tree order 第一个 navigable", DetermineValue(w, "spices").Nav.Origin, org)

	// 取值优先级
	w = Window{org, []*Node{el(0, "div", "x", "")}}
	eq("单元素 → 元素", DetermineValue(w, "x").Kind, "element")
	w = Window{org, []*Node{el(0, "div", "x", ""), el(1, "span", "x", "")}}
	eq("两元素 → HTMLCollection", DetermineValue(w, "x").Kind, "collection")
	eq("集合含两个对象", len(DetermineValue(w, "x").Elements), 2)
	w = Window{org, []*Node{nav(0, "x", org), el(1, "div", "x", "")}}
	eq("navigable 优先于元素", DetermineValue(w, "x").Kind, "windowproxy")
	w = Window{org, []*Node{el(0, "div", "x", ""), nav(1, "x", org)}}
	eq("元素在前 navigable 仍优先", DetermineValue(w, "x").Kind, "windowproxy")

	// 经典 clobbering：注入 <a id=config href=...>
	a := el(0, "a", "config", "")
	a.Attrs = map[string]string{"href": "javascript:alert(1)"}
	w = Window{org, []*Node{a}}
	v := WindowGet(w, "config")
	eq("注入的 a 元素成为 window.config", v.Kind, "element")
	eq("clobber 后的 href 可被读到", v.Elements[0].Attrs["href"], "javascript:alert(1)")
	w = Window{org, []*Node{el(0, "a", "config", ""), el(1, "a", "config", "")}}
	eq("两个同名注入得到集合", WindowGet(w, "config").Kind, "collection")

	// window_get 只认 supported names
	w = Window{org, []*Node{el(0, "div", "", "x")}}
	eq("未列入 supported names 即 undefined", WindowGet(w, "x").Kind, "")

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
