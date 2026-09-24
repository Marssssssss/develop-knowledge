package main

import "fmt"

func main() {
	system := newMacroSystem()
	if err := system.Add("stringify", MacroSpec{Type: "StringifyMacro", ModuleName: "MacroLib"}); err != nil {
		fmt.Println("add failed:", err)
	}
	if err := system.Add("stringify", MacroSpec{Type: "Other"}); err != nil {
		fmt.Println("  重复注册 ->", err)
	}
	system.Add("recur", MacroSpec{Type: "RecurMacro"})

	fmt.Println("== 属性名 -> (名字, 模块) ==")
	for _, t := range []string{"stringify", "MacroLib.stringify", "MacroLib::stringify",
		"Outer.Inner", "A.B.C", "Gen<Int>.Name"} {
		n, m, ok := attachedMacroReference(t)
		if !ok {
			fmt.Printf("  @%-22s -> nil\n", t)
			continue
		}
		fmt.Printf("  @%-22s -> (%s, %s)\n", t, n, m)
	}

	fmt.Println("\n== 查找:模块必须对得上 ==")
	for _, q := range [][2]string{{"stringify", ""}, {"stringify", "MacroLib"}, {"stringify", "OtherLib"}} {
		spec, ok := system.Lookup(q[0], q[1])
		if !ok {
			fmt.Printf("  lookup(%s, %q) -> nil\n", q[0], q[1])
			continue
		}
		fmt.Printf("  lookup(%s, %q) -> %s\n", q[0], q[1], spec.Type)
	}

	fmt.Println("\n== collapse:按角色决定分隔符 ==")
	for _, r := range []string{roleExpression, roleMemberAttribute, roleMember,
		roleAccessor, roleBody, rolePreamble} {
		fmt.Printf("  %-16s -> %q\n", r, collapse([]string{"a", "b"}, r, false, defaultIndent))
	}
	fmt.Printf("  %-16s -> %q\n", "accessor(已有)",
		collapse([]string{"a", "b"}, roleAccessor, true, defaultIndent))

	fmt.Println("\n== 独立宏展开的三态 ==")
	ctx := &Context{}
	app := &MacroApplication{System: system, Context: ctx}
	st, res := app.ExpandFreestanding("nope", "", func(string) (string, error) {
		return "x", nil
	})
	fmt.Printf("  未注册   -> %s %q\n", st, res)
	st, res = app.ExpandFreestanding("stringify", "MacroLib", func(string) (string, error) {
		return "expanded", nil
	})
	fmt.Printf("  正常     -> %s %q\n", st, res)
	st, _ = app.ExpandFreestanding("stringify", "MacroLib", func(string) (string, error) {
		return "", fmt.Errorf("macro threw")
	})
	fmt.Printf("  抛错     -> %s 诊断=%v\n", st, ctx.Diagnostics)

	ctx2 := &Context{}
	app2 := &MacroApplication{System: system, Context: ctx2}
	st, _ = app2.ExpandFreestanding("recur", "", func(string) (string, error) {
		inner, _ := app2.ExpandFreestanding("recur", "", func(string) (string, error) {
			return "inner", nil
		})
		return inner, nil
	})
	fmt.Printf("  自我递归 -> %s 诊断=%v\n", st, ctx2.Diagnostics)

	fmt.Println("\n== 唯一名 ==")
	base := &Context{LexicalContext: []string{"S"}}
	wrap := WrapperContext{Prepend: []string{"f"}, Wrapped: base}
	fmt.Println("  被包装层 lexicalContext =", base.LexicalContext)
	fmt.Println("  包装层   lexicalContext =", wrap.LexicalContext())
	fmt.Println("  包装层取唯一名   =", wrap.MakeUniqueName("value"))
	fmt.Println("  被包装层取唯一名 =", base.MakeUniqueName("value"))

	fmt.Println("\n== 角色 -> 协议名 ==")
	for _, r := range []string{roleAccessor, roleBody, roleCodeItem, roleConformance,
		roleDeclaration, roleExpression, roleExtension, roleMemberAttribute,
		roleMember, rolePeer, rolePreamble} {
		fmt.Printf("  %-16s -> %s\n", r, protocolName[r])
	}
}
