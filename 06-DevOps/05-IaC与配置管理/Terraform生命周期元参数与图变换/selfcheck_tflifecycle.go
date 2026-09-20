// tflifecycle 自检：与 Python 版同口径，全部基于实际读过的官方原文。
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

func hasErr(errs []string, sub string) bool {
	for _, e := range errs {
		if strings.Contains(e, sub) {
			return true
		}
	}
	return false
}

func acts(m map[string]string) string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	parts := make([]string, 0, len(ks))
	for _, k := range ks {
		parts = append(parts, k+"="+m[k])
	}
	return strings.Join(parts, ",")
}

func main() {
	// ---- 1. 基础动作 ----
	cfg := map[string]Resource{"aws_instance.web": {
		Name: "aws_instance.web", Attrs: map[string]string{"ami": "ami-2"}}}
	r := Plan(map[string]map[string]string{"aws_instance.web": {"ami": "ami-1"}}, cfg, nil)
	check("A1 普通属性变化是 update", r.Actions["aws_instance.web"] == "update" && len(r.Errors) == 0, acts(r.Actions))
	check("A2 默认非 CBD", !r.CBD["aws_instance.web"], "")

	cfg = map[string]Resource{"aws_instance.web": {
		Name: "aws_instance.web", Attrs: map[string]string{"ami": "ami-2"},
		RequiresReplace: map[string]bool{"ami": true}}}
	r = Plan(map[string]map[string]string{"aws_instance.web": {"ami": "ami-1"}}, cfg, nil)
	check("A3 不可原地改的属性→replace", r.Actions["aws_instance.web"] == "replace", acts(r.Actions))
	check("A4 默认先销毁后新建", r.Order["aws_instance.web"] == [2]string{"destroy", "create"}, fmt.Sprint(r.Order))

	// ---- 2. create_before_destroy ----
	cfg = map[string]Resource{"aws_instance.web": {
		Name: "aws_instance.web", Attrs: map[string]string{"ami": "ami-2"},
		RequiresReplace: map[string]bool{"ami": true},
		Lc:              Lifecycle{CBD: boolp(true)}}}
	r = Plan(map[string]map[string]string{"aws_instance.web": {"ami": "ami-1"}}, cfg, nil)
	check("B1 CBD 显式置位", r.CBD["aws_instance.web"], "")
	check("B2 替换次序反过来", r.Order["aws_instance.web"] == [2]string{"create", "destroy"}, fmt.Sprint(r.Order))

	// ---- 3. CBD 传播 ----
	cfg = map[string]Resource{
		"aws_instance.web": {Name: "aws_instance.web", Attrs: map[string]string{"ami": "ami-2"},
			RequiresReplace: map[string]bool{"ami": true}, Lc: Lifecycle{CBD: boolp(true)}},
		"aws_eip.ip": {Name: "aws_eip.ip"},
	}
	deps := map[string][]string{"aws_instance.web": {"aws_eip.ip"}}
	eff, conflicts := PropagateCBD(cfg, deps)
	check("C1 依赖方被隐式置位", eff["aws_eip.ip"], "")
	check("C2 未显式写 false 时无冲突", len(conflicts) == 0, fmt.Sprint(conflicts))

	cfg3 := map[string]Resource{
		"a": {Name: "a", Lc: Lifecycle{CBD: boolp(true)}},
		"b": {Name: "b"}, "c": {Name: "c"},
	}
	eff, _ = PropagateCBD(cfg3, map[string][]string{"a": {"b"}, "b": {"c"}})
	check("C3 传播是传递的", eff["b"] && eff["c"], "")
	eff, _ = PropagateCBD(cfg3, map[string][]string{"b": {"a"}})
	check("C4 不会反向传播给依赖方", eff["a"] && !eff["b"], "")

	cfg4 := map[string]Resource{
		"a": {Name: "a", Lc: Lifecycle{CBD: boolp(true)}},
		"b": {Name: "b", Lc: Lifecycle{CBD: fboolp()}},
	}
	eff, conflicts = PropagateCBD(cfg4, map[string][]string{"a": {"b"}})
	check("C5 显式 false 撞上隐式 true 记为冲突", len(conflicts) == 1 && conflicts[0] == "b", fmt.Sprint(conflicts))
	r = Plan(map[string]map[string]string{}, cfg4, map[string][]string{"a": {"b"}})
	check("C6 冲突会进 errors", hasErr(r.Errors, "成环"), fmt.Sprint(r.Errors))

	// ---- 4. prevent_destroy ----
	cfg = map[string]Resource{"aws_db_instance.db": {
		Name: "aws_db_instance.db", Attrs: map[string]string{"size": "large"},
		RequiresReplace: map[string]bool{"size": true}, Lc: Lifecycle{PreventDestroy: true}}}
	r = Plan(map[string]map[string]string{"aws_db_instance.db": {"size": "small"}}, cfg, nil)
	check("D1 prevent_destroy 拒绝替换", hasErr(r.Errors, "prevent_destroy"), fmt.Sprint(r.Errors))
	r = Plan(map[string]map[string]string{"aws_db_instance.db": {"size": "small"}},
		map[string]Resource{"aws_db_instance.db": {
			Name: "aws_db_instance.db", Attrs: map[string]string{"size": "small"},
			Lc: Lifecycle{PreventDestroy: true}}}, nil)
	check("D2 无变化时放行", r.Actions["aws_db_instance.db"] == "noop" && len(r.Errors) == 0, acts(r.Actions))
	r = Plan(map[string]map[string]string{"aws_db_instance.db": {"size": "small"}}, map[string]Resource{}, nil)
	check("D3 配置移除后照常 destroy", r.Actions["aws_db_instance.db"] == "destroy", acts(r.Actions))
	r = Plan(map[string]map[string]string{}, cfg, nil)
	check("D4 仅创建时不触发 prevent_destroy",
		r.Actions["aws_db_instance.db"] == "create" && len(r.Errors) == 0, acts(r.Actions))

	// ---- 5. ignore_changes ----
	cfg = map[string]Resource{"aws_instance.web": {
		Name: "aws_instance.web", Attrs: map[string]string{"tags": "b"},
		Lc: Lifecycle{IgnoreChanges: []string{"tags"}}}}
	r = Plan(map[string]map[string]string{"aws_instance.web": {"tags": "a"}}, cfg, nil)
	check("E1 被忽略的属性不产生 update", r.Actions["aws_instance.web"] == "noop", acts(r.Actions))
	cfg = map[string]Resource{"aws_instance.web": {
		Name: "aws_instance.web", Attrs: map[string]string{"tags": "b", "ami": "x"},
		Lc: Lifecycle{IgnoreChanges: []string{"tags"}}}}
	r = Plan(map[string]map[string]string{"aws_instance.web": {"tags": "a", "ami": "y"}}, cfg, nil)
	check("E2 只忽略列出的属性", r.Actions["aws_instance.web"] == "update", acts(r.Actions))
	cfg = map[string]Resource{"aws_instance.web": {
		Name: "aws_instance.web", Attrs: map[string]string{"tags": "b"}, Lc: Lifecycle{IgnoreAll: true}}}
	r = Plan(map[string]map[string]string{"aws_instance.web": {"tags": "a"}}, cfg, nil)
	check("E3 all 时永不 update", r.Actions["aws_instance.web"] == "noop", acts(r.Actions))
	r = Plan(map[string]map[string]string{}, cfg, nil)
	check("E4 ignore_changes 不影响 create", r.Actions["aws_instance.web"] == "create", acts(r.Actions))
	cfg = map[string]Resource{"aws_instance.web": {
		Name: "aws_instance.web", Attrs: map[string]string{"ami": "b"},
		RequiresReplace: map[string]bool{"ami": true},
		Lc:              Lifecycle{IgnoreChanges: []string{"ami"}}}}
	r = Plan(map[string]map[string]string{"aws_instance.web": {"ami": "a"}}, cfg, nil)
	check("E5 模型口径：忽略项不进 diff 故不触发 replace",
		r.Actions["aws_instance.web"] == "noop", acts(r.Actions))

	// ---- 6. replace_triggered_by ----
	cfg = map[string]Resource{
		"aws_ecs_service.svc": {Name: "aws_ecs_service.svc", Attrs: map[string]string{"task": "2"}},
		"aws_appautoscaling_target.t": {Name: "aws_appautoscaling_target.t",
			Lc: Lifecycle{ReplaceTriggeredBy: []string{"aws_ecs_service.svc"}}},
	}
	st := map[string]map[string]string{
		"aws_ecs_service.svc": {"task": "1"}, "aws_appautoscaling_target.t": {}}
	r = Plan(st, cfg, nil)
	check("F1 目标有 update 即触发替换", r.Actions["aws_appautoscaling_target.t"] == "replace", acts(r.Actions))
	check("F2 触发原因被记录",
		len(r.Triggers["aws_appautoscaling_target.t"]) == 1 &&
			r.Triggers["aws_appautoscaling_target.t"][0] == "aws_ecs_service.svc", fmt.Sprint(r.Triggers))
	r = Plan(map[string]map[string]string{
		"aws_ecs_service.svc": {"task": "2"}, "aws_appautoscaling_target.t": {}}, cfg, nil)
	check("F3 目标无变化则不触发", r.Actions["aws_appautoscaling_target.t"] == "noop", acts(r.Actions))

	cfg2 := map[string]Resource{
		"aws_ecs_service.svc": {Name: "aws_ecs_service.svc", Attrs: map[string]string{"task": "2"}},
		"t": {Name: "t", Lc: Lifecycle{ReplaceTriggeredBy: []string{"aws_ecs_service.svc.task"}}},
	}
	r = Plan(map[string]map[string]string{"aws_ecs_service.svc": {"task": "1"}, "t": {}}, cfg2, nil)
	check("F4 属性级引用按值变否判定", r.Actions["t"] == "replace", acts(r.Actions))
	r = Plan(map[string]map[string]string{"aws_ecs_service.svc": {"task": "2"}, "t": {}}, cfg2, nil)
	check("F5 属性值未变不触发", r.Actions["t"] == "noop", acts(r.Actions))

	cfg5 := map[string]Resource{"t": {Name: "t",
		Lc: Lifecycle{ReplaceTriggeredBy: []string{"local.x"}}}}
	r = Plan(map[string]map[string]string{"t": {}}, cfg5, nil)
	check("F6 不能引用 local 值", hasErr(r.Errors, "只能引用托管资源"), fmt.Sprint(r.Errors))
	check("F7 官方触发条件只有 update/replace",
		updateOrReplace("update") && updateOrReplace("replace") &&
			!updateOrReplace("create") && !updateOrReplace("noop"), "")

	// ---- 7. precondition ----
	cfg6 := map[string]Resource{"t": {Name: "t", Attrs: map[string]string{"a": "1"},
		Lc: Lifecycle{Preconditions: []Precond{{false, "nope"}}}}}
	r = Plan(map[string]map[string]string{}, cfg6, nil)
	check("G1 precondition 失败即报错", hasErr(r.Errors, "precondition"), fmt.Sprint(r.Errors))

	// ---- 8. 销毁图与 CBD 翻转 ----
	acts1 := map[string]string{"a": "replace", "b": "replace"}
	e1 := DestroyGraph(map[string][]string{"a": {"b"}}, acts1, map[string]bool{"a": false, "b": false})
	check("H1 默认先销毁依赖方再销毁被依赖方", len(e1) == 1 && e1[0] == [2]string{"a", "b"}, fmt.Sprint(e1))
	e1 = DestroyGraph(map[string][]string{"a": {"b"}}, acts1, map[string]bool{"a": true, "b": true})
	check("H2 CBD 时销毁边翻转", len(e1) == 1 && e1[0] == [2]string{"b", "a"}, fmt.Sprint(e1))
	check("H3 翻转后与另一条边成环",
		HasCycle([][2]string{{"b", "a"}, {"a", "b"}}, []string{"a", "b"}), "")
	check("H4 无环时不误报",
		!HasCycle([][2]string{{"a", "b"}, {"b", "c"}}, []string{"a", "b", "c"}), "")
	e1 = DestroyGraph(map[string][]string{"a": {"b"}},
		map[string]string{"a": "replace", "b": "noop"}, map[string]bool{"a": true, "b": true})
	check("H5 对方不销毁则不建边", len(e1) == 0, fmt.Sprint(e1))

	fmt.Printf("checks=%d fail=%d\n", n, len(fails))
	for _, f := range fails {
		fmt.Println("  FAIL", f)
	}
}
