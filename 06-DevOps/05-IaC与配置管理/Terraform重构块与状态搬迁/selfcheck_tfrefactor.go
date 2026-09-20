// tfrefactor 自检：与 Python 版同口径，全部基于实际读过的官方原文。
package main

import (
	"fmt"
	"sort"
	"strings"
)

var n int
var fails []string

func check(label string, cond bool, detail string) {
	n++
	if !cond {
		fails = append(fails, label+"  "+detail)
	}
}

func acts(ls []Action) string {
	parts := make([]string, 0, len(ls))
	for _, a := range ls {
		parts = append(parts, a.Addr+"="+a.Act)
	}
	sort.Strings(parts)
	return strings.Join(parts, ",")
}

func strs(m map[string]map[string]string) string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return strings.Join(ks, ",")
}

func mustPlan(state map[string]map[string]string, config map[string]ResSpec,
	moves []Move, rm []Removed, im []Import) []Action {
	out, err := Plan(state, config, moves, rm, im)
	if err != nil {
		panic(err)
	}
	return out
}

func main() {
	// ---- 1. 地址解析 ----
	a := mustAddr("module.a[2].aws_instance.example")
	check("A1 module 带键", a.String() == "module.a[2].aws_instance.example", a.String())
	check("A2 模块解析", len(a.Modules) == 1 && a.Modules[0].Name == "a" &&
		*a.Modules[0].Key == "2" && a.RType == "aws_instance", a.String())
	b := mustAddr(`aws_instance.a["small"]`)
	check("A3 引号键", b.Key != nil && *b.Key == "small" && b.RName == "a", b.String())
	c := mustAddr(`aws_instance.web["a.b"]`)
	check("A4 键内点号不误切", c.Key != nil && *c.Key == "a.b" && c.RType == "aws_instance", c.String())
	d := mustAddr("data.aws_ami.x")
	check("A5 data 资源", d.IsData && d.RType == "aws_ami" && d.RName == "x", d.String())
	check("A6 整资源无键", !mustAddr("aws_instance.a").HasAnyKey(), "")
	check("A7 资源键也算带键", mustAddr("module.a.aws_instance.b[0]").HasAnyKey(), "")

	// ---- 2. 整资源级 moved ----
	st := map[string]map[string]string{
		"aws_instance.a[0]": {"t": "m3"}, "aws_instance.a[1]": {"t": "m3"}}
	cfg := map[string]ResSpec{"aws_instance.b": {Mode: "count", N: 2, Attrs: map[string]string{"t": "m3"}}}
	mv := []Move{NewMove("aws_instance.a", "aws_instance.b")}
	check("B1 整资源级判定", !mv[0].InstanceLevel(), "")
	check("B2 无 moved 时销毁+新建",
		acts(mustPlan(st, cfg, nil, nil, nil)) ==
			"aws_instance.a[0]=destroy,aws_instance.a[1]=destroy,aws_instance.b[0]=create,aws_instance.b[1]=create",
		acts(mustPlan(st, cfg, nil, nil, nil)))
	check("B3 moved 后不销毁",
		acts(mustPlan(st, cfg, mv, nil, nil)) ==
			"aws_instance.b[0]=noop,aws_instance.b[1]=noop",
		acts(mustPlan(st, cfg, mv, nil, nil)))

	// ---- 3. 新实例忽略 moved ----
	check("C1 空 state 走 create",
		acts(mustPlan(map[string]map[string]string{},
			map[string]ResSpec{"aws_instance.b": {Mode: "count", N: 2}}, mv, nil, nil)) ==
			"aws_instance.b[0]=create,aws_instance.b[1]=create", "")

	// ---- 4. 实例级 moved ----
	m2 := NewMove("aws_instance.a", `aws_instance.a["small"]`)
	check("D1 单侧带键即实例级", m2.InstanceLevel(), "")
	r := ApplyMoves(map[string]map[string]string{"aws_instance.a": {"t": "m3"}}, []Move{m2})
	check("D2 单实例→键", strs(r) == `aws_instance.a["small"]`, strs(r))
	r = ApplyMoves(map[string]map[string]string{"aws_instance.d[2]": {}},
		[]Move{NewMove("aws_instance.d[2]", "aws_instance.d")})
	check("D3 键→单实例", strs(r) == "aws_instance.d", strs(r))
	stc := map[string]map[string]string{
		"aws_instance.c[0]": {"t": "m3"}, "aws_instance.c[1]": {"t": "m3"}}
	r = ApplyMoves(stc, []Move{NewMove("aws_instance.c[0]", `aws_instance.c["small"]`),
		NewMove("aws_instance.c[1]", `aws_instance.c["tiny"]`)})
	check("D4 count→for_each 逐个映射",
		strs(r) == `aws_instance.c["small"],aws_instance.c["tiny"]`, strs(r))
	r = ApplyMoves(stc, []Move{NewMove("aws_instance.c[0]", `aws_instance.c["small"]`)})
	check("D5 实例级不误伤兄弟实例",
		strs(r) == `aws_instance.c["small"],aws_instance.c[1]`, strs(r))

	// ---- 5. 模块改名 / 拆分 ----
	r = ApplyMoves(map[string]map[string]string{"module.a.aws_instance.example": {}},
		[]Move{NewMove("module.a", "module.b")})
	check("E1 模块改名加前缀", strs(r) == "module.b.aws_instance.example", strs(r))
	r = ApplyMoves(map[string]map[string]string{
		"aws_instance.a": {}, "aws_instance.b": {}, "aws_instance.c": {}},
		[]Move{NewMove("aws_instance.a", "module.x.aws_instance.a"),
			NewMove("aws_instance.b", "module.x.aws_instance.b"),
			NewMove("aws_instance.c", "module.y.aws_instance.c")})
	check("E2 拆模块三块",
		strs(r) == "module.x.aws_instance.a,module.x.aws_instance.b,module.y.aws_instance.c", strs(r))
	r = ApplyMoves(map[string]map[string]string{"aws_instance.example": {}},
		[]Move{NewMove("aws_instance.example", "module.new[2].aws_instance.example")})
	check("E3 带键模块整体搬迁", strs(r) == "module.new[2].aws_instance.example", strs(r))

	// ---- 6. move 链 ----
	chain := []Move{NewMove("aws_instance.a", "aws_instance.b"),
		NewMove("aws_instance.b", "aws_instance.c")}
	check("F1 链首直达终点",
		strs(ApplyMoves(map[string]map[string]string{"aws_instance.a": {}}, chain)) == "aws_instance.c", "")
	check("F2 链中间可达终点",
		strs(ApplyMoves(map[string]map[string]string{"aws_instance.b": {}}, chain)) == "aws_instance.c", "")

	// ---- 7. 加 count 的自动搬迁 ----
	st1 := map[string]map[string]string{"aws_instance.a": {"t": "m3"}}
	cfg1 := map[string]ResSpec{"aws_instance.a": {Mode: "count", N: 2, Attrs: map[string]string{"t": "m3"}}}
	check("G1 加 count 自动搬 0 号",
		acts(mustPlan(st1, cfg1, nil, nil, nil)) ==
			"aws_instance.a[0]=noop,aws_instance.a[1]=create",
		acts(mustPlan(st1, cfg1, nil, nil, nil)))
	check("G2 显式 moved 提及后不再自动搬",
		acts(mustPlan(st1, cfg1, []Move{NewMove("aws_instance.a", "aws_instance.a[0]")}, nil, nil)) ==
			"aws_instance.a[0]=noop,aws_instance.a[1]=create", "")
	check("G3 for_each 无自动搬迁→旧对象被销毁",
		acts(mustPlan(st1, map[string]ResSpec{"aws_instance.a": {
			Mode: "for_each", Keys: []string{"small"}, Attrs: map[string]string{"t": "m3"}}},
			nil, nil, nil)) == `aws_instance.a=destroy,aws_instance.a["small"]=create`, "")

	// ---- 8. removed 块 ----
	check("H1 removed 默认 destroy",
		acts(mustPlan(st1, map[string]ResSpec{}, nil,
			[]Removed{{"aws_instance.a", true}}, nil)) == "aws_instance.a=destroy", "")
	check("H2 destroy=false 只移出 state",
		acts(mustPlan(st1, map[string]ResSpec{}, nil,
			[]Removed{{"aws_instance.a", false}}, nil)) == "aws_instance.a=forget", "")
	check("H3 无 removed 照常 destroy",
		acts(mustPlan(st1, map[string]ResSpec{}, nil, nil, nil)) == "aws_instance.a=destroy", "")

	// ---- 9. import 块 ----
	cfg2 := map[string]ResSpec{"aws_instance.a": {}}
	check("I1 有 import 走 import 而非 create",
		acts(mustPlan(map[string]map[string]string{}, cfg2, nil, nil,
			[]Import{{To: "aws_instance.a", ID: "i-123"}})) == "aws_instance.a=import", "")
	_, err := Plan(map[string]map[string]string{}, cfg2, nil, nil,
		[]Import{{To: "aws_instance.a", ID: "i", Identity: map[string]string{"x": "y"}}})
	check("I2 id/identity 互斥", err != nil, fmt.Sprint(err))
	_, err3 := Plan(map[string]map[string]string{}, cfg2, nil, nil,
		[]Import{{To: "aws_instance.zzz", ID: "i"}})
	check("I3 to 必须是已有 resource 块", err3 != nil, fmt.Sprint(err3))

	// ---- 10. 其它 ----
	func() {
		defer func() { check("J1 不能搬到 data 资源", recover() != nil, "") }()
		NewMove("aws_instance.a", "data.aws_instance.a")
	}()
	check("J2 属性变化判 update",
		acts(mustPlan(map[string]map[string]string{"aws_instance.a": {"t": "m1"}},
			map[string]ResSpec{"aws_instance.a": {Attrs: map[string]string{"t": "m2"}}},
			nil, nil, nil)) == "aws_instance.a=update", "")
	des := DesiredAddresses(map[string]ResSpec{"aws_instance.a": {Mode: "count", N: 3}})
	check("J3 count 展开顺序",
		des[0].Addr == "aws_instance.a[0]" && des[1].Addr == "aws_instance.a[1]" &&
			des[2].Addr == "aws_instance.a[2]", "")
	check("J4 整资源 moved 保留实例键",
		strs(ApplyMoves(map[string]map[string]string{"module.a.aws_instance.x[0]": {}},
			[]Move{NewMove("module.a.aws_instance.x", "module.a.aws_instance.y")})) ==
			"module.a.aws_instance.y[0]", "")

	fmt.Printf("checks=%d fail=%d\n", n, len(fails))
	for _, f := range fails {
		fmt.Println("  FAIL", f)
	}
}
