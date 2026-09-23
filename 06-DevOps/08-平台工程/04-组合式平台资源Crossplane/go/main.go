package main

import "fmt"

func main() {
	claim := &Names{Kind: "PostgreSQLInstance", Plural: "postgresqlinstances"}
	xrd := Xrd{
		Name:                           "xpostgresqlinstances.database.example.org",
		Group:                          "database.example.org",
		Names:                          Names{Kind: "XPostgreSQLInstance", Plural: "xpostgresqlinstances"},
		Versions:                       []Version{{Name: "v1alpha1", Served: true}, {Name: "v1beta1", Served: true, Referenceable: true}},
		Scope:                          DefaultScope,
		ClaimNames:                     claim,
		ConnectionSecretKeys:           []string{"endpoint", "username"},
		DefaultCompositionRef:          "postgres-aws",
		DefaultCompositeDeletePolicy:   DefaultDeletePolicy,
		DefaultCompositionUpdatePolicy: DefaultUpdatePolicy,
	}
	fmt.Println("== XRD 校验 ==")
	errs := xrd.Validate()
	if len(errs) == 0 {
		errs = []string{"OK"}
	}
	fmt.Println("  ", errs)
	fmt.Printf("   composite GVK=%v claim GVK=%v offersClaim=%v\n", xrd.CompositeGVK(), xrd.ClaimGVK(), xrd.OffersClaim())

	nsXrd := xrd
	nsXrd.Scope = "Namespaced"
	fmt.Println("   Namespaced 下带 claim ->", nsXrd.Validate())

	fmt.Println("== Composition 校验 ==")
	aws := Composition{
		Name:             "postgres-aws",
		CompositeTypeRef: [2]string{"database.example.org/v1beta1", "XPostgreSQLInstance"},
		Pipeline:         []string{"patch-and-transform", "auto-ready"},
		Mode:             "Pipeline",
		Labels:           map[string]string{"provider": "aws", "tier": "gold"},
	}
	gcp := aws
	gcp.Name = "postgres-gcp"
	gcp.Pipeline = []string{"patch-and-transform"}
	gcp.Labels = map[string]string{"provider": "gcp", "tier": "standard"}
	empty := aws
	empty.Pipeline = nil
	for _, c := range []Composition{aws, gcp, empty} {
		e := c.Validate()
		if len(e) == 0 {
			e = []string{"OK"}
		}
		fmt.Printf("   %-14s steps=%d -> %v\n", c.Name, len(c.Pipeline), e)
	}

	fmt.Println("== 组合选择 ==")
	comps := []Composition{aws, gcp}
	cases := []struct {
		label string
		c     Composite
	}{
		{"默认（XRD defaultCompositionRef）", Composite{Name: "db-1"}},
		{"selector provider=aws", Composite{Name: "db-2", CompositionSelector: map[string]string{"provider": "aws"}}},
		{"selector provider=gcp", Composite{Name: "db-3", CompositionSelector: map[string]string{"provider": "gcp"}}},
		{"selector tier=gold（唯一）", Composite{Name: "db-4", CompositionSelector: map[string]string{"tier": "gold"}}},
	}
	for _, cs := range cases {
		got, err := SelectComposition(xrd, cs.c, comps)
		fmt.Printf("   %-32s -> %s %v\n", cs.label, got.Name, err)
	}
	if _, err := SelectComposition(xrd, Composite{Name: "db-5", CompositionSelector: map[string]string{"provider": "azure"}}, comps); err != nil {
		fmt.Println("   命中 0 个:", err)
	}

	fmt.Println("== 连接密钥白名单 ==")
	produced := map[string]string{"endpoint": "db.example.org", "username": "app", "password": "s3cr3t"}
	fmt.Println("   全部发布:", FilterConnectionSecret(nil, produced))
	fmt.Println("   白名单  :", FilterConnectionSecret(xrd.ConnectionSecretKeys, produced))
}
