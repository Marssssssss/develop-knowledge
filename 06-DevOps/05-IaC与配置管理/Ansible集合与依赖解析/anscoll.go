// Ansible Collections 的 FQCN 解析、collections 搜索路径作用域与 runtime.yml 元数据。
// 口径与 Python 版一致，来自实际读过的官方原文（见 README 参考资料）。
package main

import (
	"strconv"
	"strings"
)

// 官方："an FQCN is still required for non-action or module plugins
// (for example, lookups, filters, and tests)"
var nonSearchable = map[string]bool{
	"lookup": true, "filter": true, "test": true,
	"connection": true, "callback": true, "vars": true, "cache": true,
}

type FQCN struct {
	Namespace  string
	Collection string
	Rest       []string
}

// ParseFQCN 返回 nil 表示不是 FQCN。
func ParseFQCN(ref string) *FQCN {
	parts := strings.Split(ref, ".")
	if len(parts) < 3 {
		return nil
	}
	return &FQCN{parts[0], parts[1], parts[2:]}
}

// IsValidPlaybookName：官方说集合里的 playbook 名不允许出现连字符。
func IsValidPlaybookName(name string) bool {
	return name != "" && !strings.Contains(name, "-")
}

// ModuleUtilsImport：from ansible_collections.{ns}.{coll}.plugins.module_utils.{util} import ...
func ModuleUtilsImport(namespace, collection, util string) string {
	return "ansible_collections." + namespace + "." + collection + ".plugins.module_utils." + util
}

// ResolvePlugin 按 collections 列表顺序找短名插件，返回 (fqcn, reason)。
// reason ∈ fqcn / search_path / requires_fqcn / not_found
func ResolvePlugin(shortName string, collections []string, available map[string]bool,
	ptype string) (string, string) {
	if ParseFQCN(shortName) != nil {
		if available[shortName] {
			return shortName, "fqcn"
		}
		return "", "not_found"
	}
	if nonSearchable[ptype] {
		return "", "requires_fqcn"
	}
	for _, coll := range collections {
		cand := coll + "." + shortName
		if available[cand] {
			return cand, "search_path"
		}
	}
	return "", "not_found"
}

// ResolveInRole：角色内只看角色自己的 collections，不继承 playbook 的。
func ResolveInRole(shortName string, playbookCollections, roleCollections []string,
	available map[string]bool, ptype string) (string, string) {
	return ResolvePlugin(shortName, roleCollections, available, ptype)
}

// releaseTuple 截掉预发布段（官方：Ansible 会 truncate prerelease segments）。
func releaseTuple(v string) [3]int {
	core := strings.Split(v, "+")[0]
	seps := []string{"a", "b", "rc", "dev", ".post"}
	best := -1
	for _, s := range seps {
		if i := strings.Index(core[1:], s); i >= 0 && (best < 0 || i+1 < best) {
			best = i + 1
		}
	}
	if best > 0 {
		core = core[:best]
	}
	core = strings.TrimRight(core, ".")
	var out [3]int
	i := 0
	for _, p := range strings.Split(core, ".") {
		if i >= 3 {
			break
		}
		digits := ""
		for _, ch := range p {
			if ch >= '0' && ch <= '9' {
				digits += string(ch)
			}
		}
		n := 0
		if digits != "" {
			n, _ = strconv.Atoi(digits)
		}
		out[i] = n
		i++
	}
	return out
}

func cmpTuple(a, b [3]int) int {
	for i := 0; i < 3; i++ {
		if a[i] < b[i] {
			return -1
		}
		if a[i] > b[i] {
			return 1
		}
	}
	return 0
}

// RequiresAnsibleOK：meta/runtime.yml 的 requires_ansible，PEP440 说明符，逗号分隔。
// 官方：Ansible 2.11.0b1 与 requires_ansible ">=2.11" 兼容（预发布段被截断）。
func RequiresAnsibleOK(spec, ansibleVersion string) (bool, error) {
	ver := releaseTuple(ansibleVersion)
	for _, raw := range strings.Split(spec, ",") {
		c := strings.TrimSpace(raw)
		if c == "" {
			continue
		}
		matched := false
		for _, op := range []string{">=", "<=", "!=", "=", ">", "<"} {
			if !strings.HasPrefix(c, op) {
				continue
			}
			want := releaseTuple(strings.TrimSpace(c[len(op):]))
			r := cmpTuple(ver, want)
			var ok bool
			switch op {
			case ">=":
				ok = r >= 0
			case "<=":
				ok = r <= 0
			case "=":
				ok = r == 0
			case "!=":
				ok = r != 0
			case ">":
				ok = r > 0
			case "<":
				ok = r < 0
			}
			if !ok {
				return false, nil
			}
			matched = true
			break
		}
		if !matched {
			return false, errUnrecognized(c)
		}
	}
	return true, nil
}

func errUnrecognized(c string) error {
	return &specErr{c}
}

type specErr struct{ c string }

func (e *specErr) Error() string { return "无法识别的说明符: " + e.c }

type Tombstone struct {
	RemovalVersion string
	WarningText    string
}

type RoutingEntry struct {
	Redirect    string
	Deprecation string
	Tombstone   *Tombstone
}

type RouteResult struct {
	Target   string
	Warnings []string
	Fatal    string
}

// RoutePlugin 应用 plugin_routing：redirect / deprecation / tombstone。
func RoutePlugin(routing map[string]map[string]RoutingEntry, ptype, pluginName string) RouteResult {
	r, ok := routing[ptype][pluginName]
	if !ok {
		return RouteResult{pluginName, nil, ""}
	}
	var res RouteResult
	if r.Tombstone != nil {
		res.Fatal = pluginName + " 已被移除（removal_version=" + r.Tombstone.RemovalVersion +
			"）：" + r.Tombstone.WarningText
		return res
	}
	if r.Deprecation != "" {
		res.Warnings = append(res.Warnings, r.Deprecation)
	}
	if r.Redirect != "" {
		res.Target = r.Redirect
		return res
	}
	res.Target = pluginName
	return res
}
