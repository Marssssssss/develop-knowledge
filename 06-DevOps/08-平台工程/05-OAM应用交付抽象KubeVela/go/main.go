package main

import "fmt"

func main() {
	web := Component{
		Name:       "web",
		Type:       "webservice",
		Properties: map[string]any{"image": "nginx:1.25", "port": 80},
		Traits:     []Trait{{Type: "scaler", Properties: map[string]any{"replicas": 2}}, {Type: "gateway"}},
		ReplicaKey: "p80",
	}

	fmt.Println("== 渲染与下发 ==")
	plain := Application{Components: []Component{web}}
	applied, revision := plain.Render()
	fmt.Printf("   无 workflow: 下发 %d 个资源, revision 含 workflow=%v\n",
		len(applied), revision["workflow"] != nil)
	ref := "deploy-flow"
	wf := Application{Components: []Component{web}, Workflow: &ref}
	applied2, revision2 := wf.Render()
	fmt.Printf("   有 workflow: 下发 %d 个资源, revision.workflow=%v\n",
		len(applied2), revision2["workflow"])

	fmt.Println("== traits 顺序与 ReplicaKey ==")
	manifest := web.ToManifest()
	for _, t := range manifest["traits"].([]map[string]any) {
		fmt.Printf("   trait: %s\n", t["type"])
	}
	_, hasReplica := manifest["replicaKey"]
	fmt.Println("   replicaKey 出现在清单里:", hasReplica)

	fmt.Println("== 校验 ==")
	bad := Application{Components: []Component{{Name: "a", Type: "webservice", DependsOn: []string{"zzz"}}}}
	fmt.Println("   dependsOn 未知组件 ->", bad.Validate())
	fmt.Println("   空 components    ->", Application{}.Validate())

	fmt.Println("== 定义引用与修订 ==")
	if v, err := (DefinitionReference{Name: "webservice"}).ResolveVersion([]string{"v1", "v2"}); err == nil {
		fmt.Println("   未指定 version ->", v)
	}
	if _, err := (DefinitionReference{Name: "webservice", Version: "v9"}).ResolveVersion([]string{"v1"}); err != nil {
		fmt.Println("   指定不存在 version ->", err)
	}
	specV1 := map[string]string{"template": "output: {}"}
	h1 := RevisionHash(specV1)
	rev := DefinitionRevision{Name: "webservice-v1", Revision: 1, DefinitionType: "Component",
		SnapshotField: "componentDefinition", Hash: h1}
	fmt.Printf("   v1: revision=%d hash=%s 校验=%v\n", rev.Revision, rev.Hash, rev.Validate())
	specV2 := map[string]string{"template": "output: { replicas: 2 }"}
	h2 := RevisionHash(specV2)
	n, _ := NextRevision(&rev, h2)
	fmt.Println("   spec 变化后的 revision:", n)
	fmt.Println("   spec 未变:", func() int { v, _ := NextRevision(&rev, h1); return v }())
	fmt.Println("   Trait 快照字段填错 ->",
		DefinitionRevision{Name: "scaler-v1", Revision: 1, DefinitionType: "Trait",
			SnapshotField: "componentDefinition"}.Validate())

	fmt.Println("== 源状态可见性 ==")
	consumed := map[string]any{"registry": "harbor.example.org", "token": "abc",
		"nested": map[string]any{"token": "deep", "user": "u"}}
	fmt.Println("   未设置策略:", ExposeSourceValues(nil, consumed))
	fmt.Println("   打码路径  :", ExposeSourceValues(
		map[string]any{"maskPaths": []string{"token", "nested.token"}}, consumed))
}
