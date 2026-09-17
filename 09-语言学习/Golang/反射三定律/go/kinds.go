package main

// reflect.Kind 与 reflect.Value.flag 的常量表。
// 数值与顺序照抄 go1.24.0：Kind 的 iota 块在 src/reflect/type.go，
// flag 常量块在 src/reflect/value.go。

// Kind 共 27 个（flagKindWidth = 5，源码注释 "there are 27 kinds"）。
type Kind int

const (
	Invalid Kind = iota
	Bool
	Int
	Int8
	Int16
	Int32
	Int64
	Uint
	Uint8
	Uint16
	Uint32
	Uint64
	Uintptr
	Float32
	Float64
	Complex64
	Complex128
	Array
	Chan
	Func
	Interface
	Map
	Pointer
	Slice
	String
	Struct
	UnsafePointer
)

var kindNames = []string{
	"Invalid", "Bool", "Int", "Int8", "Int16", "Int32", "Int64",
	"Uint", "Uint8", "Uint16", "Uint32", "Uint64", "Uintptr",
	"Float32", "Float64", "Complex64", "Complex128", "Array", "Chan",
	"Func", "Interface", "Map", "Pointer", "Slice", "String",
	"Struct", "UnsafePointer",
}

type flag uintptr

const (
	flagKindWidth = 5
	flagKindMask  = 1<<flagKindWidth - 1
	flagStickyRO  = 1 << 5 // 经未导出且非嵌入字段得到 → 只读
	flagEmbedRO   = 1 << 6 // 经未导出嵌入字段得到 → 只读
	flagIndir     = 1 << 7 // ptr 指向数据
	flagAddr      = 1 << 8 // CanAddr 为真
	flagMethod    = 1 << 9 // 方法值
	flagRO        = flagStickyRO | flagEmbedRO
)

// roCollapse 对应源码 func (f flag) ro() flag：任意 RO 都折叠成 flagStickyRO。
// 只被 Elem() 的 Interface 分支用到。
func roCollapse(f flag) flag {
	if f&flagRO != 0 {
		return flagStickyRO
	}
	return 0
}

// kindOf 对应源码 func (f flag) kind() Kind。
func kindOf(f flag) Kind { return Kind(f & flagKindMask) }

// getter 走"能装下该值的最大类型"，故按 Kind 分组。
var (
	intKindBits  = map[Kind]uint{Int: 64, Int8: 8, Int16: 16, Int32: 32, Int64: 64}
	uintKindBits = map[Kind]uint{
		Uint: 64, Uint8: 8, Uint16: 16, Uint32: 32, Uint64: 64, Uintptr: 64,
	}
	widestGetter = map[Kind]string{}
)

func init() {
	for k := range intKindBits {
		widestGetter[k] = "Int"
	}
	for k := range uintKindBits {
		widestGetter[k] = "Uint"
	}
	widestGetter[Float32] = "Float"
	widestGetter[Float64] = "Float"
}

// truncate 按 Go 的窄整型补码语义截断：int8(300) == 44，int16(32768) == -32768。
// 注意不能用 `% ` 直接取模：Go 的 % 保留被除数符号（-1%256 == -1），负数会算错，
// 所以统一在 uint64 空间按位掩码取低位再判符号。
func truncate(k Kind, v int64) int64 {
	if b, ok := intKindBits[k]; ok {
		if b == 64 {
			return v
		}
		m := uint64(1) << b
		u := uint64(v) & (m - 1)
		if u >= m>>1 {
			return int64(u) - int64(m)
		}
		return int64(u)
	}
	if b, ok := uintKindBits[k]; ok {
		if b == 64 {
			return v
		}
		return int64(uint64(v) & ((uint64(1) << b) - 1))
	}
	return v
}
