package main

import "fmt"

func tags(xs []*Item) []string {
	out := make([]string, 0, len(xs))
	for _, x := range xs {
		t := x.Tag
		if x.IsDup {
			t += "'"
		}
		out = append(out, t)
	}
	return out
}

func main() {
	fmt.Println("== 1. class_data_bits_t：指针与 3 个 FAST 标志挤在同一个字里 ==")
	arch := LP64IPhone()
	ro := &ClassRo{FlagsField: 0, Name: "MyClass",
		BaseMethods:    &ListOrList{Kind: "list"},
		BaseProperties: &ListOrList{Kind: "list"},
		BaseProtocols:  &ListOrList{Kind: "list"}}
	rw := newClassRw(arch, ro, RwRealized, 0x1000)
	bits := newClassDataBits(arch, 0x1000)
	bits.Bits = bits.ptra.Sign(alloc(ro) | FastIsSwiftStable | FastHasDefaultRR)
	fmt.Printf("  RO 态  bits = 0x%016x\n", bits.Bits)
	fmt.Printf("    has_rw_pointer = %v\n", bits.HasRwPointer(bits.Bits))
	fmt.Printf("    safe_ro()      = %v\n", nameOf(bits))
	if err := bits.SetData(rw); err != nil {
		fmt.Println("  SetData 出错:", err)
		return
	}
	fmt.Printf("  RW 态  bits = 0x%016x\n", bits.Bits)
	fmt.Printf("    has_rw_pointer = %v\n", bits.HasRwPointer(bits.Bits))
	fmt.Printf("    保留下来的 FAST 标志 = 0x%x\n", authedBits(bits)&FastFlagsMask64)

	fmt.Println()
	fmt.Println("== 2. 32 位没有 FAST_IS_RW_POINTER，判据退化成 RW_REALIZED ==")
	a32 := ILP32()
	ro32 := &ClassRo{FlagsField: 0, Name: "MyClass32",
		BaseMethods:    &ListOrList{Kind: "list"},
		BaseProperties: &ListOrList{Kind: "list"},
		BaseProtocols:  &ListOrList{Kind: "list"}}
	for _, tc := range []struct {
		flags uint32
		label string
	}{{RwRealized, "带 RW_REALIZED"}, {0, "不带"}} {
		rw32 := newClassRw(a32, ro32, tc.flags, 0x2000)
		b32 := newClassDataBits(a32, 0x2000)
		if err := b32.SetData(rw32); err != nil {
			fmt.Println("  SetData 出错:", err)
			continue
		}
		fmt.Printf("  %-12s -> has_rw_pointer = %-5v  safe_ro() = %s\n",
			tc.label, b32.HasRwPointer(b32.Bits), typeName(b32))
	}

	fmt.Println()
	fmt.Println("== 3. extAlloc：脏内存只在需要改类时才分配 ==")
	m0, m1, m2 := &Item{Tag: "base0"}, &Item{Tag: "base1"}, &Item{Tag: "base2"}
	p0, p1 := &Item{Tag: "prop0"}, &Item{Tag: "prop1"}
	ro3 := &ClassRo{FlagsField: 0, Name: "Dirty",
		BaseMethods:    &ListOrList{Kind: "rel", Rel: []*Item{m0, m1, m2}},
		BaseProperties: &ListOrList{Kind: "rel", Rel: []*Item{p0, p1}},
		BaseProtocols:  &ListOrList{Kind: "list"}}
	rw3 := newClassRw(arch, ro3, 0, 0x3000)
	fmt.Printf("  分配前 ext() = %v\n", rw3.Ext())
	ms, _ := rw3.Methods()
	fmt.Printf("  methods() = %v\n", tags(ms))
	for _, deep := range []bool{false, true} {
		rwd := newClassRw(arch, ro3, 0, 0x3100)
		rwe, err := ExtAlloc(rwd, ro3, deep)
		if err != nil {
			fmt.Println("  ExtAlloc 出错:", err)
			continue
		}
		ps, _ := rwd.Properties()
		fmt.Printf("  deep=%-5v version=%d methods=%v properties=%v\n",
			deep, rwe.Version, tags(rwd.Ext().Methods.Lists()), tags(ps))
	}

	fmt.Println()
	fmt.Println("== 4. rwe 分配后列表存储形态与后续 attach 的排布 ==")
	rw4 := newClassRw(arch, ro3, 0, 0x4000)
	e, err := ExtAllocIfNeeded(rw4)
	if err != nil {
		fmt.Println("  ExtAllocIfNeeded 出错:", err)
		return
	}
	e2, _ := ExtAllocIfNeeded(rw4)
	fmt.Printf("  ext() 幂等 = %v\n", e == e2)
	_ = e.Methods.AttachLists([]*Item{{Tag: "cat0"}})
	m4, _ := rw4.Methods()
	fmt.Printf("  挂上一个分类后 methods = %v\n", tags(m4))
	ok1, _ := SetDemangledName(e, "MyClass", "_TtC7MyClass", true)
	ok2, _ := SetDemangledName(e, "Other", "_TtC7MyClass", true)
	fmt.Printf("  解修饰名 CAS 首次 = %v，再次 = %v\n", ok1, ok2)
	_ = rw4.ChangeFlags(uint32(1)<<29, 0)
	fmt.Printf("  changeFlags 后 flags = 0x%x\n", rw4.FlagsField)
}

func authedBits(b *ClassDataBits) uint64 {
	v, err := b.ptra.Auth(b.Bits)
	if err != nil {
		return 0
	}
	return v
}

func nameOf(b *ClassDataBits) string {
	o, err := b.SafeRo(true)
	if err != nil {
		return "<err>"
	}
	if r, ok := o.(*ClassRo); ok {
		return r.Name
	}
	return "<not a class_ro_t>"
}

func typeName(b *ClassDataBits) string {
	o, err := b.SafeRo(true)
	if err != nil {
		return "<err>"
	}
	switch o.(type) {
	case *ClassRo:
		return "ClassRo"
	case *ClassRw:
		return "ClassRw"
	}
	return "?"
}
