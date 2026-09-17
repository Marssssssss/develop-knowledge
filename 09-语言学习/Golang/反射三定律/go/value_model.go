package main

// reflect.Value 的 flag 位模型：判定条件照抄 go1.24.0 src/reflect/value.go 的
// Elem / Field / CanSet / mustBeAssignable / Interface / ro 实现。

// ---------------------------------------------------------------- RValue
// 对应 reflect.Value 的 typ / ptr / flag 三字段；fl == 0 即 zero Value。
type RValue struct {
	kind    Kind
	typ     string
	fl      flag
	slot    int
	store   *Store
	structT *StructType
}

func ZeroValue() RValue { return RValue{} }

func (v RValue) IsValid() bool { return v.fl != 0 }

func (v RValue) KindName() string { return kindNames[v.kind] }

func (v RValue) TypeName() string {
	if v.fl == 0 {
		panic("reflect: call of reflect.Value.Type on zero Value")
	}
	return v.typ
}

func (v RValue) CanAddr() bool { return v.fl&flagAddr != 0 }

// CanSet 对应源码：return v.flag&(flagAddr|flagRO) == flagAddr
func (v RValue) CanSet() bool { return v.fl&(flagAddr|flagRO) == flagAddr }

func (v RValue) IsRO() bool { return v.fl&flagRO != 0 }

func (v RValue) Flag() flag { return v.fl }

func (v RValue) String() string {
	if v.fl == 0 {
		return "<invalid Value>"
	}
	return "<" + v.typ + " Value>"
}

// valueError 对应 ValueError.Error() 的消息格式。
func (v RValue) valueError(method string) string {
	if v.kind == Invalid {
		return "reflect: call of reflect.Value." + method + " on zero Value"
	}
	return "reflect: call of reflect.Value." + method + " on " +
		v.KindName() + " Value"
}

func (v RValue) mustBe(k Kind, method string) {
	if v.kind != k {
		panic(v.valueError(method))
	}
}

func (v RValue) mustBeExported(method string) {
	if v.fl&flagRO != 0 {
		panic("reflect: reflect.Value." + method +
			" using value obtained using unexported field")
	}
}

// mustBeAssignable 对应源码的两个 panic 分支，顺序固定：先 RO，再 Addr。
func (v RValue) mustBeAssignable(method string) {
	if v.fl&flagRO != 0 {
		panic("reflect: reflect.Value." + method +
			" using value obtained using unexported field")
	}
	if v.fl&flagAddr == 0 {
		panic("reflect: reflect.Value." + method +
			" using unaddressable value")
	}
}

// ---------------------------------------------------------------- 变换
func (v RValue) Elem() RValue {
	if v.kind == Interface {
		if v.store.Load(v.slot) == nil {
			return ZeroValue()
		}
		x := v.store.Load(v.slot).(*Goval).Copy()
		fl := flag(x.Kind) | flagIndir | roCollapse(v.fl)
		return RValue{x.Kind, x.Typ, fl, v.store.Alloc(x.Value), v.store, x.Struct}
	}
	if v.kind != Pointer {
		panic(v.valueError("Elem"))
	}
	if v.store.Load(v.slot) == nil {
		return ZeroValue()
	}
	p := v.store.Load(v.slot).(*Ptr)
	in := p.Pointee
	fl := (v.fl & flagRO) | flagIndir | flagAddr | flag(in.Kind)
	return RValue{in.Kind, in.Typ, fl, p.Target, v.store, in.Struct}
}

func (v RValue) Field(i int) RValue {
	if v.kind != Struct {
		panic(v.valueError("Field"))
	}
	obj := v.store.Load(v.slot).(*StructObj)
	if i < 0 || i >= len(obj.Slots) {
		panic("reflect: Field index out of range")
	}
	f := v.structT.Fields[i]
	// 源码：继承权限位但清掉 flagEmbedRO，也不继承 flagMethod
	fl := (v.fl & (flagStickyRO | flagIndir | flagAddr)) | flag(f.Kind)
	if !f.Exported() {
		if f.Embedded {
			fl |= flagEmbedRO
		} else {
			fl |= flagStickyRO
		}
	}
	return RValue{f.Kind, f.Typ, fl, obj.Slots[i], v.store, f.Struct}
}

func (v RValue) FieldByIndex(index []int) RValue {
	if len(index) == 1 {
		return v.Field(index[0])
	}
	out := v
	for _, i := range index {
		out = out.Field(i)
	}
	return out
}

