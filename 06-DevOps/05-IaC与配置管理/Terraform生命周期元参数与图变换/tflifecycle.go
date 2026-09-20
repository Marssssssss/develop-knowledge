// Terraform lifecycle 元参数与依赖图变换模型。
// 口径与 Python 版一致，来自实际读过的官方原文（见 README 参考资料）。
package main

import (
	"sort"
	"strings"
)

type Precond struct {
	Condition    bool
	ErrorMessage string
}

type Lifecycle struct {
	CBD                *bool // nil 表示配置里没写
	PreventDestroy     bool
	IgnoreChanges      []string
	IgnoreAll          bool
	ReplaceTriggeredBy []string
	Preconditions      []Precond
}

type Resource struct {
	Name            string
	Attrs           map[string]string
	RequiresReplace map[string]bool
	Lc              Lifecycle
}

func boolp(b bool) *bool { return &b }
func fboolp() *bool      { x := false; return &x }

var unmanaged = []string{"data.", "var.", "local.", "each.", "count."}

func isManagedRef(ref string) bool {
	for _, p := range unmanaged {
		if strings.HasPrefix(ref, p) {
			return false
		}
	}
	return true
}

// PropagateCBD：create_before_destroy 沿依赖边向**被依赖方**传播。
// 官方：CBD 开在 A 上而 A 依赖 B，则 Terraform 隐式给 B 也开上并写进 state；
// B 再显式写 false 会被拒绝，因为那会隐含依赖图中的环。
func PropagateCBD(resources map[string]Resource, deps map[string][]string) (map[string]bool, []string) {
	eff := map[string]bool{}
	for n, r := range resources {
		eff[n] = r.Lc.CBD != nil && *r.Lc.CBD
	}
	for changed := true; changed; {
		changed = false
		for n := range eff {
			if !eff[n] {
				continue
			}
			for _, d := range deps[n] {
				if _, ok := eff[d]; ok && !eff[d] {
					eff[d] = true
					changed = true
				}
			}
		}
	}
	var conflicts []string
	for n, r := range resources {
		if r.Lc.CBD != nil && !*r.Lc.CBD && eff[n] {
			conflicts = append(conflicts, n)
		}
	}
	sort.Strings(conflicts)
	return eff, conflicts
}

func ignored(r Resource, attr string) bool {
	if r.Lc.IgnoreAll {
		return true
	}
	for _, a := range r.Lc.IgnoreChanges {
		if a == attr {
			return true
		}
	}
	return false
}

func updateOrReplace(act string) bool { return act == "update" || act == "replace" }

type PlanResult struct {
	Actions   map[string]string
	Errors    []string
	CBD       map[string]bool
	Order     map[string][2]string
	Triggers  map[string][]string
}

func Plan(state map[string]map[string]string, config map[string]Resource,
	deps map[string][]string) PlanResult {

	var errors []string
	eff, conflicts := PropagateCBD(config, deps)
	for _, n := range conflicts {
		errors = append(errors, n+": 依赖方开启了 create_before_destroy，此处不能显式写 false（会在依赖图中成环）")
	}

	base := map[string]string{}
	names := make([]string, 0, len(config))
	for n := range config {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, name := range names {
		res := config[name]
		for _, pre := range res.Lc.Preconditions {
			if !pre.Condition {
				errors = append(errors, name+": precondition 失败: "+pre.ErrorMessage)
			}
		}
		so, ok := state[name]
		if !ok {
			base[name] = "create"
			continue
		}
		act, rep := "noop", false
		keys := make([]string, 0, len(res.Attrs))
		for k := range res.Attrs {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			if so[k] == res.Attrs[k] || ignored(res, k) {
				continue
			}
			if res.RequiresReplace[k] {
				rep = true
			} else {
				act = "update"
			}
		}
		if rep {
			act = "replace"
		}
		base[name] = act
	}

	triggers := map[string][]string{}
	for _, name := range names {
		res := config[name]
		for _, ref := range res.Lc.ReplaceTriggeredBy {
			if !isManagedRef(ref) {
				errors = append(errors, name+": replace_triggered_by 只能引用托管资源: "+ref)
				continue
			}
			target, attr := ref, ""
			if _, ok := config[ref]; !ok && strings.Contains(ref, ".") {
				i := strings.LastIndex(ref, ".")
				target, attr = ref[:i], ref[i+1:]
			}
			tr, ok := config[target]
			if !ok {
				errors = append(errors, name+": replace_triggered_by 引用的资源不存在: "+ref)
				continue
			}
			fired := false
			if attr == "" {
				fired = updateOrReplace(base[target])
			} else {
				fired = state[target][attr] != tr.Attrs[attr]
			}
			if fired {
				triggers[name] = append(triggers[name], ref)
			}
		}
	}
	for name := range triggers {
		if base[name] == "noop" || base[name] == "update" {
			base[name] = "replace"
		}
	}

	for _, name := range names {
		res := config[name]
		if res.Lc.PreventDestroy && (base[name] == "replace" || base[name] == "destroy") {
			errors = append(errors, name+": prevent_destroy=true，拒绝会销毁该对象的计划")
		}
	}

	actions := map[string]string{}
	for k, v := range base {
		actions[k] = v
	}
	for name := range state {
		if _, ok := config[name]; !ok {
			actions[name] = "destroy"
		}
	}

	order := map[string][2]string{}
	for name, act := range actions {
		if act != "replace" {
			continue
		}
		if eff[name] {
			order[name] = [2]string{"create", "destroy"}
		} else {
			order[name] = [2]string{"destroy", "create"}
		}
	}
	sort.Strings(errors)
	return PlanResult{actions, errors, eff, order, triggers}
}

// DestroyGraph：X 依赖 Y ⇒ 默认先销毁 X 再销毁 Y；
// X 开了 CBD 时约束翻转为「先销毁 Y 再销毁 X」。
func DestroyGraph(deps map[string][]string, actions map[string]string, eff map[string]bool) [][2]string {
	xs := make([]string, 0, len(deps))
	for x := range deps {
		xs = append(xs, x)
	}
	sort.Strings(xs)
	var edges [][2]string
	for _, x := range xs {
		if actions[x] != "destroy" && actions[x] != "replace" {
			continue
		}
		for _, y := range deps[x] {
			if actions[y] != "destroy" && actions[y] != "replace" {
				continue
			}
			if eff[x] {
				edges = append(edges, [2]string{y, x})
			} else {
				edges = append(edges, [2]string{x, y})
			}
		}
	}
	return edges
}

func HasCycle(edges [][2]string, nodes []string) bool {
	adj := map[string][]string{}
	for _, n := range nodes {
		adj[n] = nil
	}
	for _, e := range edges {
		adj[e[0]] = append(adj[e[0]], e[1])
	}
	color := map[string]int{}
	var dfs func(string) bool
	dfs = func(u string) bool {
		color[u] = 1
		for _, v := range adj[u] {
			if color[v] == 1 {
				return true
			}
			if color[v] == 0 && dfs(v) {
				return true
			}
		}
		color[u] = 2
		return false
	}
	for _, n := range nodes {
		if color[n] == 0 && dfs(n) {
			return true
		}
	}
	return false
}
