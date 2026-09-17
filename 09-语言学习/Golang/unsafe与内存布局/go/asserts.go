package main

// 分组：A 规范的尺寸/对齐保证 · B Sizeof 的语义 · C 文档的 pointer bytes 三例 ·
//      D 字段排序与 size class 浪费 · E false sharing · F unsafe 六种模式 ·
//      G Offsetof 与累计偏移

func groupA() {
	for _, c := range []struct {
		tag string
		n   int
	}{
		{"int8", 1}, {"uint8", 1}, {"int16", 2}, {"uint16", 2},
		{"int32", 4}, {"uint32", 4}, {"float32", 4},
		{"int64", 8}, {"uint64", 8}, {"float64", 8},
		{"complex64", 8}, {"complex128", 16},
	} {
		eq(Sizeof(&Typ{Tag: c.tag}), c.n, "A 规范尺寸 "+c.tag)
	}
	for _, tag := range []string{"int8", "uint8", "uint16", "int32", "int64", "float64"} {
		check(Alignof(&Typ{Tag: tag}) >= 1, "A 对齐至少 1: "+tag)
	}
	s1 := Struct("S1", Fld("a", "int8"), Fld("b", "int64"))
	eq(Alignof(s1), 8, "A 结构体对齐取字段最大值")
	empty := Struct("Empty")
	eq(Alignof(empty), 1, "A 空结构体对齐为 1")
	eq(Sizeof(empty), 0, "A 空结构体尺寸为 0")
	eq(Alignof(Arr("int8", 37)), 1, "A 数组对齐 = 元素对齐（1）")
	eq(Alignof(Arr("float64", 3)), 8, "A 数组对齐 = 元素对齐（8）")
	eq(Sizeof(Arr("complex128", 0)), 0, "A 空数组尺寸为 0")
	z := Struct("Z", FT("x", Arr("int8", 0)))
	eq(Sizeof(z), 0, "A 只含零尺寸字段的结构体尺寸为 0")
	eq(Alignof(z), 1, "A 且对齐为 1（两个零尺寸变量可同址）")
}

func groupB() {
	eq(Sizeof(&Typ{Tag: "slice", Elem: &Typ{Tag: "int64"}}), 24, "B1 切片 = 24（描述符）")
	eq(Sizeof(&Typ{Tag: "string"}), 16, "B2 字符串 = 16")
	eq(Sizeof(&Typ{Tag: "iface"}), 16, "B3 接口 = 16")
	for _, tag := range []string{"map", "chan", "func", "ptr", "unsafeptr"} {
		eq(Sizeof(&Typ{Tag: tag}), 8, "B4 "+tag+" = 8（一个机器字）")
	}
	for _, tag := range []string{"int", "uint", "uintptr"} {
		eq(Sizeof(&Typ{Tag: tag}), 8, "B5 "+tag+" = 8（64 位平台）")
	}
	eq(Ptrdata(&Typ{Tag: "slice", Elem: &Typ{Tag: "int64"}}), 8, "B6 切片只扫 8 字节前缀")
	eq(Ptrdata(&Typ{Tag: "iface"}), 16, "B7 接口要扫全部 16 字节")
	eq(Ptrdata(&Typ{Tag: "string"}), 8, "B8 字符串只扫指针那 8 字节")
}

func groupC() {
	// fieldalignment 文档给出的三个例子
	c1 := Struct("C1", Fld("x", "uint32"), Fld("s", "string"))
	eq(Sizeof(c1), 24, "C1 struct{uint32; string} 尺寸 24")
	eq(Ptrdata(c1), 16, "C1 pointer bytes = 16")
	c2 := Struct("C2", Fld("s", "string"), FT("p", PtrTo("uint32")))
	eq(Sizeof(c2), 24, "C2 struct{string; *uint32} 尺寸 24")
	eq(Ptrdata(c2), 24, "C2 pointer bytes = 24（要扫到 *uint32 之后）")
	c3 := Struct("C3", Fld("s", "string"), Fld("x", "uint32"))
	eq(Sizeof(c3), 24, "C3 struct{string; uint32} 尺寸 24")
	eq(Ptrdata(c3), 8, "C3 pointer bytes = 8（指针后即停）")
}

