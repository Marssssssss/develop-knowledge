// in-toto 演示：一条完整供应链布局，以及三种典型的「规则写错」后果。
package main

import "fmt"

func h(c byte) string {
	out := make([]byte, 64)
	for i := range out {
		out[i] = c
	}
	return string(out)
}

func buildLayout() *Layout {
	return &Layout{
		Steps: []Step{
			{Name: "fetch", Threshold: 1, Pubkeys: []string{"alice"},
				ExpectedProducts: []string{"CREATE src/*", "DISALLOW *"}},
			{Name: "build", Threshold: 1, Pubkeys: []string{"bob"},
				ExpectedMaterials: []string{"MATCH src/* WITH PRODUCTS FROM fetch", "DISALLOW *"},
				ExpectedProducts:  []string{"CREATE obj/*", "DISALLOW *"}},
			{Name: "package", Threshold: 2, Pubkeys: []string{"carol", "dave"},
				ExpectedMaterials: []string{"MATCH obj/* WITH PRODUCTS FROM build", "DISALLOW *"},
				ExpectedProducts:  []string{"CREATE dist/app.tar.gz", "DISALLOW *"}},
		},
		Inspections: []Inspection{
			{Name: "untar",
				ExpectedMaterials: []string{"MATCH dist/app.tar.gz WITH PRODUCTS FROM package",
					"DISALLOW *"}},
		},
	}
}

func goodLinks() map[string][]*Link {
	return map[string][]*Link{
		"fetch": {{Name: "fetch", Materials: map[string]string{},
			Products: map[string]string{"src/main.c": h('a')}, Signer: "alice"}},
		"build": {{Name: "build", Materials: map[string]string{"src/main.c": h('a')},
			Products: map[string]string{"obj/main.o": h('b')}, Signer: "bob"}},
		"package": {
			{Name: "package", Materials: map[string]string{"obj/main.o": h('b')},
				Products: map[string]string{"dist/app.tar.gz": h('c')}, Signer: "carol"},
			{Name: "package", Materials: map[string]string{"obj/main.o": h('b')},
				Products: map[string]string{"dist/app.tar.gz": h('c')}, Signer: "dave"},
		},
		"untar": {{Name: "untar",
			Materials: map[string]string{"dist/app.tar.gz": h('c')}, Signer: "alice"}},
	}
}

func demo() {
	fmt.Println("in-toto 布局验证：规则写对与写错的差别")

	fmt.Println()
	fmt.Println("1) 一条完整供应链（package 阈值 2，需要两人结果一致）")
	fmt.Println("  ", Describe(Verify(buildLayout(), goodLinks())))

	fmt.Println()
	fmt.Println("2) 中间产物被偷偷换掉（build 拿着 a，package 声称拿到 z）")
	links := goodLinks()
	links["package"] = []*Link{
		{Name: "package", Materials: map[string]string{"obj/main.o": h('z')},
			Products: map[string]string{"dist/app.tar.gz": h('c')}, Signer: "carol"},
		{Name: "package", Materials: map[string]string{"obj/main.o": h('z')},
			Products: map[string]string{"dist/app.tar.gz": h('c')}, Signer: "dave"},
	}
	fmt.Println("  ", Describe(Verify(buildLayout(), links)))

	fmt.Println()
	fmt.Println("3) 阈值 2 但两人报告不一致")
	links = goodLinks()
	links["package"] = []*Link{
		{Name: "package", Materials: map[string]string{"obj/main.o": h('b')},
			Products: map[string]string{"dist/app.tar.gz": h('c')}, Signer: "carol"},
		{Name: "package", Materials: map[string]string{"obj/main.o": h('b')},
			Products: map[string]string{"dist/app.tar.gz": h('d')}, Signer: "dave"},
	}
	fmt.Println("  ", Describe(Verify(buildLayout(), links)))

	fmt.Println()
	fmt.Println("4) 忘记在末尾写 DISALLOW * 的后果")
	leak := &Link{Name: "s", Materials: map[string]string{"src/main.c": h('a'),
		"src/backdoor.c": h('x')}, Products: map[string]string{}}
	fetch := map[string]Artifacts{"fetch": &Link{Name: "fetch",
		Products: map[string]string{"src/main.c": h('a')}}}
	for _, rules := range [][]string{
		{"MATCH src/main.c WITH PRODUCTS FROM fetch"},
		{"MATCH src/main.c WITH PRODUCTS FROM fetch", "DISALLOW *"},
	} {
		passed, _, left := VerifyExpected(rules, leak.Materials, "materials", leak, fetch)
		fmt.Printf("   规则 %-52v -> 通过=%v 剩余=%v\n", rules, passed, left)
	}

	fmt.Println()
	fmt.Println("5) 规则顺序：ALLOW * 写前面会把后面的 DISALLOW 架空")
	bad := &Link{Name: "s", Materials: map[string]string{"bad.c": h('x')},
		Products: map[string]string{}}
	for _, rules := range [][]string{
		{"ALLOW *", "DISALLOW *.c"},
		{"DISALLOW *.c", "ALLOW *"},
	} {
		passed, err, _ := VerifyExpected(rules, bad.Materials, "materials", bad, nil)
		msg := ""
		if err != nil {
			msg = err.Error()
		}
		fmt.Printf("   %-24v -> 通过=%v %s\n", rules, passed, msg)
	}

	fmt.Println()
	fmt.Println("6) link 文件名（规范 4.4：keyid 前六字节）")
	fmt.Println("  ", LinkFilename("package", "0123456789abcdef0123"))
}

func main() {
	demo()
}
