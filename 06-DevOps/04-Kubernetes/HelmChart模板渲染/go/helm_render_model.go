// Helm Chart 模板渲染三件套 (Go):值合并 → 模板求值 → 按 Kind 排序。
//
// 权威来源(实际读过):
//   1. https://helm.sh/docs/chart_template_guide/values_files/
//   2. https://helm.sh/docs/chart_template_guide/functions_and_pipelines/
//   3. https://cdn.jsdelivr.net/gh/helm/helm@v3.19.0/pkg/releaseutil/kind_sorter.go
//
// 关键点:值优先级 values.yaml < 父 chart < -f < --set;置 null 删除键;
// 管道值作为函数的最后一个参数;InstallOrder 排序中未知 kind 永远最后、同 kind 保序。
package main

import (
	"fmt"
	"strings"
	"strconv"
)

// Helm Chart 模板渲染三件套 (Go):值合并 → 模板求值 → 按 Kind 排序。
//
// 权威来源(实际读过):
//   1. https://helm.sh/docs/chart_template_guide/values_files/
//   2. https://helm.sh/docs/chart_template_guide/functions_and_pipelines/
//   3. https://cdn.jsdelivr.net/gh/helm/helm@v3.19.0/pkg/releaseutil/kind_sorter.go
//
// 关键点:值优先级 values.yaml < 父 chart < -f < --set;置 null 删除键;
// 管道值作为函数的最后一个参数;InstallOrder 排序中未知 kind 永远最后、同 kind 保序。
package main

import (
	"fmt"
	"strconv"
	"strings"
)

// InstallOrder 完整照抄 helm v3.19.0 pkg/releaseutil/kind_sorter.go
var InstallOrder = []string{
	"PriorityClass", "Namespace", "NetworkPolicy", "ResourceQuota", "LimitRange",
	"PodSecurityPolicy", "PodDisruptionBudget", "ServiceAccount", "Secret",
	"SecretList", "ConfigMap", "StorageClass", "PersistentVolume",
	"PersistentVolumeClaim", "CustomResourceDefinition", "ClusterRole",
	"ClusterRoleList", "ClusterRoleBinding", "ClusterRoleBindingList", "Role",
	"RoleList", "RoleBinding", "RoleBindingList", "Service", "DaemonSet", "Pod",
	"ReplicationController", "ReplicaSet", "Deployment", "HorizontalPodAutoscaler",
	"StatefulSet", "Job", "CronJob", "IngressClass", "Ingress", "APIService",
}

func orderIndex() map[string]int {
	m := make(map[string]int, len(InstallOrder))
	for i, k := range InstallOrder {
		m[k] = i
	}
	return m
}

func LessByKind(a, b string) bool {
	idx := orderIndex()
	aok, bok := idx[a], idx[b]
	_, ha := idx[a]
	_, hb := idx[b]
	if !ha && !hb {
		if a != b {
			return a < b
		}
		return false // 相同 kind 保持原顺序
	}
	if !ha {
		return false // 未知 kind 排最后
	}
	if !hb {
		return true
	}
	return aok < bok
}

type Manifest struct {
	Kind, Name string
}

func SortManifestsByKind(ms []Manifest) []Manifest {
	out := append([]Manifest{}, ms...)
	for i := 1; i < len(out); i++ { // 插入排序 = 稳定
		cur := out[i]
		j := i - 1
		for j >= 0 && LessByKind(cur.Kind, out[j].Kind) {
			out[j+1] = out[j]
			j--
		}
		out[j+1] = cur
	}
	return out
}

// Values 用 map[string]interface{} 表达;nil 表示"删除该键"
type Values map[string]interface{}

// Ctx 是模板求值上下文的别名(避免 map[string]interface{} 反复出现在签名里)
type Ctx map[string]interface{}

func Coalesce(base, override Values) Values {
	out := Values{}
	for k, v := range base {
		out[k] = v
	}
	for k, v := range override {
		if v == nil {
			delete(out, k) // 置 null = 删除默认键
			continue
		}
		if sub, ok := v.(Values); ok {
			if old, ok2 := out[k].(Values); ok2 {
				out[k] = Coalesce(old, sub)
				continue
			}
		}
		out[k] = v
	}
	return out
}

func BuildValues(chart Values, parent *Values, files []Values, sets []Values) Values {
	v := Coalesce(chart, Values{})
	if parent != nil {
		v = Coalesce(v, *parent)
	}
	for _, f := range files {
		v = Coalesce(v, f)
	}
	for _, s := range sets {
		v = Coalesce(v, s)
	}
	return v
}

