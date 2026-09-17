// Argo CD diff 定制与去噪(Go 对照实现)。配套 argocd_sync.go 覆盖同步/自愈/prune。
//
// 权威依据: argo-cd.readthedocs.io 的 user-guide/diffing/。
//
// 与 Python 版的差异(有意为之, README「对比」段亦有标注):
//   - JSON Pointer 删除只支持**对象路径**(RFC6902 的数组下标删除在泛型 map 上需要
//     把重建后的切片写回父容器, 本 demo 不铺开);数组场景统一走 jq 子集的 [] 展开,
//     语义更清楚。
//   - DiffRule 的 group / kind 留空即视为通配 "*"(Go 零值不便表达 Python 的缺省键)。
// 值规整(Quantity / knownTypeFields / 摘要)在同包的 argocd_canon.go。
package main

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
)

// DiffRule 对应 ignoreDifferences 的一个条目。
type DiffRule struct {
	Group                 string
	Kind                  string
	Name                  string
	Namespace             string
	JSONPointers          []string
	JQPathExpressions     []string
	ManagedFieldsManagers []string
}

// DeepCopyJSON 用 JSON 往返做深拷贝(泛型 JSON 在 Go 里唯一可靠的深拷贝方式)。
func DeepCopyJSON(m map[string]interface{}) map[string]interface{} {
	b, err := json.Marshal(m)
	if err != nil {
		return map[string]interface{}{}
	}
	out := map[string]interface{}{}
	if err := json.Unmarshal(b, &out); err != nil {
		return map[string]interface{}{}
	}
	return out
}

// CanonicalJSON 是用于比对的规范化序列化: encoding/json 对 map 键自动排序,
// 与 Python 版 json.dumps(sort_keys=True) 等价。
func CanonicalJSON(v interface{}) string {
	b, err := json.Marshal(v)
	if err != nil {
		return ""
	}
	return string(b)
}

// UnescapePointerToken: RFC6902 的 ~1 -> /、~0 -> ~(必须按此顺序)。
func UnescapePointerToken(tok string) string {
	return strings.ReplaceAll(strings.ReplaceAll(tok, "~1", "/"), "~0", "~")
}

// DeletePointer 按 JSON Pointer 就地删除(不存在则静默返回), 仅支持对象路径。
func DeletePointer(obj map[string]interface{}, pointer string) {
	toks := []string{}
	for _, t := range strings.Split(pointer, "/")[1:] {
		toks = append(toks, UnescapePointerToken(t))
	}
	if len(toks) == 0 {
		return
	}
	deleteNested(obj, toks)
}

// deleteNested 在对象内逐层下钻并删除末段键。
func deleteNested(obj map[string]interface{}, toks []string) {
	cur := obj
	for _, t := range toks[:len(toks)-1] {
		child, ok := cur[t].(map[string]interface{})
		if !ok {
			return
		}
		cur = child
	}
	delete(cur, toks[len(toks)-1])
}

// JQPath 是解析后的 jq 子集: 段列表 + 通配下标 + 可选 select 过滤。
type JQPath struct {
	Parts     []string
	Wild      int // -1 表示无通配
	HasSelect bool
	SelectKey string
	SelectVal string
}

// ParseJQPath 解析本 demo 支持的 jq 子集: .a.b / .a[].b / .a[]?.b.c / + 尾部 select。
func ParseJQPath(expr string) (JQPath, error) {
	p := JQPath{Wild: -1}
	body := strings.TrimSpace(expr)
	if i := strings.Index(body, "|"); i >= 0 {
		head, tail := body[:i], strings.TrimSpace(body[i+1:])
		inner, err := parseSelect(tail)
		if err != nil {
			return p, err
		}
		p.HasSelect, p.SelectKey, p.SelectVal = true, inner[0], inner[1]
		body = strings.TrimSpace(head)
	}
	if !strings.HasPrefix(body, ".") {
		return p, fmt.Errorf("jq 路径必须以 . 开头: %s", body)
	}
	for _, seg := range strings.Split(body[1:], ".") {
		if seg == "" {
			continue
		}
		if strings.HasSuffix(seg, "[]?") {
			p.Wild = len(p.Parts)
			p.Parts = append(p.Parts, strings.TrimSuffix(seg, "[]?"))
			continue
		}
		if strings.HasSuffix(seg, "[]") {
			p.Wild = len(p.Parts)
			p.Parts = append(p.Parts, strings.TrimSuffix(seg, "[]"))
			continue
		}
		p.Parts = append(p.Parts, seg)
	}
	return p, nil
}

// parseSelect 只认 select(.k == "v") 这一种形态, 其余一律报错(本 demo 的口径)。
func parseSelect(tail string) ([2]string, error) {
	var out [2]string
	if !strings.HasPrefix(tail, "select(") || !strings.HasSuffix(tail, ")") {
		return out, fmt.Errorf("不支持的 select 子句: %s", tail)
	}
	inner := strings.TrimSpace(tail[len("select(") : len(tail)-1])
	eq := strings.Index(inner, "==")
	if eq < 0 {
		return out, fmt.Errorf("不支持的 select 子句: %s", tail)
	}
	key := strings.TrimSpace(inner[:eq])
	val := strings.TrimSpace(inner[eq+2:])
	if !strings.HasPrefix(key, ".") || len(key) < 2 ||
		!strings.HasPrefix(val, "\"") || !strings.HasSuffix(val, "\"") || len(val) < 2 {
		return out, fmt.Errorf("不支持的 select 子句: %s", tail)
	}
	return [2]string{key[1:], val[1 : len(val)-1]}, nil
}

