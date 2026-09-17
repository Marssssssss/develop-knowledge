// diff 侧断言: 忽略规则匹配 / JSON Pointer / jq 子集 / managedFields / Quantity 规整。
// 与 python/argocd_check_diff.py 对应;harness 与 main() 在 main.go。
package main

import (
	"fmt"
	"strings"
)

func asF(v interface{}) (float64, bool) {
	f, ok := v.(float64)
	return f, ok
}

// dig 按层下钻对象, 任一环不是对象就返回 nil(断言里替代难读的多层类型断言链)。
func dig(m map[string]interface{}, path ...string) interface{} {
	var cur interface{} = m
	for _, p := range path {
		mm, ok := cur.(map[string]interface{})
		if !ok {
			return nil
		}
		cur = mm[p]
	}
	return cur
}

func deploymentFixture() (map[string]interface{}, map[string]interface{}) {
	live := map[string]interface{}{
		"apiVersion": "apps/v1",
		"kind":       "Deployment",
		"metadata":   map[string]interface{}{"name": "guestbook", "namespace": "default"},
		"spec": map[string]interface{}{
			"replicas": 3,
			"template": map[string]interface{}{"spec": map[string]interface{}{
				"containers": []interface{}{
					map[string]interface{}{"name": "app", "image": "g:1"}},
			}},
		},
		"status": map[string]interface{}{"readyReplicas": 3},
	}
	target := map[string]interface{}{
		"apiVersion": "apps/v1",
		"kind":       "Deployment",
		"metadata":   map[string]interface{}{"name": "guestbook", "namespace": "default"},
		"spec": map[string]interface{}{
			"replicas": 2,
			"template": map[string]interface{}{"spec": map[string]interface{}{
				"containers": []interface{}{
					map[string]interface{}{"name": "app", "image": "g:1"}},
			}},
		},
	}
	return live, target
}