func (v RValue) FieldByName(name string) RValue {
	v.mustBe(Struct, "FieldByName")
	idx, ok := v.structT.FieldByName(name)
	if !ok {
		return ZeroValue()
	}
	return v.FieldByIndex(idx)
}

// Interface 是第二定律：把 (值, 具体类型) 重新装回 interface{}。
func (v RValue) Interface() interface{} {
	if v.fl == 0 {
		panic(v.valueError("Interface"))
	}
	if v.fl&flagRO != 0 {
		panic("reflect.Value.Interface: cannot return value obtained " +
			"from unexported field or method")
	}
	return v.store.Load(v.slot)
}

func (v RValue) Addr() RValue {
	if !v.CanAddr() {
		panic("reflect: reflect.Value.Addr using unaddressable value")
	}
	g := &Goval{v.kind, v.typ, v.store.Load(v.slot), v.structT}
	ps := v.store.Alloc(&Ptr{v.slot, g})
	return RValue{Pointer, "*" + v.typ, flag(Pointer) | flagIndir, ps, v.store, nil}
}

// ---------------------------------------------------------------- 取值/写值
func (v RValue) GetScalar() (interface{}, string, string) {
	name, ok := widestGetter[v.kind]
	if !ok {
		panic(v.valueError("Int"))
	}
	target := "int64"
	switch name {
	case "Int":
		return toInt64(v.store.Load(v.slot)), name, target
	case "Uint":
		return toInt64(v.store.Load(v.slot)), name, "uint64"
	default:
		return toFloat64(v.store.Load(v.slot)), name, "float64"
	}
}

func (v RValue) SetInt(x int64, method string) {
	v.mustBeAssignable(method)
	if _, ok := intKindBits[v.kind]; !ok {
		if _, ok2 := uintKindBits[v.kind]; !ok2 {
			panic(v.valueError(method))
		}
	}
	v.store.Put(v.slot, truncate(v.kind, x))
}

func (v RValue) SetFloat(x float64, method string) {
	v.mustBeAssignable(method)
	if v.kind != Float32 && v.kind != Float64 {
		panic(v.valueError(method))
	}
	v.store.Put(v.slot, x)
}

func (v RValue) SetString(x string, method string) {
	v.mustBeAssignable(method)
	if v.kind != String {
		panic(v.valueError(method))
	}
	v.store.Put(v.slot, x)
}

func (v RValue) IsNil() bool {
	if v.fl&flagMethod != 0 {
		return false
	}
	switch v.kind {
	case Chan, Func, Map, Pointer, UnsafePointer, Interface, Slice:
		return v.store.Load(v.slot) == nil
	}
	panic(v.valueError("IsNil"))
}

// ---------------------------------------------------------------- 入口函数
// ValueOf 对应 reflect.ValueOf：把实参复制进新 slot，不设 flagAddr（不可设置）。
func ValueOf(store *Store, g *Goval) RValue {
	if g == nil {
		return ZeroValue()
	}
	c := g.Copy()
	return RValue{c.Kind, c.Typ, flag(c.Kind) | flagIndir, store.Alloc(c.Value), store, c.Struct}
}

// AddrValue 对应 reflect.ValueOf(&x)：得到指向 slot 的 Ptr Value，自身不可设置。
func AddrValue(store *Store, g *Goval, slot int) RValue {
	ps := store.Alloc(&Ptr{slot, g})
	return RValue{Pointer, "*" + g.Typ, flag(Pointer) | flagIndir, ps, store, nil}
}

// NewStruct 建一个结构体变量，返回它的 Goval 与 slot。
func NewStruct(store *Store, st *StructType, values []interface{}) (*Goval, int) {
	slots := make([]int, len(values))
	for i, v := range values {
		slots[i] = store.Alloc(v)
	}
	obj := &StructObj{slots}
	slot := store.Alloc(obj)
	return &Goval{Struct, st.Name, obj, st}, slot
}

func toInt64(x interface{}) int64 {
	switch n := x.(type) {
	case int:
		return int64(n)
	case int64:
		return n
	case float64:
		return int64(n)
	}
	return 0
}

func toFloat64(x interface{}) float64 {
	switch n := x.(type) {
	case float64:
		return n
	case float32:
		return float64(n)
	case int:
		return float64(n)
	case int64:
		return float64(n)
	}
	return 0
}
