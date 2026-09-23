package main

import "fmt"

func main() {
	entity := map[string]any{
		"kind":    "Component",
		"owners":  []any{"group:default/platform", "user:default/alice"},
		"annotations": map[string]any{
			"backstage.io/managed-by-location": "file:/tmp/catalog-info.yaml",
		},
	}

	del, err := NewPermission("catalog.entity.delete", "delete", "catalog-entity")
	if err != nil {
		fmt.Println("bad permission:", err)
		return
	}
	create, _ := NewPermission("catalog.entity.create", "create", "")

	fmt.Println("== 决策形状校验 ==")
	samples := []map[string]any{
		{"result": Allow},
		{"result": Conditional, "resourceType": "catalog-entity", "conditions": map[string]any{"rule": "IS_ENTITY_OWNER"}},
		{"result": Conditional, "pluginId": "catalog", "resourceType": "catalog-entity", "conditions": map[string]any{"anyOf": []any{}}},
		{"result": Allow, "conditions": map[string]any{"rule": "X"}},
	}
	for _, s := range samples {
		errs := ValidateDecision(s)
		if len(errs) == 0 {
			errs = []string{"OK"}
		}
		fmt.Printf("  result=%-11v -> %v\n", s["result"], errs)
	}

	fmt.Println("== 条件求值 ==")
	criteria := []map[string]any{
		{"rule": "IS_ENTITY_OWNER", "params": map[string]any{"claims": []any{"user:default/alice"}}},
		{"rule": "IS_ENTITY_OWNER", "params": map[string]any{"claims": []any{"user:default/bob"}}},
		{"not": map[string]any{"rule": "IS_ENTITY_KIND", "params": map[string]any{"kinds": []any{"API"}}}},
		{"allOf": []any{
			map[string]any{"rule": "IS_ENTITY_KIND", "params": map[string]any{"kinds": []any{"Component"}}},
			map[string]any{"rule": "HAS_ANNOTATION", "params": map[string]any{"key": "backstage.io/managed-by-location"}},
		}},
	}
	for _, c := range criteria {
		v, err := EvalCriteria(c, entity)
		fmt.Printf("  %-60v -> %v %v\n", c, v, err)
	}

	fmt.Println("== 授权 ==")
	policy := OwnerOnlyPolicy("catalog-entity", "catalog")
	for _, claims := range [][]string{{"user:default/alice"}, {"user:default/bob"}, {}} {
		res, err := Authorize(del, claims, entity, policy)
		fmt.Printf("  claims=%-24v -> %s %v\n", claims, res, err)
	}
	res, err := Authorize(create, nil, entity, policy)
	fmt.Printf("  基础权限（无 resourceType）                 -> %s %v\n", res, err)
}
