package main

// 反射三定律的 Go 版断言组（与 python/main.py 同一批场景）。
// 分组：A Kind/Type 基础 · B zero Value 不变量 · C 第一定律 · D 第二定律
//      E 第三定律（可设置性）· F 结构体字段与只读位的继承 · G panic 条件表

func groupA() {
	eq(len(kindNames), 27, "A1 Kind 共 27 个")
	check(27 <= (1<<5), "A2 flagKindWidth=5 能放下 27 个 Kind")
	eq(kindNames[0], "Invalid", "A3 Invalid 排第一")
	eq(kindNames[22], "Pointer", "A4 Pointer=22")
	eq(kindNames[25], "Struct", "A5 Struct=25")
	eq(kindNames[26], "UnsafePointer", "A6 UnsafePointer=26")

	eq(int(flagStickyRO), 1<<5, "A7 flagStickyRO = 1<<5")
	eq(int(flagEmbedRO), 1<<6, "A8 flagEmbedRO = 1<<6")
	eq(int(flagIndir), 1<<7, "A9 flagIndir = 1<<7")
	eq(int(flagAddr), 1<<8, "A10 flagAddr = 1<<8")
	eq(int(flagMethod), 1<<9, "A11 flagMethod = 1<<9")
	eq(int(flagRO), int(flagStickyRO|flagEmbedRO), "A12 flagRO 是两者之并")

	st := NewStore()
	v := ValueOf(st, &Goval{Int, "MyInt", int64(7), nil})
	eq(v.TypeName(), "MyInt", "A13 Type() 给静态类型 MyInt")
	eq(v.KindName(), "Int", "A14 Kind() 只给底层 Int")
	eq(int(kindOf(v.Flag())), int(Int), "A15 flag 低 5 位就是 Kind")

	val, name, target := v.GetScalar()
	eq(val, int64(7), "A16 有符号经 Int64 取值")
	eq(name, "Int", "A17 getter 名 Int")
	eq(target, "int64", "A18 目标类型 int64")
	eq(widestGetter[Int8], "Int", "A19 int8 走 Int")
	eq(widestGetter[Uint16], "Uint", "A20 uint16 走 Uint")
	eq(widestGetter[Float32], "Float", "A21 float32 走 Float")
}

func groupB() {
	z := ZeroValue()
	eq(z.IsValid(), false, "B1 zero Value 无效")
	eq(int(z.Flag()), 0, "B2 zero Value 的 flag == 0")
	eq(int(z.kind), int(Invalid), "B3 zero Value 的 Kind 是 Invalid")
	eq(z.String(), "<invalid Value>", "B4 String() 给 <invalid Value>")
	eq(z.CanAddr(), false, "B5 zero Value 不可取址")
	eq(z.CanSet(), false, "B6 zero Value 不可设置")
	mustPanic(func() { z.Elem() }, "on zero Value", "B7 Elem() panic")
	mustPanic(func() { z.Interface() }, "on zero Value", "B8 Interface() panic")
	mustPanic(func() { z.IsNil() }, "on zero Value", "B9 IsNil() panic")
	mustPanic(func() { z.TypeName() }, "on zero Value", "B10 Type() panic")
	mustPanic(func() { z.Field(0) }, "on zero Value", "B11 Field() panic")
	eq(ValueOf(NewStore(), nil).IsValid(), false, "B12 ValueOf(nil) 得 zero Value")
}

func groupC() {
	st := NewStore()
	xslot := st.Alloc(3.4) // var x float64 = 3.4
	v := ValueOf(st, &Goval{Float64, "float64", st.Load(xslot), nil})

	eq(v.IsValid(), true, "C1 拿到有效 Value")
	eq(v.TypeName(), "float64", "C2 Type() = float64")
	eq(v.CanAddr(), false, "C3 ValueOf 的结果不可取址")

	st.Put(xslot, 99.0) // 之后改 x
	fv, _, _ := v.GetScalar()
	eq(fv, 3.4, "C4 ValueOf 拿的是拷贝，改 x 不影响 v")

	// 接口变量里存的永远是 (值, 具体类型)，不能嵌套接口类型
	inner := &Goval{Int, "int", int64(42), nil}
	iface := &Goval{Interface, "interface {}", inner, nil}
	ei := ValueOf(st, iface).Elem()
	eq(ei.KindName(), "Int", "C5 接口里装的是具体类型 int")
	eq(ei.TypeName(), "int", "C6 剥出后类型不是 interface{}")

	for _, g := range []*Goval{
		{Int, "int", int64(1), nil},
		{String, "string", "s", nil},
		{Struct, "T", nil, nil},
	} {
		tv := ValueOf(st, g)
		check(tv.TypeName() != "interface {}", "C7 空接口不掩盖具体类型", tv.TypeName())
		eq(tv.TypeName(), g.Typ, "C8 TypeOf 给出具体类型名")
	}
}