func groupD() {
	t := Struct("Tri", Fld("A", "bool"), Fld("B", "int64"), Fld("C", "bool"))
	eq(Sizeof(t), 24, "D1 未排序尺寸 24")
	eq(Sizeof(Reorder(t)), 16, "D2 重排后尺寸 16")
	eq(OptimalOrder(t), []int{1, 0, 2}, "D3 最优顺序把 int64 排最前")
	eq(Diagnostic(t),
		"Tri has size 24 but the optimal size is 16 leading to a waste of 8 bytes (33%)",
		"D4 诊断文本（24/16 都是 size class，不加后缀）")

	p := Struct("Padded", Fld("A", "bool"), Fld("B", "int64"),
		FT("C", Arr("int8", 32)), Fld("D", "bool"))
	eq(Sizeof(p), 56, "D5 Padded 实际尺寸 56")
	eq(OptimalOrder(p), []int{1, 2, 0, 3}, "D6 最优顺序 int64 → [32]byte → 两个 bool")
	eq(Sizeof(Reorder(p)), 48, "D7 重排后尺寸 48")
	eq(Diagnostic(p),
		"Padded has size 56 (allocator size class 64) but the optimal size is 48 "+
			"leading to a waste of 16 bytes (25%)",
		"D8 只有非 size class 的那一侧才带 class 后缀")

	eq(ClassSize(24), 24, "D9 classSize(24) = 24")
	eq(ClassSize(40), 48, "D10 classSize(40) = 48（40 不是 size class）")
	eq(ClassSize(56), 64, "D11 classSize(56) = 64")
	eq(ClassSize(MaxSmall), 32768, "D12 classSize(32768) = 32768")
	eq(ClassSize(MaxSmall+1), -1, "D13 超过 32768 走大对象分配（-1）")
	check(classToSize[1] == 8 && classToSize[len(classToSize)-1] == 32768,
		"D14 size class 表两端正确")

	tz := Struct("TailZero", Fld("a", "int64"), FT("z", Struct("Empty")))
	eq(Sizeof(tz), 16, "D15 尾部零尺寸字段把尺寸从 8 顶到 16")
	eq(OptimalOrder(tz)[0], 1, "D16 零尺寸字段被排到最前")

	lo := Struct("PtrFirst", FT("p", PtrTo("int64")), FT("a", Arr("int8", 100)))
	hi := Struct("PtrLast", FT("a", Arr("int8", 100)), FT("p", PtrTo("int64")))
	eq(Ptrdata(lo), 8, "D17 指针在前 → pointer bytes = 8")
	eq(Ptrdata(hi), 112, "D18 指针在后 → pointer bytes = 112")
	eq(names(hi, OptimalOrder(hi)), []string{"p", "a"}, "D19 重排把含指针字段提前")
	eq(Ptrdata(Reorder(hi)), 8, "D20 重排后 pointer bytes 降到 8")
	eq(Sizeof(hi), Sizeof(Reorder(hi)), "D21 该重排不改尺寸，只改扫描范围")
	eq(Diagnostic(hi),
		"PtrLast has 112 leading bytes of pointer data but optimal value is 8",
		"D22 尺寸不变时走 pointer bytes 诊断")
}

func groupE() {
	t := Struct("Counters", Fld("a", "int64"), Fld("b", "int64"))
	eq(OptimalOrder(t), []int{0, 1}, "E1 两字段都已对齐，重排顺序不变")
	offA, offB := 0, 8
	check(offA/64 == offB/64, "E2 两字段落在同一 64 字节 cache line", offA, offB)
	padded := Struct("CountersPadded", Fld("a", "int64"),
		FT("pad", Arr("int8", 56)), Fld("b", "int64"))
	eq(Sizeof(padded), 72, "E3 8 + 56 + 8 = 72（填充 56 字节）")
	check(0/64 != 64/64, "E4 a 与 b 不再同行（0 行 vs 1 行）")
}

