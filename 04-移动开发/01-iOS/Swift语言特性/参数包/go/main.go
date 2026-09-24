package main

import "fmt"

func main() {
	fmt.Println("== 捕获集 ==")
	pat := repeatOf(nodeOf("foo", eachOf("x"), nodeOf("self", eachOf("T")), repeatOf(eachOf("y"))))
	fmt.Println("  repeat foo(each x, (each T).self, (repeat each y))")
	fmt.Println("    外层捕获:", sortedKeys(captures(pat)))
	fmt.Println("    内层捕获:", sortedKeys(captures(pat.Parts[0].Parts[2])))
	tp := nodeOf("Foo", eachOf("U"), repeatOf(eachOf("V")))
	fmt.Println("  值包类型 Foo<U, (repeat each V)> 顺带捕获:", sortedKeys(captures(tp)))

	fmt.Println("\n== 同形状推断 ==")
	s := newShapeSolver([]string{"T", "U", "V"})
	fmt.Println("  推断前 T 与 U 同形状?", s.sameShape("T", "U"))
	inferFromPackExpansion(s, []string{"T", "U"})
	fmt.Println("  返回类型处见过 (repeat (each T, each U)) 之后:",
		s.sameShape("T", "U"), "等价类 =", s.classOf("T"))
	if err := checkPackExpansion(s, []string{"T", "V"}, "local var"); err != nil {
		fmt.Println("  局部变量位置 ->", err)
	}
	fmt.Println("  给 Q 钉上长度 2 再钉 3 ->", s.imposeConcrete("T", 2), s.imposeConcrete("T", 3))

	fmt.Println("\n== 变长泛型类型的实参绑定 ==")
	spec := []Param{{false, "T"}, {true, "U"}, {false, "V"}}
	for _, args := range [][]string{{"Int", "Float"}, {"Int", "Bool", "Float"},
		{"Int", "Bool", "String", "Float"}, {"Int"}} {
		got, err := bindGenericArgs(spec, args)
		if err != nil {
			fmt.Printf("  S<%v> -> %v\n", args, err)
			continue
		}
		fmt.Printf("  S<%v> -> %v\n", args, got)
	}

	fmt.Println("\n== 要求推断(SE-0398) ==")
	r1, _ := inferRequirements("scalar", []Arg{{true, "U"}}, nil)
	fmt.Println("  repeat ImposeRequirement<each U> ->", r1)
	r2, _ := inferRequirements("expansion",
		[]Arg{{false, "Int"}, {false, "V"}, {true, "U"}}, nil)
	fmt.Println("  ImposeRepeatedRequirement<Int, V, repeat each U> ->", r2)
	_, err := inferRequirements("expansion", []Arg{{true, "U"}, {true, "V"}},
		map[string]int{"U": 1, "V": 2})
	fmt.Println("  repeat ImposeRepeatedSameType<each U, repeat each V> ->", err)

	fmt.Println("\n== 包遍历 vs 包扩展(惰性 vs 全量) ==")
	values := []string{"1", "hello", "true"}
	events := iterateOverPack(len(values), func(i int) string {
		return "evaluated " + values[i]
	}, 1, true)
	for _, e := range events {
		if e.IsEval {
			fmt.Println("    ", e.Text)
		}
	}
	fmt.Println("  repeat 全量展开:", expandAll(len(values), func(i int) string {
		return "evaluated " + values[i]
	}))

	fmt.Println("\n== 抽象元组的区别(SE-0399) ==")
	for _, k := range []string{"each value", "each tuple", "value + tuple", "value + each tuple"} {
		fmt.Printf("  %-20s -> %v\n", k,
			abstractTupleExample([]int{1, 2, 3}, []int{4, 5, 6})[k])
	}
}