func testDiff() {
	fmt.Println("[5] diff 定制与去噪")
	live, target := deploymentFixture()
	liveCopy, _ := deploymentFixture()

	check("未忽略时 OutOfSync", IsOutOfSync(live, target, nil, "all", ""), "")
	ignoreReplicas := []DiffRule{{Group: "apps", Kind: "Deployment",
		JSONPointers: []string{"/spec/replicas"}}}
	check("忽略 /spec/replicas 后 Synced",
		!IsOutOfSync(live, target, ignoreReplicas, "all", ""), "")
	check("status 字段默认被忽略(ignoreResourceStatusField 默认 all)",
		!IsOutOfSync(live, target, ignoreReplicas, "all", ""), "")
	check("ignoreResourceStatusField: none 时 status 也算差异",
		IsOutOfSync(live, target, ignoreReplicas, "none", ""), "")
	check("忽略规则只对匹配的 kind 生效",
		IsOutOfSync(live, target, []DiffRule{{Group: "apps", Kind: "StatefulSet",
			JSONPointers: []string{"/spec/replicas"}}}, "all", ""), "")
	check("忽略规则可按 name 收窄: 名字不符则不生效",
		IsOutOfSync(live, target, []DiffRule{{Group: "apps", Kind: "Deployment",
			Name: "other", JSONPointers: []string{"/spec/replicas"}}}, "all", ""), "")
	check("name + namespace 命中则生效",
		!IsOutOfSync(live, target, []DiffRule{{Group: "apps", Kind: "Deployment",
			Name: "guestbook", Namespace: "default",
			JSONPointers: []string{"/spec/replicas"}}}, "all", ""), "")
	check("group 留空即通配, 对所有资源生效",
		!IsOutOfSync(live, target, []DiffRule{{JSONPointers: []string{"/spec/replicas"}}},
			"all", ""), "")

	coreLive := map[string]interface{}{
		"apiVersion": "v1", "kind": "ConfigMap",
		"metadata":   map[string]interface{}{"name": "c"},
		"metadata2":  1,
		"data":       map[string]interface{}{"a": "1"},
	}
	coreTarget := DeepCopyJSON(coreLive)
	delete(coreTarget, "metadata2")
	check("core 组的 group 是空串而非 v1",
		!IsOutOfSync(coreLive, coreTarget, []DiffRule{{Group: "", Kind: "ConfigMap",
			JSONPointers: []string{"/metadata2"}}}, "all", ""), "")
	check("group=v1 匹配不到 core 资源",
		IsOutOfSync(coreLive, coreTarget, []DiffRule{{Group: "v1", Kind: "ConfigMap",
			JSONPointers: []string{"/metadata2"}}}, "all", ""), "")

	// managedFields 所有权
	mfLive := map[string]interface{}{
		"apiVersion": "apps/v1", "kind": "Deployment",
		"metadata": map[string]interface{}{"name": "g"},
		"spec":     map[string]interface{}{"replicas": 5},
	}
	mfTarget := map[string]interface{}{
		"apiVersion": "apps/v1", "kind": "Deployment",
		"metadata": map[string]interface{}{"name": "g"},
		"spec":     map[string]interface{}{"replicas": 2},
	}
	check("未忽略 replicas 时 OutOfSync", IsOutOfSync(mfLive, mfTarget, nil, "all", ""), "")
	check("按 managedFieldsManagers 忽略后 Synced",
		!IsOutOfSync(mfLive, mfTarget, []DiffRule{
			{ManagedFieldsManagers: []string{"kube-controller-manager"}}}, "all", ""), "")
	check("kube-controller-manager 拥有 Deployment 的 replicas 与容器 resources",
		eqS(OwnershipOf("kube-controller-manager", "Deployment"),
			[]string{"/spec/replicas", "/spec/template/spec/containers/0/resources"}), "")
	check("未登记的 manager 无所有权", len(OwnershipOf("who", "Deployment")) == 0, "")
	check("HPA 也拥有 /spec/replicas",
		eqS(OwnershipOf("horizontal-pod-autoscaler", "Deployment"), []string{"/spec/replicas"}), "")

	// JSON Pointer 转义
	check("~1 反转义为 /",
		UnescapePointerToken("node-role.kubernetes.io~1worker") == "node-role.kubernetes.io/worker", "")
	check("~0 反转义为 ~", UnescapePointerToken("a~0b") == "a~b", "")
	na, nb := IgnoreDifferences(live, liveCopy, []DiffRule{
		{JSONPointers: []string{"/spec/nope"}}}, "none", "")
	check("不存在的 pointer 静默返回(两副本仍相同)",
		CanonicalJSON(na) == CanonicalJSON(nb), "")

	// jq 子集
	jqLive := map[string]interface{}{
		"apiVersion": "apps/v1", "kind": "Deployment",
		"metadata": map[string]interface{}{"name": "g"},
		"spec": map[string]interface{}{"template": map[string]interface{}{"spec": map[string]interface{}{
			"initContainers": []interface{}{
				map[string]interface{}{"name": "injected-init-container", "image": "x"},
				map[string]interface{}{"name": "real-init", "image": "y"}},
		}}},
	}
	jqTarget := map[string]interface{}{
		"apiVersion": "apps/v1", "kind": "Deployment",
		"metadata": map[string]interface{}{"name": "g"},
		"spec": map[string]interface{}{"template": map[string]interface{}{"spec": map[string]interface{}{
			"initContainers": []interface{}{
				map[string]interface{}{"name": "real-init", "image": "y"}},
		}}},
	}
	check("未忽略时 OutOfSync", IsOutOfSync(jqLive, jqTarget, nil, "all", ""), "")
	jqRules := []DiffRule{{Group: "apps", Kind: "Deployment", JQPathExpressions: []string{
		`.spec.template.spec.initContainers[] | select(.name == "injected-init-container")`}}}
	check("jq select 过滤注入的 initContainer 后 Synced",
		!IsOutOfSync(jqLive, jqTarget, jqRules, "all", ""), "")

	p1, err1 := ParseJQPath(".a[].b")
	check("jq 子集解析出通配位置",
		err1 == nil && p1.Wild == 0 && eqS(p1.Parts, []string{"a", "b"}), "")
	p2, err2 := ParseJQPath(".a[]?.b.c")
	check("通配可以在中间层", err2 == nil && p2.Wild == 0 && eqS(p2.Parts, []string{"a", "b", "c"}), "")
	p3, err3 := ParseJQPath(".a.b")
	check("无通配时 Wild = -1", err3 == nil && p3.Wild == -1, "")
	p4, err4 := ParseJQPath(`.a[] | select(.k == "v")`)
	check("jq 子集解析出 select",
		err4 == nil && p4.HasSelect && p4.SelectKey == "k" && p4.SelectVal == "v", "")
	_, err5 := ParseJQPath(`.a[] | select(.k > 1)`)
	check("不支持的 select 报错", err5 != nil, "")
	_, err6 := ParseJQPath("a.b")
	check("非 . 开头报错", err6 != nil, "")

	doc := map[string]interface{}{"webhooks": []interface{}{
		map[string]interface{}{"clientConfig": map[string]interface{}{"caBundle": "X"}},
		map[string]interface{}{"clientConfig": map[string]interface{}{"caBundle": "Y"}},
	}}
	_ = ApplyJQPath(doc, ".webhooks[]?.clientConfig.caBundle")
	hooks, _ := doc["webhooks"].([]interface{})
	ok := len(hooks) == 2
	for _, h := range hooks {
		hm, _ := h.(map[string]interface{})
		cfg, _ := hm["clientConfig"].(map[string]interface{})
		if _, exists := cfg["caBundle"]; exists {
			ok = false
		}
	}
	check("链式 jq 删除嵌套字段", ok, "")

	doc2 := map[string]interface{}{"webhooks": []interface{}{
		map[string]interface{}{"clientConfig": map[string]interface{}{"caBundle": "X"}},
	}}
	_ = ApplyJQPath(doc2, ".webhooks.clientConfig.caBundle")
	hooks2, _ := doc2["webhooks"].([]interface{})
	hm2, _ := hooks2[0].(map[string]interface{})
	cfg2, _ := hm2["clientConfig"].(map[string]interface{})
	check("省略 [] 时数组不展开, 路径落空", cfg2["caBundle"] == "X", "")

	// 已知类型规整
	rollout := map[string]interface{}{
		"spec": map[string]interface{}{"template": map[string]interface{}{"spec": map[string]interface{}{
			"containers": []interface{}{map[string]interface{}{
				"name": "a", "resources": map[string]interface{}{
					"requests": map[string]interface{}{"cpu": "100m"}}}},
		}}},
	}
	if err := CanonicalizeKnownTypes(rollout, "argoproj.io/Rollout", "spec.template.spec"); err != nil {
		check("规整调用不应报错", false, err.Error())
	}
	containers, _ := dig(rollout, "spec", "template", "spec", "containers").([]interface{})
	var cpu interface{}
	if len(containers) == 1 {
		if c0, ok := containers[0].(map[string]interface{}); ok {
			if req, ok := dig(c0, "resources", "requests").(map[string]interface{}); ok {
				cpu = req["cpu"]
			}
		}
	}
	cf, cok := asF(cpu)
	check("100m 被规整为 0.1", cok && feq(cf, 0.1), fmt.Sprint(cpu))

	q1, ok1 := asF(CanonicalizeQuantity("100m"))
	q2, ok2 := asF(CanonicalizeQuantity("0.1"))
	check("100m 与 0.1 规整后相等", ok1 && ok2 && feq(q1, q2), "")
	q3, ok3 := asF(CanonicalizeQuantity("250m"))
	check("250m -> 0.25", ok3 && feq(q3, 0.25), "")
	q4, ok4 := asF(CanonicalizeQuantity("1"))
	check("1 保持 1.0", ok4 && feq(q4, 1.0), "")
	check("带后缀的 1Gi 原样返回", CanonicalizeQuantity("1Gi") == "1Gi", "")

	check("未登记的类型报错",
		CanonicalizeKnownTypes(rollout, "argoproj.io/Other", "spec.x") != nil, "")
	check("非法 ignoreResourceStatusField 报错", func() bool {
		_, err := ShouldIgnoreStatus("sometimes", "Deployment")
		return err != nil
	}(), "")

	d1, derr := ContentDigest([]byte("x"), "sha256")
	d2, _ := ContentDigest([]byte("y"), "sha256")
	d3, _ := ContentDigest([]byte("x"), "sha512")
	check("content_digest 形状正确",
		derr == nil && strings.HasPrefix(d1, "sha256:") && len(d1) == 71, d1)
	check("content_digest 内容敏感", d1 != d2, "")
	check("sha512 摘要更长", len(d3) == 135, "")
	_, derr2 := ContentDigest([]byte("x"), "md5")
	check("未支持的算法报错", derr2 != nil, "")
}
