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
)

func isEmpty(v interface{}) bool {
	switch t := v.(type) {
	case nil:
		return true
	case string:
		return t == ""
	case int:
		return t == 0
	case []interface{}:
		return len(t) == 0
	}
	return false
}


var checks int

func ck(cond bool, msg string) {
	if !cond {
		panic("assert failed: " + msg)
	}
	checks++
}

func main() {
	// 1. 值优先级
	chart := Values{"image": Values{"repo": "nginx", "tag": "1.0"}, "replicas": 1, "extra": "keep"}
	parent := Values{"image": Values{"tag": "1.1"}}
	file := Values{"replicas": 3}
	setTag := Values{"image": Values{"tag": "2.0"}}
	v := BuildValues(chart, &parent, []Values{file}, []Values{setTag})
	img := v["image"].(Values)
	ck(img["repo"] == "nginx", "未覆盖的键保留默认")
	ck(img["tag"] == "2.0", fmt.Sprintf("--set 优先级最高, 实得 %v", img["tag"]))
	ck(v["replicas"] == 3, "-f 覆盖 chart 默认值")
	ck(v["extra"] == "keep", "无关键不受影响")

	// 2. null 删除键
	base := Values{"livenessProbe": Values{"httpGet": Values{"path": "/login", "port": "http"},
		"initialDelaySeconds": 120}}
	m := Coalesce(base, Values{"livenessProbe": Values{"httpGet": nil}})
	lp := m["livenessProbe"].(Values)
	_, has := lp["httpGet"]
	ck(!has, "置 null 应删除该键")
	ck(lp["initialDelaySeconds"] == 120, "同层其他键保留")
	m2 := Coalesce(base, Values{"livenessProbe": Values{"httpGet": Values{"path": "/health"}}})
	hg := m2["livenessProbe"].(Values)["httpGet"].(Values)
	ck(hg["path"] == "/health" && hg["port"] == "http", "非 null 覆盖应深合并")

	// 3. 模板函数与管道
	ctx := map[string]interface{}{
		"Values": map[string]interface{}{
			"favorite": map[string]interface{}{
				"drink": "coffee", "food": "pizza",
				"drinks": []interface{}{"coffee", "tea", "water"},
			},
			"missing": nil,
		},
		"Release": map[string]interface{}{"Name": "trendsetting-p"},
		"Chart":   map[string]interface{}{"Name": "mychart"},
	}
	ck(EvalAction(".Values.favorite.drink | quote", ctx) == "\"coffee\"", "quote")
	ck(EvalAction("quote .Values.favorite.drink", ctx) == "\"coffee\"", "函数式调用等价")
	ck(EvalAction(".Values.favorite.food | upper | quote", ctx) == "\"PIZZA\"", "管道链式")
	ck(EvalAction(".Values.favorite.drink | repeat 5 | quote", ctx) ==
		"\"coffeecoffeecoffeecoffeecoffee\"", "repeat:管道值作为最后参数")
	ck(EvalAction(".Values.favorite.drinks | join \", \" | quote", ctx) ==
		"\"coffee, tea, water\"", "join")
	ck(EvalAction(".Values.missing | default \"tea\" | quote", ctx) == "\"tea\"", "default 生效")
	ck(EvalAction(".Values.favorite.drink | default \"tea\" | quote", ctx) == "\"coffee\"",
		"default 非空不生效")
	ck(EvalAction(".Release.Name", ctx) == "trendsetting-p", "内置对象 Release")
	ck(EvalAction("\"literal\"", ctx) == "literal", "字符串字面量")

	// 4. 整段渲染
	out := Render("name: {{ .Release.Name }}-configmap\ndrink: {{ .Values.favorite.drink | quote }}\n", ctx)
	ck(strings.Contains(out, "name: trendsetting-p-configmap"), "整段渲染 Release.Name")
	ck(strings.Contains(out, "drink: \"coffee\""), "整段渲染并 quote")
	ck(!strings.Contains(out, "{{"), "不应残留 action")
	ck(Render("a{{/* c */}}b", ctx) == "ab", "注释 action 输出空")

	// 5. InstallOrder 与 lessByKind
	ck(InstallOrder[0] == "PriorityClass" && InstallOrder[len(InstallOrder)-1] == "APIService",
		"首尾 kind")
	ck(LessByKind("Namespace", "ConfigMap"), "Namespace 早于 ConfigMap")
	ck(LessByKind("Service", "Deployment"), "Service 早于 Deployment")
	ck(LessByKind("Deployment", "StatefulSet"), "Deployment 早于 StatefulSet")
	ck(!LessByKind("ConfigMap", "ConfigMap"), "相同 kind 保序")
	ck(LessByKind("Deployment", "Widget"), "已知 kind 在未知之前")
	ck(!LessByKind("Widget", "Deployment"), "未知 kind 排最后")
	ck(LessByKind("Alpha", "Zeta"), "双未知按字母序")
	ck(!LessByKind("Zeta", "Alpha"), "双未知字母序反向")

	// 6. 稳定排序
	ms := []Manifest{{"Deployment", "d1"}, {"Widget", "w1"}, {"ConfigMap", "c1"},
		{"Deployment", "d2"}, {"Namespace", "n1"}}
	ordered := SortManifestsByKind(ms)
	ck(ordered[0].Kind == "Namespace", "Namespace 第一")
	ck(ordered[len(ordered)-1].Kind == "Widget", "未知 kind 最后")
	names := []string{}
	for _, m := range ordered {
		if m.Kind == "Deployment" {
			names = append(names, m.Name)
		}
	}
	ck(strings.Join(names, ",") == "d1,d2", "同 kind 保序")
	ck(len(ordered) == len(ms), "排序不丢元素")

	fmt.Printf("helm_render(go): %d assertions passed\n", checks)
}
