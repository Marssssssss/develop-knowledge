// Terraform 声明式状态重构：配置展开与计划生成（地址与 moved 部分见 tfrefactor.go）。
// 口径与 Python 版一致，来自实际读过的官方原文（见 README 参考资料）。
package main

import (
	"fmt"
	"sort"
)

// ---- 配置与计划 ----

type ResSpec struct {
	Mode  string            // "" | "count" | "for_each"
	N     int               // count
	Keys  []string          // for_each
	Attrs map[string]string // 期望属性
}

type Desired struct {
	Addr  string
	Attrs map[string]string
}

func DesiredAddresses(config map[string]ResSpec) []Desired {
	var out []Desired
	names := make([]string, 0, len(config))
	for n := range config {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		spec := config[n]
		a := mustAddr(n)
		var keys []*string
		switch spec.Mode {
		case "count":
			for i := 0; i < spec.N; i++ {
				keys = append(keys, sptr(fmt.Sprint(i)))
			}
		case "for_each":
			for _, k := range spec.Keys {
				keys = append(keys, sptr(k))
			}
		default:
			keys = append(keys, nil)
		}
		for _, k := range keys {
			aa := Addr{a.Modules, a.RType, a.RName, k, a.IsData}
			out = append(out, Desired{aa.String(), spec.Attrs})
		}
	}
	return out
}

type Removed struct {
	From    string
	Destroy bool // removed 块默认 destroy=true
}

type Import struct {
	To       string
	ID       string
	Identity map[string]string
}

type Action struct {
	Addr string
	Act  string // create/import/noop/update/destroy/forget
}

func AutoCountMove(state map[string]map[string]string, config map[string]ResSpec, moves []Move) map[string]map[string]string {
	mentioned := map[string]bool{}
	for _, mv := range moves {
		for _, a := range []Addr{mv.From, mv.To} {
			if !a.IsModuleOnly() {
				mentioned[Addr{a.Modules, a.RType, a.RName, nil, a.IsData}.String()] = true
			}
		}
	}
	cur := map[string]map[string]string{}
	for k, v := range state {
		cur[k] = v
	}
	for n, spec := range config {
		if spec.Mode != "count" {
			continue
		}
		a := mustAddr(n)
		plain := Addr{a.Modules, a.RType, a.RName, nil, a.IsData}.String()
		if mentioned[plain] {
			continue
		}
		if obj, ok := cur[plain]; ok {
			delete(cur, plain)
			cur[Addr{a.Modules, a.RType, a.RName, sptr("0"), a.IsData}.String()] = obj
		}
	}
	return cur
}

func Plan(state map[string]map[string]string, config map[string]ResSpec,
	moves []Move, removed []Removed, imports []Import) ([]Action, error) {

	removedMap := map[string]bool{}
	for _, r := range removed {
		removedMap[mustAddr(r.From).String()] = r.Destroy
	}
	imp := map[string]bool{}
	for _, i := range imports {
		to := mustAddr(i.To).String()
		if i.ID != "" && len(i.Identity) > 0 {
			return nil, fmt.Errorf("import 的 id 与 identity 互斥: %s", i.To)
		}
		imp[to] = true
	}

	cur := ApplyMoves(state, moves)
	cur = AutoCountMove(cur, config, moves)
	desired := DesiredAddresses(config)

	dset := map[string]bool{}
	for _, d := range desired {
		dset[d.Addr] = true
	}
	for to := range imp {
		if !dset[to] {
			return nil, fmt.Errorf("import 的 to 必须匹配已有 resource 块地址: %s", to)
		}
	}

	var out []Action
	for _, d := range desired {
		if obj, ok := cur[d.Addr]; ok {
			act := "noop"
			if !sameAttrs(obj, d.Attrs) {
				act = "update"
			}
			out = append(out, Action{d.Addr, act})
		} else if imp[d.Addr] {
			out = append(out, Action{d.Addr, "import"})
		} else {
			out = append(out, Action{d.Addr, "create"})
		}
	}
	for addr := range cur {
		if dset[addr] {
			continue
		}
		if d, ok := removedMap[addr]; ok {
			if d {
				out = append(out, Action{addr, "destroy"})
			} else {
				out = append(out, Action{addr, "forget"})
			}
		} else {
			out = append(out, Action{addr, "destroy"})
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Addr < out[j].Addr })
	return out, nil
}

func sameAttrs(a, b map[string]string) bool {
	if len(a) != len(b) {
		return false
	}
	for k, v := range a {
		if b[k] != v {
			return false
		}
	}
	return true
}