func groupD() {
	st := NewStore()
	v := ValueOf(st, &Goval{Float64, "float64", 3.4, nil})
	eq(v.Interface(), 3.4, "D1 Interface() 是 ValueOf 的逆")
	check(indexOf(v.String(), "3.4") < 0, "D2 String() 不含值本身", v.String())
	eq(v.String(), "<float64 Value>", "D3 String() 的格式")

	outer := &StructType{"outer", []Field{{Name: "A", Kind: Float64, Typ: "float64"}}}
	ogv, oslot := NewStruct(st, outer, []interface{}{3.4})
	s := AddrValue(st, ogv, oslot).Elem()
	eq(s.Field(0).Interface(), 3.4, "D4 导出字段可以 Interface()")
}

func groupE() {
	st := NewStore()
	xslot := st.Alloc(3.4)
	gv := &Goval{Float64, "float64", 3.4, nil}

	vx := ValueOf(st, gv) // ValueOf(x)
	eq(vx.CanSet(), false, "E1 ValueOf(x) 不可设置")
	mustPanic(func() { vx.SetFloat(7.1, "SetFloat") },
		"using unaddressable value", "E2 SetFloat 报 unaddressable")
	eq(st.Load(xslot), 3.4, "E3 失败的 Set 没有副作用")

	p := AddrValue(st, gv, xslot) // ValueOf(&x)
	eq(p.TypeName(), "*float64", "E4 Type() = *float64")
	eq(p.CanSet(), false, "E5 指针 Value 自身不可设置")
	eq(p.CanAddr(), false, "E6 指针 Value 自身不可取址")

	vv := p.Elem() // v := p.Elem()
	eq(vv.CanSet(), true, "E7 Elem() 后可设置")
	eq(vv.CanAddr(), true, "E8 Elem() 后可取址")
	vv.SetFloat(7.1, "SetFloat")
	eq(st.Load(xslot), 7.1, "E9 改到了原变量 x 本身")
	back, _, _ := vv.GetScalar()
	eq(back, 7.1, "E10 回读一致")
}

func mkStruct(st *Store) (*Goval, int, *StructType, *StructType) {
	inner := &StructType{"inner", []Field{
		{Name: "X", Kind: Int, Typ: "int"},
		{Name: "y", Kind: Int, Typ: "int"},
	}}
	outer := &StructType{"outer", []Field{
		{Name: "A", Kind: Int, Typ: "int"},
		{Name: "b", Kind: Int, Typ: "int"},
		{Name: "inner", Kind: Struct, Typ: "inner", Struct: inner, Embedded: true},
	}}
	ogv, oslot := NewStruct(st, outer, []interface{}{int64(23), int64(7), nil})
	innerGv, islot := NewStruct(st, inner, []interface{}{int64(23), int64(7)})
	obj := st.Load(oslot).(*StructObj)
	st.Put(obj.Slots[2], st.Load(islot))
	_ = innerGv
	return ogv, oslot, outer, inner
}

func groupF() {
	st := NewStore()
	ogv, oslot, _, _ := mkStruct(st)
	s := AddrValue(st, ogv, oslot).Elem()

	eq(s.CanSet(), true, "F1 可取的地址的结构体 Value 可设置")
	eq(s.Field(0).CanSet(), true, "F2 导出字段可设置")
	eq(s.Field(0).CanAddr(), true, "F3 导出字段可取址")

	ub := s.Field(1)
	eq(ub.IsRO(), true, "F4 未导出非嵌入字段 → 只读")
	check(ub.Flag()&flagStickyRO != 0, "F5 置的是 flagStickyRO")
	check(ub.Flag()&flagEmbedRO == 0, "F6 不置 flagEmbedRO")
	eq(ub.CanAddr(), true, "F7 只读但可取址（CanSet 不等于 CanAddr）")
	eq(ub.CanSet(), false, "F8 只读即不可设置")
	mustPanic(func() { ub.Interface() },
		"cannot return value obtained from unexported",
		"F9 未导出字段 Interface() panic")
	ubv, ubn, ubt := ub.GetScalar()
	eq(ubv, int64(7), "F10 未导出字段仍可用 Int() 读值")
	eq(ubn, "Int", "F10b getter 名")
	eq(ubt, "int64", "F10c 目标类型")

	emb := s.Field(2)
	eq(emb.IsRO(), true, "F11 未导出嵌入字段 → 只读")
	check(emb.Flag()&flagEmbedRO != 0, "F12 置的是 flagEmbedRO")
	check(emb.Flag()&flagStickyRO == 0, "F13 不是 StickyRO")
	eq(emb.CanSet(), false, "F14 嵌入字段本身不可设置")

	ex := emb.Field(0)
	eq(ex.IsRO(), false, "F15 嵌入字段的导出成员不带 RO")
	eq(ex.CanSet(), true, "F16 因此可以设置（Field 掩码清掉 EmbedRO）")
	eq(emb.Field(1).IsRO(), true, "F17 嵌入字段的未导出成员仍只读")
	check(emb.Field(1).Flag()&flagStickyRO != 0, "F18 置 StickyRO 而非 EmbedRO")

	eq(s.FieldByName("X").CanSet(), true, "F19 提升的导出字段可设置")
	eq(s.FieldByName("y").CanSet(), false, "F20 提升的未导出字段不可设置")
	eq(s.FieldByName("A").CanSet(), true, "F21 直接字段可设置")
	eq(s.FieldByName("nope").IsValid(), false, "F22 找不到字段返回 zero Value")

	l := &StructType{"l", []Field{{Name: "X", Kind: Int, Typ: "int"}}}
	r := &StructType{"r", []Field{{Name: "X", Kind: Int, Typ: "int"}}}
	amb := &StructType{"amb", []Field{
		{Name: "l", Kind: Struct, Typ: "l", Struct: l, Embedded: true},
		{Name: "r", Kind: Struct, Typ: "r", Struct: r, Embedded: true},
	}}
	_, ok := amb.FieldByName("X")
	eq(ok, false, "F23 同深度两名候选 → 歧义")

	eq(int(roCollapse(flagEmbedRO)), int(flagStickyRO), "F24 ro() 折叠 EmbedRO")
	eq(int(roCollapse(0)), 0, "F25 非 RO 折叠为 0")

	ms := AddrValue(st, ogv, oslot).Elem()
	ms.fl |= flagMethod
	eq(int(ms.Field(0).Flag()&flagMethod), 0, "F26 Field() 清掉 flagMethod")
}