// ApplyJQPath 按 jq 子集就地删除匹配项。[] / []? 可以出现在任意一层, 都把后半段
// 路径分发到数组的每个元素上;省略 [] 时 jq 不会自动展开数组, 路径直接落空。
func ApplyJQPath(obj map[string]interface{}, expr string) error {
	p, err := ParseJQPath(expr)
	if err != nil {
		return err
	}
	if p.Wild < 0 {
		deleteNested(obj, p.Parts)
		return nil
	}
	node := obj
	for _, seg := range p.Parts[:p.Wild] {
		child, ok := node[seg].(map[string]interface{})
		if !ok {
			return nil
		}
		node = child
	}
	arr, ok := node[p.Parts[p.Wild]].([]interface{})
	if !ok {
		return nil
	}
	rest := p.Parts[p.Wild+1:]
	if len(rest) > 0 {
		for _, item := range arr {
			if m, ok := item.(map[string]interface{}); ok {
				deleteNested(m, rest)
			}
		}
		return nil
	}
	keep := []interface{}{}
	for _, item := range arr {
		if p.HasSelect {
			if m, ok := item.(map[string]interface{}); ok && m[p.SelectKey] == p.SelectVal {
				continue
			}
		}
		keep = append(keep, item)
	}
	node[p.Parts[p.Wild]] = keep
	return nil
}

// OwnershipOf 用一张静态表模拟 metadata.managedFields 的字段所有权。
func OwnershipOf(manager, kind string) []string {
	table := map[string]map[string][]string{
		"kube-controller-manager": {
			"/spec/replicas":                             {"Deployment", "ReplicaSet"},
			"/spec/template/spec/containers/0/resources": {"Deployment"},
		},
		"horizontal-pod-autoscaler": {"/spec/replicas": {"Deployment"}},
	}
	out := []string{}
	for ptr, kinds := range table[manager] {
		for _, k := range kinds {
			if k == kind {
				out = append(out, ptr)
			}
		}
	}
	sort.Strings(out)
	return out
}

// ShouldIgnoreStatus: all(默认) / crd / none。
func ShouldIgnoreStatus(mode, kind string) (bool, error) {
	switch mode {
	case "none":
		return false, nil
	case "all":
		return true, nil
	case "crd":
		return kind == "CustomResourceDefinition", nil
	}
	return false, fmt.Errorf("ignoreResourceStatusField 只能是 crd/all/none")
}

// IgnoreDifferences 返回去噪后的 (live, target) 副本;kind 留空时取 live 自己的 kind。
func IgnoreDifferences(live, target map[string]interface{}, rules []DiffRule,
	ignoreStatus, kind string) (map[string]interface{}, map[string]interface{}) {
	l, t := DeepCopyJSON(live), DeepCopyJSON(target)
	if kind == "" {
		kind, _ = l["kind"].(string)
	}
	group := groupOf(stringField(l, "apiVersion"))
	for _, r := range rules {
		if r.Group != "" && r.Group != "*" && r.Group != group {
			continue
		}
		if r.Kind != "" && r.Kind != "*" && r.Kind != kind {
			continue
		}
		if r.Name != "" && r.Name != metadataField(l, "name", "") {
			continue
		}
		if r.Namespace != "" && r.Namespace != metadataField(l, "namespace", "default") {
			continue
		}
		for _, ptr := range r.JSONPointers {
			DeletePointer(l, ptr)
			DeletePointer(t, ptr)
		}
		for _, expr := range r.JQPathExpressions {
			_ = ApplyJQPath(l, expr)
			_ = ApplyJQPath(t, expr)
		}
		for _, mgr := range r.ManagedFieldsManagers {
			for _, ptr := range OwnershipOf(mgr, kind) {
				DeletePointer(l, ptr)
				DeletePointer(t, ptr)
			}
		}
	}
	if ok, _ := ShouldIgnoreStatus(ignoreStatus, kind); ok {
		delete(l, "status")
		delete(t, "status")
	}
	return l, t
}

// groupOf: core 组的 group 是空串 ""(apiVersion 里没有 "/"), 不是 "v1"。
func groupOf(apiVersion string) string {
	if i := strings.Index(apiVersion, "/"); i >= 0 {
		return apiVersion[:i]
	}
	return ""
}

func stringField(obj map[string]interface{}, key string) string {
	s, _ := obj[key].(string)
	return s
}

func metadataField(obj map[string]interface{}, key, fallback string) string {
	meta, ok := obj["metadata"].(map[string]interface{})
	if !ok {
		return fallback
	}
	if s, ok := meta[key].(string); ok {
		return s
	}
	return fallback
}

// IsOutOfSync 比较去噪后的规范序列化结果。
func IsOutOfSync(live, target map[string]interface{}, rules []DiffRule,
	ignoreStatus, kind string) bool {
	a, b := IgnoreDifferences(live, target, rules, ignoreStatus, kind)
	return CanonicalJSON(a) != CanonicalJSON(b)
}
