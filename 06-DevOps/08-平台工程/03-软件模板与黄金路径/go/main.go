package main

import "fmt"

func main() {
	ctx := Context{
		"parameters": map[string]any{"name": "artist-web", "enabledDB": false},
		"steps": map[string]any{
			"publish": map[string]any{"output": map[string]any{
				"remoteUrl":      "github.com?repo=artist-web",
				"repoContentsUrl": "github.com?repo=artist-web/blob/main",
			}},
		},
		"values": map[string]any{"name": "artist-web"},
	}

	fmt.Println("== 路径求值 ==")
	for _, p := range []string{"parameters.name", "steps['publish'].output.remoteUrl", "steps.publish.output.remoteUrl"} {
		v, err := Lookup(ctx, p)
		fmt.Printf("  %-42s -> %v %v\n", p, v, err)
	}
	if _, err := Lookup(ctx, "parameters.nope"); err != nil {
		fmt.Println("  未定义变量:", err)
	}

	fmt.Println("== 类型保持 ==")
	for _, s := range []string{"${{ parameters.enabledDB }}", "repo-${{ parameters.name }}"} {
		v, err := RenderTyped(s, ctx)
		fmt.Printf("  %-32s -> %#v %v\n", s, v, err)
	}

	fmt.Println("== 模板校验 ==")
	tpl := Template{
		APIVersion: "scaffolder.backstage.io/v1beta3",
		Name:       "v1beta3-demo",
		Spec: map[string]any{
			"type":       "service",
			"parameters": []any{},
			"steps":      []any{},
		},
	}
	fmt.Printf("  合法模板 -> %v\n", ValidateTemplate(tpl))
	bad := tpl
	bad.APIVersion = "backstage.io/v1"
	fmt.Printf("  错误 apiVersion -> %v\n", ValidateTemplate(bad))

	fmt.Println("== 步骤执行 ==")
	tpl.Spec["steps"] = []any{
		map[string]any{
			"id":     "publish",
			"action": "publish:github",
			"input":  map[string]any{"repoUrl": "github.com?repo=${{ parameters.name }}"},
		},
		map[string]any{
			"id":     "register",
			"action": "catalog:register",
			"input":  map[string]any{"repoContentsUrl": "${{ steps['publish'].output.repoContentsUrl }}"},
		},
	}
	actions := map[string]Action{
		"publish:github": {
			ID: "publish:github", Required: []string{"repoUrl"}, SupportsDryRun: true,
			Handler: func(i map[string]any) map[string]any {
				return map[string]any{"remoteUrl": i["repoUrl"], "repoContentsUrl": fmt.Sprint(i["repoUrl"]) + "/blob/main"}
			},
		},
		"catalog:register": {
			ID: "catalog:register", Required: []string{"repoContentsUrl"}, SupportsDryRun: true,
			Handler: func(i map[string]any) map[string]any {
				return map[string]any{"entityRef": "component:default/catalog-info"}
			},
		},
	}
	_, results, err := Execute(tpl, map[string]any{"name": "artist-web"}, actions, false)
	if err != nil {
		fmt.Println("  error:", err)
		return
	}
	for _, r := range results {
		fmt.Printf("  %-10s %-10s output=%v\n", r.ID, r.Status, r.Output)
	}

	fmt.Println("== 时长 ==")
	for _, d := range []string{"PT4H", "PT15M", "P1DT2H", "P"} {
		v, err := ParseDuration(d)
		fmt.Printf("  %-8s -> %v %v\n", d, v, err)
	}
}
