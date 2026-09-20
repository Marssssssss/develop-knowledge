// tfunknown 自检：与 Python 版同口径，来自实际读过的 objchange.go 与官方文档。
package main

import (
	"fmt"
)

var n int
var fails []string

func check(label string, cond bool, detail string) {
	n++
	if !cond {
		fails = append(fails, label+"  "+detail)
	}
}

func schema() map[string]Attr {
	return map[string]Attr{
		"id":   {Computed: true},
		"name": {},
		"tags": {Optional: true},
	}
}

func obj(kv ...interface{}) map[string]interface{} {
	m := map[string]interface{}{}
	for i := 0; i+1 < len(kv); i += 2 {
		m[kv[i].(string)] = kv[i+1]
	}
	return m
}

func isU(v interface{}) bool { return isUnknown(v) }

func main() {
	sch := schema()

	// ---- 1. prior 为空（新建）----
	p := ProposedNew(sch, nil, obj("id", nil, "name", "web", "tags", nil))
	m := p.(map[string]interface{})
	check("A1 新建时 prior 视为全 null",
		m["id"] == nil && m["name"] == "web" && m["tags"] == nil, fmtv(p))
	ev := EmptyValue(sch)
	check("A2 EmptyValue 是全 null",
		ev["id"] == nil && ev["name"] == nil && ev["tags"] == nil, fmtv(ev))
	check("A3 ProposedNew 阶段 computed 取 prior 而非直接 unknown", m["id"] == nil, fmtv(p))
	p2 := ProviderFillUnknown(sch, p).(map[string]interface{})
	check("A4 provider 补 unknown 后 id 才是 UNKNOWN", isU(p2["id"]), fmtv(p2))
	check("A5 非 computed 属性不受影响", p2["name"] == "web", fmtv(p2))

	// ---- 2. 已有资源：computed 保留 prior ----
	prior := obj("id", "i-123", "name", "web", "tags", "a")
	p = ProposedNew(sch, prior, obj("id", nil, "name", "web", "tags", "a"))
	check("B1 computed 保留 prior 值", p.(map[string]interface{})["id"] == "i-123", fmtv(p))
	p = ProposedNew(sch, prior, obj("id", nil, "name", "web2", "tags", "a"))
	m = p.(map[string]interface{})
	check("B2 改普通属性不影响 computed", m["id"] == "i-123" && m["name"] == "web2", fmtv(p))
	p = ProposedNew(sch, prior, obj("id", nil, "name", nil, "tags", nil))
	check("B3 非 computed 即使 config 为 null 也取 config",
		p.(map[string]interface{})["name"] == nil, fmtv(p))

	// ---- 3. config 整体 unknown / null ----
	p = ProposedNew(sch, prior, UNKNOWN)
	check("C1 config 整体 unknown 时返回 prior", fmtv(p) == fmtv(prior), fmtv(p))
	p = ProposedNew(sch, prior, nil)
	check("C2 config 为 null 时返回 prior", fmtv(p) == fmtv(prior), fmtv(p))
	check("C3 两者都 null 时直接返回 prior", ProposedNew(sch, nil, nil) == nil, "")

	// ---- 4. 嵌套块 ----
	ns := &Nested{"single", map[string]Attr{"inner": {}, "cid": {Computed: true}}}
	bs := map[string]Attr{"blk": {Nested: ns}}
	p = ProposedNew(bs, obj("blk", obj("inner", "x", "cid", "c")), obj("blk", UNKNOWN))
	check("D1 嵌套块整体 unknown 直接透传", isU(p.(map[string]interface{})["blk"]), fmtv(p))
	p = ProposedNew(bs, obj("blk", obj("inner", "x", "cid", "c")),
		obj("blk", obj("inner", "y", "cid", nil)))
	blk := p.(map[string]interface{})["blk"].(map[string]interface{})
	check("D2 嵌套块内 computed 同样保留 prior",
		blk["inner"] == "y" && blk["cid"] == "c", fmtv(p))
	p = ProposedNew(bs, obj("blk", nil), obj("blk", nil))
	check("D3 single 且 config 为 null 时取 config",
		p.(map[string]interface{})["blk"] == nil, fmtv(p))

	// ---- 5. list 按下标关联 ----
	bl := map[string]Attr{"b": {Nested: &Nested{"list", ns.Schema}}}
	prior = obj("b", []interface{}{obj("inner", "p0", "cid", "c0"), obj("inner", "p1", "cid", "c1")})
	p = ProposedNew(bl, prior, obj("b", []interface{}{obj("inner", "n0", "cid", nil)}))
	lst := p.(map[string]interface{})["b"].([]interface{})
	check("E1 list 按位合并，多余 prior 被丢弃", len(lst) == 1 &&
		lst[0].(map[string]interface{})["cid"] == "c0", fmtv(p))
	p = ProposedNew(bl, prior, obj("b", []interface{}{
		obj("inner", "n0", "cid", nil), obj("inner", "n1", "cid", nil)}))
	lst = p.(map[string]interface{})["b"].([]interface{})
	check("E2 两位都能对上",
		lst[0].(map[string]interface{})["cid"] == "c0" &&
			lst[1].(map[string]interface{})["cid"] == "c1", fmtv(p))
	p = ProposedNew(bl, prior, obj("b", []interface{}{
		obj("inner", "n0", "cid", nil), obj("inner", "n1", "cid", nil), obj("inner", "n2", "cid", nil)}))
	lst = p.(map[string]interface{})["b"].([]interface{})
	check("E3 超出 prior 长度的元素原样取 config",
		lst[2].(map[string]interface{})["cid"] == nil, fmtv(p))
	p = ProposedNew(bl, prior, obj("b", []interface{}{}))
	check("E4 空 config list 原样返回",
		len(p.(map[string]interface{})["b"].([]interface{})) == 0, fmtv(p))

	// ---- 6. map 按键关联 ----
	bm := map[string]Attr{"b": {Nested: &Nested{"map", ns.Schema}}}
	prior = obj("b", map[string]interface{}{"x": obj("inner", "p", "cid", "cx")})
	p = ProposedNew(bm, prior, obj("b", map[string]interface{}{
		"x": obj("inner", "n", "cid", nil), "y": obj("inner", "q", "cid", nil)}))
	mp := p.(map[string]interface{})["b"].(map[string]interface{})
	check("F1 map 存在的键合并 computed",
		mp["x"].(map[string]interface{})["cid"] == "cx", fmtv(p))
	check("F2 map 不存在的键原样取 config",
		mp["y"].(map[string]interface{})["cid"] == nil, fmtv(p))

	// ---- 7. set 关联是启发式 ----
	bst := map[string]Attr{"b": {Nested: &Nested{"set", ns.Schema}}}
	prior = obj("b", []interface{}{obj("inner", "keep", "cid", "ck")})
	p = ProposedNew(bst, prior, obj("b", []interface{}{obj("inner", "keep", "cid", nil)}))
	lst = p.(map[string]interface{})["b"].([]interface{})
	check("G1 非 computed 值相同时能匹配上",
		lst[0].(map[string]interface{})["cid"] == "ck", fmtv(p))
	p = ProposedNew(bst, prior, obj("b", []interface{}{obj("inner", "other", "cid", nil)}))
	lst = p.(map[string]interface{})["b"].([]interface{})
	check("G2 匹配不上时原样取 config",
		lst[0].(map[string]interface{})["cid"] == nil, fmtv(p))
	check("G3 set 签名只含非 computed 属性",
		nonComputedSig(ns.Schema, obj("inner", "a", "cid", "zzz")) ==
			nonComputedSig(ns.Schema, obj("inner", "a", "cid", "yyy")), "")

	// ---- 8. data source：整体 unknown 的 prior ----
	p = PlannedDataResourceObject(sch, obj("id", nil, "name", "web", "tags", nil))
	m = p.(map[string]interface{})
	check("H1 data source 的 computed 全是 unknown", isU(m["id"]), fmtv(p))
	check("H2 config 给的值不受影响", m["name"] == "web", fmtv(p))
	check("H3 config 也是 unknown 时返回 unknown",
		isU(PlannedDataResourceObject(sch, UNKNOWN)), "")

	// ---- 9. optional + computed + nested 的例外分支 ----
	ons := &Nested{"single", map[string]Attr{"sub": {}, "subcid": {Computed: true}}}
	osch := map[string]Attr{"blk": {Computed: true, Optional: true, Nested: ons}}
	p = ProposedNew(osch, obj("blk", obj("sub", "v", "subcid", "sc")), obj("blk", nil))
	check("I1 prior 含非 computed 值时取 config(null)",
		p.(map[string]interface{})["blk"] == nil, fmtv(p))
	p = ProposedNew(map[string]Attr{"x": {Computed: true, Optional: true}},
		obj("x", "prior"), obj("x", nil))
	check("I2 非嵌套的 optional+computed 仍保留 prior",
		p.(map[string]interface{})["x"] == "prior", fmtv(p))

	// ---- 10. unknown 短路 ----
	p = ProposedNew(sch, UNKNOWN, obj("id", nil, "name", "n", "tags", nil))
	m = p.(map[string]interface{})
	check("J1 对 unknown 取属性仍是 unknown", isU(m["id"]), fmtv(p))
	check("J2 prior 整体 unknown 时非 computed 仍取 config", m["name"] == "n", fmtv(p))

	fmt.Printf("checks=%d fail=%d\n", n, len(fails))
	for _, f := range fails {
		fmt.Println("  FAIL", f)
	}
}