// ---------------------------------------------------------------- 模板
func quote(v interface{}) string { return fmt.Sprintf("%q", fmt.Sprint(v)) }
func upper(v interface{}) string { return strings.ToUpper(fmt.Sprint(v)) }
func lower(v interface{}) string { return strings.ToLower(fmt.Sprint(v)) }

func def(d, v interface{}) interface{} {
	if isEmpty(v) {
		return d
	}
	return v
}
func repeat(n int, v interface{}) string { return strings.Repeat(fmt.Sprint(v), n) }
func join(sep string, v interface{}) string {
	parts := []string{}
	if arr, ok := v.([]interface{}); ok {
		for _, x := range arr {
			parts = append(parts, fmt.Sprint(x))
		}
	}
	return strings.Join(parts, sep)
}

func lookup(path string, ctx Ctx) interface{} {
	cur := interface{}(map[string]interface{}(ctx))
	for _, p := range strings.Split(strings.TrimPrefix(path, "."), ".") {
		if p == "" {
			continue
		}
		m, ok := cur.(map[string]interface{})
		if !ok {
			return nil
		}
		cur, ok = m[p]
		if !ok {
			return nil
		}
	}
	return cur
}

func tokenize(s string) []string {
	toks := []string{}
	cur := ""
	var quote byte
	for i := 0; i < len(s); i++ {
		c := s[i]
		if quote != 0 {
			cur += string(c)
			if c == quote {
				quote = 0
			}
		} else if c == '"' || c == '\'' {
			quote = c
			cur += string(c)
		} else if c == ' ' || c == '\t' {
			if cur != "" {
				toks = append(toks, cur)
				cur = ""
			}
		} else {
			cur += string(c)
		}
	}
	if cur != "" {
		toks = append(toks, cur)
	}
	return toks
}

func parseArg(tok string) interface{} {
	if len(tok) >= 2 && ((tok[0] == '"' && tok[len(tok)-1] == '"') || (tok[0] == '\'' && tok[len(tok)-1] == '\'')) {
		return tok[1 : len(tok)-1]
	}
	if n, err := strconv.Atoi(tok); err == nil {
		return n
	}
	return tok
}

func resolve(tok string, ctx Ctx) interface{} {
	if len(tok) >= 2 && ((tok[0] == '"' && tok[len(tok)-1] == '"') || (tok[0] == '\'' && tok[len(tok)-1] == '\'')) {
		return parseArg(tok)
	}
	if _, err := strconv.Atoi(tok); err == nil {
		return parseArg(tok)
	}
	return lookup(tok, ctx)
}

// EvalAction 求值一个 action:支持 `a | f x | g`、`f .a "x"` 与纯路径。
func EvalAction(body string, ctx Ctx) string {
	body = strings.TrimSpace(body)
	if strings.HasPrefix(body, "/*") {
		return ""
	}
	segs := strings.Split(body, "|")
	head := strings.TrimSpace(segs[0])
	headToks := tokenize(head)
	var val interface{}
	switch headToks[0] {
	case "quote":
		val = quote(resolve(headToks[1], ctx))
	case "upper":
		val = upper(resolve(headToks[1], ctx))
	case "lower":
		val = lower(resolve(headToks[1], ctx))
	case "repeat":
		val = repeat(resolve(headToks[1], ctx).(int), resolve(headToks[2], ctx))
	case "default":
		val = def(resolve(headToks[1], ctx), resolve(headToks[2], ctx))
	case "join":
		val = join(fmt.Sprint(resolve(headToks[1], ctx)), resolve(headToks[2], ctx))
	default:
		val = resolve(head, ctx)
	}
	for _, seg := range segs[1:] {
		toks := tokenize(strings.TrimSpace(seg))
		switch toks[0] {
		case "quote":
			val = quote(val)
		case "upper":
			val = upper(val)
		case "lower":
			val = lower(val)
		case "repeat":
			val = repeat(parseArg(toks[1]).(int), val)
		case "default":
			val = def(parseArg(toks[1]), val)
		case "join":
			val = join(fmt.Sprint(parseArg(toks[1])), val)
		}
	}
	if val == nil {
		return ""
	}
	return fmt.Sprint(val)
}

func Render(tmpl string, ctx Ctx) string {
	out := ""
	for {
		s := strings.Index(tmpl, "{{")
		if s < 0 {
			return out + tmpl
		}
		out += tmpl[:s]
		e := strings.Index(tmpl[s:], "}}")
		if e < 0 {
			panic("unclosed action")
		}
		out += EvalAction(tmpl[s+2:s+e], ctx)
		tmpl = tmpl[s+e+2:]
	}
}
