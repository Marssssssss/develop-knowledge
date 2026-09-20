// anscoll 自检：与 Python 版同口径，全部基于实际读过的官方原文。
package main

import (
	"fmt"
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

func avail() map[string]bool {
	return map[string]bool{
		"ansible.builtin.copy":        true,
		"community.general.kubevirt":  true,
		"community.general.my_inv":    true,
		"my_ns.my_coll.ping":          true,
		"other.coll.ping":             true,
		"my_ns.my_coll.lookup_x":      true,
	}
}

func req(spec, ver string) bool {
	ok, err := RequiresAnsibleOK(spec, ver)
	if err != nil {
		panic(err)
	}
	return ok
}

func main() {
	a := avail()

	// ---- 1. FQCN 解析 ----
	f := ParseFQCN("ns.coll.mod")
	check("A1 FQCN 三段拆分", f != nil && f.Namespace == "ns" && f.Collection == "coll" &&
		len(f.Rest) == 1 && f.Rest[0] == "mod", fmt.Sprint(f))
	check("A2 短名不是 FQCN", ParseFQCN("copy") == nil, "")
	check("A3 两段不是 FQCN", ParseFQCN("ns.coll") == nil, "")
	check("A4 playbook 名不允许连字符",
		IsValidPlaybookName("playbook1") && !IsValidPlaybookName("my-playbook"), "")

	// ---- 2. collections 是有序搜索路径 ----
	got, why := ResolvePlugin("ping", []string{"my_ns.my_coll", "other.coll"}, a, "module")
	check("B1 命中第一个集合", got == "my_ns.my_coll.ping" && why == "search_path", got+"/"+why)
	got, why = ResolvePlugin("ping", []string{"other.coll", "my_ns.my_coll"}, a, "module")
	check("B2 顺序决定命中谁", got == "other.coll.ping" && why == "search_path", got+"/"+why)
	got, why = ResolvePlugin("ping", nil, a, "module")
	check("B3 空搜索路径找不到", got == "" && why == "not_found", got+"/"+why)
	got, why = ResolvePlugin("my_ns.my_coll.ping", nil, a, "module")
	check("B4 FQCN 不需要搜索路径", got == "my_ns.my_coll.ping" && why == "fqcn", got+"/"+why)
	got, why = ResolvePlugin("nope.absent.ping", nil, a, "module")
	check("B5 不存在的 FQCN 报 not_found", got == "" && why == "not_found", got+"/"+why)

	// ---- 3. 非 action/module 类型必须用 FQCN ----
	for _, t := range []string{"lookup", "filter", "test"} {
		got, why = ResolvePlugin("lookup_x", []string{"my_ns.my_coll"}, a, t)
		check("C-"+t+" 在搜索路径里也要求 FQCN", got == "" && why == "requires_fqcn", got+"/"+why)
	}
	got, why = ResolvePlugin("lookup_x", []string{"my_ns.my_coll"}, a, "module")
	check("C4 module 类型可以用短名", got == "my_ns.my_coll.lookup_x" && why == "search_path", got+"/"+why)
	check("C5 非可搜索类型集合口径",
		nonSearchable["lookup"] && nonSearchable["filter"] && nonSearchable["test"] &&
			nonSearchable["vars"] && nonSearchable["cache"] && !nonSearchable["module"], "")

	// ---- 4. 角色不继承 playbook 的 collections ----
	got, why = ResolveInRole("ping", []string{"my_ns.my_coll"}, nil, a, "module")
	check("D1 角色没定义自己的 collections 时解析不到", got == "" && why == "not_found", got+"/"+why)
	got, why = ResolveInRole("ping", []string{"my_ns.my_coll"}, []string{"other.coll"}, a, "module")
	check("D2 角色用自己的列表", got == "other.coll.ping" && why == "search_path", got+"/"+why)
	got, why = ResolveInRole("ping", nil, []string{"my_ns.my_coll"}, a, "module")
	check("D3 playbook 为空而角色有值时照样命中", got == "my_ns.my_coll.ping", got+"/"+why)

	// ---- 5. module_utils 导入路径 ----
	check("E1 module_utils 导入约定",
		ModuleUtilsImport("community", "test_collection", "qradar") ==
			"ansible_collections.community.test_collection.plugins.module_utils.qradar", "")

	// ---- 6. requires_ansible 与预发布截断 ----
	check("F1 官方原例：2.11.0b1 满足 >=2.11", req(">=2.11", "2.11.0b1"), "")
	check("F2 2.10 不满足 >=2.11", !req(">=2.11", "2.10.0"), "")
	check("F3 区间说明符", req(">=2.10,<2.11", "2.10.3") && !req(">=2.10,<2.11", "2.11.0"), "")
	check("F4 逗号分隔是与", !req(">=2.10,<2.11", "2.12.0"), "")
	check("F5 正式版正常比较", req(">=2.9", "2.15.0"), "")
	check("F6 截断对 rc 同样成立", req(">=2.11", "2.11.0rc1"), "")
	check("F7 位数不同按位补齐", req(">=2.11", "2.11"), "")

	// ---- 7. plugin_routing ----
	rt := map[string]map[string]RoutingEntry{
		"inventory": {"kubevirt": RoutingEntry{Redirect: "community.general.kubevirt"}},
	}
	rr := RoutePlugin(rt, "inventory", "kubevirt")
	check("G1 redirect 改指向", rr.Target == "community.general.kubevirt" && rr.Fatal == "", fmt.Sprint(rr))
	rt = map[string]map[string]RoutingEntry{
		"inventory": {"my_inventory": RoutingEntry{Tombstone: &Tombstone{
			RemovalVersion: "2.0.0",
			WarningText:    "my_inventory has been removed. Please use other_inventory instead."}}},
	}
	rr = RoutePlugin(rt, "inventory", "my_inventory")
	check("G2 tombstone 是致命错误",
		rr.Target == "" && strings.Contains(rr.Fatal, "my_inventory") &&
			strings.Contains(rr.Fatal, "2.0.0"), fmt.Sprint(rr))
	rt = map[string]map[string]RoutingEntry{
		"modules": {"old": RoutingEntry{Deprecation: "use new", Redirect: "ns.coll.new"}},
	}
	rr = RoutePlugin(rt, "modules", "old")
	check("G3 改名时 deprecation 与 redirect 并存",
		rr.Target == "ns.coll.new" && len(rr.Warnings) == 1 &&
			rr.Warnings[0] == "use new" && rr.Fatal == "", fmt.Sprint(rr))
	rr = RoutePlugin(rt, "modules", "not_routed")
	check("G4 未登记的插件原样返回",
		rr.Target == "not_routed" && len(rr.Warnings) == 0 && rr.Fatal == "", fmt.Sprint(rr))

	fmt.Printf("checks=%d fail=%d\n", n, len(fails))
	for _, x := range fails {
		fmt.Println("  FAIL", x)
	}
}
