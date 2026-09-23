package main

import "fmt"

func main() {
	fmt.Println("== 字符集规则的差异 ==")
	cases := []struct {
		v    string
		name bool
		ns   bool
		tag  bool
	}{
		{"under_score", true, false, false},
		{"dot.sep", true, false, false},
		{"Java", true, true, false},
		{"c++", false, false, true},
		{"trail-", false, false, false},
		{"ok-1", true, true, true},
	}
	for _, c := range cases {
		fmt.Printf("  %-14s name=%-5v namespace=%-5v tag=%v\n",
			c.v, ValidName(c.v) == c.name, ValidNamespace(c.v) == c.ns, ValidTag(c.v) == c.tag)
	}

	fmt.Println("== 键校验 ==")
	for _, k := range []string{"example.com/custom", "NotDomain/custom", "backstage.io/x", "bad prefix/x"} {
		fmt.Printf("  %-22s valid=%v reserved=%v\n", k, ValidKey(k), len(k) > 13 && k[:13] == "backstage.io/")
	}

	fmt.Println("== 实体引用 ==")
	for _, pair := range [][3]string{
		{"artist-relations-team", "Group", "default"},
		{"group:default/dev.infra", "Group", "default"},
		{"resource:default/artists-db", "Component", "default"},
		{"ghe/alice", "User", "ghe"},
	} {
		ref, err := ParseEntityRef(pair[0], pair[1], pair[2])
		if err != nil {
			fmt.Println("  error:", err)
			continue
		}
		fmt.Printf("  %-30s -> %s (kind=%s ns=%s)\n", pair[0], ref, ref.Kind, ref.Namespace)
	}

	fmt.Println("== 关系推导 ==")
	comp := Entity{
		Kind:      "Component",
		Name:      "artist-web",
		Namespace: "default",
		Spec: map[string][]string{
			"owner":        {"artist-relations-team"},
			"system":       {"public-websites"},
			"providesApis": {"artist-api"},
			"dependsOn":    {"resource:default/artists-db"},
		},
	}
	rels, err := DeduceRelations(comp)
	if err != nil {
		fmt.Println("  error:", err)
		return
	}
	for _, r := range rels {
		fmt.Printf("  %s --%s--> %s\n", r.Source, r.Type, r.Target)
	}

	if _, err := DeduceRelations(Entity{
		Kind: "Component", Name: "bad", Namespace: "default",
		Spec: map[string][]string{"dependsOn": {"api:default/x"}},
	}); err != nil {
		fmt.Println("  反向用例（dependsOn 指向 API）:", err)
	}
}