func groupF() {
	for _, c := range validCases {
		eq(CheckChain(c.objSize, c.t1Size, c.steps), []string{}, "F 正例合法: "+c.name)
	}
	bad := CheckChain(24, 8, invalidPastEnd)
	check(len(bad) == 1 && contains(bad[0], "原对象内部"), "F1 越界一个字节即 INVALID", bad)

	bad = CheckChain(24, 8, invalidTempBeforePtr)
	check(len(bad) == 1 && contains(bad[0], "存进过变量"), "F2 uintptr 不得存变量", bad)

	bad = CheckChain(24, 8, invalidNil)
	check(len(bad) == 1 && contains(bad[0], "不能是 nil"), "F3 nil 指针不能做算术", bad)

	bad = CheckChain(24, 8, invalidSyscallTemp)
	check(len(bad) == 1 && contains(bad[0], "就地转换"), "F4 系统调用实参须就地转换", bad)

	bad = CheckChain(16, 8, invalidReflectTemp)
	check(len(bad) == 1 && contains(bad[0], "存进过变量"),
		"F5 reflect.Pointer() 结果须就地转换", bad)

	bad = CheckChain(16, 8, invalidHeaderDecl)
	check(len(bad) == 1 && contains(bad[0], "不得声明"),
		"F6 不得声明 reflect header 普通变量", bad)

	bad = CheckChain(8, 8, invalidSmallerT1)
	check(len(bad) == 1 && contains(bad[0], "T2 不大于 T1"), "F7 模式 1 要求 T2 不大于 T1", bad)

	edge := []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_uintptr"},
		{Kind: "add_offset", N: 23}, {Kind: "uintptr_to_unsafe"}}
	eq(CheckChain(24, 8, edge), []string{}, "F8 offset == size-1 合法")
	eq(CheckChain(8, 8, p2Print), []string{}, "F9 转 uintptr 后仅打印合法")

	roundBad := []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_uintptr"},
		{Kind: "add_offset", N: 300}, {Kind: "and_not", N: 63},
		{Kind: "uintptr_to_unsafe"}}
	bad = CheckChain(256, 8, roundBad)
	check(len(bad) == 1 && contains(bad[0], "原对象内部"), "F10 &^ 取整不豁免越界检查", bad)

	roundOK := []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_uintptr"},
		{Kind: "add_offset", N: 250}, {Kind: "and_not", N: 63},
		{Kind: "uintptr_to_unsafe"}}
	eq(CheckChain(256, 8, roundOK), []string{}, "F11 &^ 只减小偏移（250 → 192），仍在对象内")
}

func groupG() {
	t := Struct("Mix", Fld("A", "bool"), Fld("B", "int64"),
		Fld("C", "uint16"), Fld("D", "uint32"))
	offs := []int{}
	o, mx := 0, 1
	for _, f := range t.Fields {
		o = Align(o, Alignof(f.T))
		offs = append(offs, o)
		if a := Alignof(f.T); a > mx {
			mx = a
		}
		o += Sizeof(f.T)
	}
	eq(offs, []int{0, 8, 16, 20}, "G1 Offsetof 表：0 / 8 / 16 / 20")
	eq(Align(o, mx), Sizeof(t), "G2 末字段结尾对齐后即结构体尺寸")
	eq(Sizeof(t), 24, "G3 Mix 尺寸 24")
	eq(Ptrdata(t), 0, "G4 无指针字段 → pointer bytes 为 0")
	eq(Diagnostic(t),
		"Mix has size 24 but the optimal size is 16 leading to a waste of 8 bytes (33%)",
		"G5 未排序时按尺寸报诊断")
	eq(Diagnostic(Reorder(t)), "", "G6 重排后无诊断（已最优）")
	eq(names(t, OptimalOrder(t)), []string{"B", "D", "C", "A"},
		"G7 最优顺序：对齐大的先来，逐级降序")
	eq(Sizeof(Reorder(t)), 16, "G8 重排后尺寸 16")
}

func contains(s, sub string) bool {
	for i := 0; i+len(sub) <= len(s); i++ {
		if s[i:i+len(sub)] == sub {
			return true
		}
	}
	return false
}