func groupG() {
	st := NewStore()
	vi := ValueOf(st, &Goval{Int, "int", int64(5), nil})
	mustPanic(func() { vi.Elem() }, "on Int Value", "G1 Elem() 非指针/接口")
	mustPanic(func() { vi.Field(0) }, "on Int Value", "G2 Field() 非结构体")
	mustPanic(func() { vi.IsNil() }, "on Int Value", "G3 IsNil() 非可空类型")
	mustPanic(func() { vi.Addr() }, "unaddressable value", "G4 Addr() 需可取址")

	nilp := RValue{Pointer, "*int", flag(Pointer) | flagIndir, st.Alloc(nil), st, nil}
	eq(nilp.Elem().IsValid(), false, "G5 nil 指针 Elem() 返回 zero Value")

	ogv, oslot, _, _ := mkStruct(st)
	s := AddrValue(st, ogv, oslot).Elem()
	mustPanic(func() { s.Field(99) }, "reflect: Field index out of range", "G6 越界")

	vf := ValueOf(st, &Goval{Float64, "float64", 1.0, nil})
	mustPanic(func() { vf.SetInt(1, "SetInt") },
		"using unaddressable value", "G7 不可设置时先报 unaddressable")
	fslot := st.Alloc(1.0)
	vfa := AddrValue(st, &Goval{Float64, "float64", 1.0, nil}, fslot).Elem()
	mustPanic(func() { vfa.SetInt(1, "SetInt") }, "on Float64 Value",
		"G7b 可设置但 Kind 不符")

	eq(ValueOf(st, &Goval{Slice, "[]int", nil, nil}).IsNil(), true, "G8 nil 切片")
	eq(ValueOf(st, &Goval{Chan, "chan int", nil, nil}).IsNil(), true, "G9 nil chan")
	nilp.fl |= flagMethod
	eq(nilp.IsNil(), false, "G10 方法值即使底层为 nil 也返回 false")
	nilp.fl &^= flagMethod

	mustPanic(func() {
		ValueOf(st, &Goval{UnsafePointer, "unsafe.Pointer", nil, nil}).
			SetString("x", "SetString")
	}, "using unaddressable value", "G11 先报 unaddressable")

	st2 := NewStore()
	slot8 := st2.Alloc(int64(0))
	v8 := AddrValue(st2, &Goval{Int8, "int8", int64(0), nil}, slot8).Elem()
	v8.SetInt(300, "SetInt")
	eq(st2.Load(slot8), int64(44), "G12 int8(300) == 44")
	slotu := st2.Alloc(int64(0))
	vu := AddrValue(st2, &Goval{Uint8, "uint8", int64(0), nil}, slotu).Elem()
	vu.SetInt(300, "SetInt")
	eq(st2.Load(slotu), int64(44), "G13 uint8(300) == 44")
	eq(truncate(Int8, -129), int64(127), "G14 int8 下溢回绕")
	eq(truncate(Int16, 32768), int64(-32768), "G15 int16 上溢回绕")
	eq(truncate(Int, 5), int64(5), "G16 平台 int 不截断（b == 64 直接返回）")
}
