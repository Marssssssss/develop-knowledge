// x86-64 ELF 重定位:类型表、计算公式与字节落地(Go 侧对照实现)。
//
// 数据来源(本轮实测下载并提取正文):
//
//	System V AMD64 psABI Draft 0.99.6 (July 2, 2012)
//	§4.4 Relocation / §4.4.1 Relocation Types — Table 4.10
//	https://refspecs.linuxfoundation.org/elf/x86_64-abi-0.99.pdf
//
// 与 elf_reloc.py 是同一份模型的两种写法:Python 侧负责断言,本文件负责展示
// 「同一套表在无 Python 运行时时的等价实现」。对 Calculation 列留空的类型,
// 两侧一律返回 ok=false,而不是返回 0 —— 0 是合法结果,不能兼作哨兵。
package main

import (
	"encoding/binary"
	"fmt"
	"math"
	"sort"
)

// ---- psABI Figure 4.1「Relocatable Fields」 ----

// fieldSize 给出字段宽度(字节)。none 表示不写字面值,word64x2 是
// R_X86_64_TLSDESC 专用的一对 word64。
var fieldSize = map[string]int{
	"none":     0,
	"word8":    1,
	"word16":   2,
	"word32":   4,
	"word64":   8,
	"word64x2": 16,
}

// fieldMask 给出无符号掩码,用于「是否能塞进该字段」的判断。
var fieldMask = map[string]uint64{
	"word8":  math.MaxUint8,
	"word16": math.MaxUint16,
	"word32": math.MaxUint32,
	"word64": math.MaxUint64,
}

// ---- psABI §4.4.1 的八个记号 ----

// RelocContext 对应 psABI 的 A/B/G/GOT/L/P/S/Z。
type RelocContext struct {
	Sym     int64 // S:value of the symbol
	Addend  int64 // A:addend
	Place   int64 // P:place of the storage unit being relocated (r_offset)
	Base    int64 // B:base address of the loaded shared object
	PLT     int64 // L:place of the PLT entry
	GotOff  int64 // G:offset into the GOT for this symbol
	GOT     int64 // GOT:address of the global offset table
	SymSize int64 // Z:size of the symbol
}

// RelocSpec 是 Table 4.10 的一行:编号、字段宽、原始公式串。
type RelocSpec struct {
	Value   int
	Field   string
	Formula string
}

// ---- Table 4.10:Relocation Types ----

var relocTable = map[string]RelocSpec{
	"R_X86_64_NONE":             {0, "none", "none"},
	"R_X86_64_64":               {1, "word64", "S + A"},
	"R_X86_64_PC32":             {2, "word32", "S + A - P"},
	"R_X86_64_GOT32":            {3, "word32", "G + A"},
	"R_X86_64_PLT32":            {4, "word32", "L + A - P"},
	"R_X86_64_COPY":             {5, "none", "none"},
	"R_X86_64_GLOB_DAT":         {6, "word64", "S"},
	"R_X86_64_JUMP_SLOT":        {7, "word64", "S"},
	"R_X86_64_RELATIVE":         {8, "word64", "B + A"},
	"R_X86_64_GOTPCREL":         {9, "word32", "G + GOT + A - P"},
	"R_X86_64_32":               {10, "word32", "S + A"},
	"R_X86_64_32S":              {11, "word32", "S + A"},
	"R_X86_64_16":               {12, "word16", "S + A"},
	"R_X86_64_PC16":             {13, "word16", "S + A - P"},
	"R_X86_64_8":                {14, "word8", "S + A"},
	"R_X86_64_PC8":              {15, "word8", "S + A - P"},
	"R_X86_64_DTPMOD64":         {16, "word64", ""},
	"R_X86_64_DTPOFF64":         {17, "word64", ""},
	"R_X86_64_TPOFF64":          {18, "word64", ""},
	"R_X86_64_TLSGD":            {19, "word32", ""},
	"R_X86_64_TLSLD":            {20, "word32", ""},
	"R_X86_64_DTPOFF32":         {21, "word32", ""},
	"R_X86_64_GOTTPOFF":         {22, "word32", ""},
	"R_X86_64_TPOFF32":          {23, "word32", ""},
	"R_X86_64_PC64":             {24, "word64", "S + A - P"},
	"R_X86_64_GOTOFF64":         {25, "word64", "S + A - GOT"},
	"R_X86_64_GOTPC32":          {26, "word32", "GOT + A - P"},
	"R_X86_64_SIZE32":           {32, "word32", "Z + A"},
	"R_X86_64_SIZE64":           {33, "word64", "Z + A"},
	"R_X86_64_GOTPC32_TLSDESC":  {34, "word32", ""},
	"R_X86_64_TLSDESC_CALL":     {35, "none", ""},
	"R_X86_64_TLSDESC":          {36, "word64x2", ""},
	"R_X86_64_IRELATIVE":        {37, "word64", "indirect (B + A)"},
}

// largeModelTable 是 Table 4.11:Large Model Relocation Types。
var largeModelTable = map[string]RelocSpec{
	"R_X86_64_GOT64":      {27, "word64", "G + A"},
	"R_X86_64_GOTPCREL64": {28, "word64", "G + GOT - P + A"},
	"R_X86_64_GOTPC64":    {29, "word64", "GOT - P + A"},
	"R_X86_64_GOTPLT64":   {30, "word64", "G + A"},
	"R_X86_64_PLTOFF64":   {31, "word64", "L - GOT + A"},
}

// ---- Calculation 列的可执行版本 ----

func calcTable() map[string]func(RelocContext) int64 {
	return map[string]func(RelocContext) int64{
		"R_X86_64_NONE":         func(c RelocContext) int64 { return 0 },
		"R_X86_64_64":           func(c RelocContext) int64 { return c.Sym + c.Addend },
		"R_X86_64_PC32":         func(c RelocContext) int64 { return c.Sym + c.Addend - c.Place },
		"R_X86_64_GOT32":        func(c RelocContext) int64 { return c.GotOff + c.Addend },
		"R_X86_64_PLT32":        func(c RelocContext) int64 { return c.PLT + c.Addend - c.Place },
		"R_X86_64_GLOB_DAT":     func(c RelocContext) int64 { return c.Sym },
		"R_X86_64_JUMP_SLOT":    func(c RelocContext) int64 { return c.Sym },
		"R_X86_64_RELATIVE":     func(c RelocContext) int64 { return c.Base + c.Addend },
		"R_X86_64_GOTPCREL":     func(c RelocContext) int64 { return c.GotOff + c.GOT + c.Addend - c.Place },
		"R_X86_64_32":           func(c RelocContext) int64 { return c.Sym + c.Addend },
		"R_X86_64_32S":          func(c RelocContext) int64 { return c.Sym + c.Addend },
		"R_X86_64_16":           func(c RelocContext) int64 { return c.Sym + c.Addend },
		"R_X86_64_PC16":         func(c RelocContext) int64 { return c.Sym + c.Addend - c.Place },
		"R_X86_64_8":            func(c RelocContext) int64 { return c.Sym + c.Addend },
		"R_X86_64_PC8":          func(c RelocContext) int64 { return c.Sym + c.Addend - c.Place },
		"R_X86_64_PC64":         func(c RelocContext) int64 { return c.Sym + c.Addend - c.Place },
		"R_X86_64_GOTOFF64":     func(c RelocContext) int64 { return c.Sym + c.Addend - c.GOT },
		"R_X86_64_GOTPC32":      func(c RelocContext) int64 { return c.GOT + c.Addend - c.Place },
		"R_X86_64_SIZE32":       func(c RelocContext) int64 { return c.SymSize + c.Addend },
		"R_X86_64_SIZE64":       func(c RelocContext) int64 { return c.SymSize + c.Addend },
		"R_X86_64_IRELATIVE":    func(c RelocContext) int64 { return c.Base + c.Addend },
		"R_X86_64_GOT64":        func(c RelocContext) int64 { return c.GotOff + c.Addend },
		"R_X86_64_GOTPCREL64":   func(c RelocContext) int64 { return c.GotOff + c.GOT - c.Place + c.Addend },
		"R_X86_64_GOTPC64":      func(c RelocContext) int64 { return c.GOT - c.Place + c.Addend },
		"R_X86_64_GOTPLT64":     func(c RelocContext) int64 { return c.GotOff + c.Addend },
		"R_X86_64_PLTOFF64":     func(c RelocContext) int64 { return c.PLT - c.GOT + c.Addend },
	}
}

// RelocValue 返回 Table 4.10/4.11 的 Calculation 结果。
// ok=false 表示「这条类型没有通用公式」,不是「值是 0」。
func RelocValue(name string, c RelocContext) (int64, bool) {
	if _, known := relocTable[name]; !known {
		if _, known2 := largeModelTable[name]; !known2 {
			return 0, false
		}
	}
	fn, present := calcTable()[name]
	if !present {
		return 0, false
	}
	return fn(c), true
}

// FieldOf 返回该类型的字段名。
func FieldOf(name string) (string, bool) {
	if s, ok := relocTable[name]; ok {
		return s.Field, true
	}
	if s, ok := largeModelTable[name]; ok {
		return s.Field, true
	}
	return "", false
}

// Fits 判断 value 能否放进该字段。注意这一步只看位宽,
// 是否允许按有符号解释由 Signed32OK 单独判断。
func Fits(value int64, field string) bool {
	mask, ok := fieldMask[field]
	if !ok {
		return true // none / word64x2 不走这条路
	}
	if value < 0 {
		return true // 负值按补码截断写回,调用方自己保证语义正确
	}
	return uint64(value) <= mask
}

// Signed32OK:PC 相对字段(PLT32 / PC32)被当作有符号 32 位位移时是否可用。
func Signed32OK(value int64) bool {
	return value >= math.MinInt32 && value <= math.MaxInt32
}

// ApplyReloc 把 value 按 field 宽度写进 image 的 place 处。
// place 是运行期虚拟地址,image 从 base 开始。
func ApplyReloc(image []byte, place, base uint64, field string, value int64) ([]byte, error) {
	size, ok := fieldSize[field]
	if !ok {
		return nil, fmt.Errorf("unknown field %q", field)
	}
	if size == 0 {
		return image, nil // NONE / COPY:不写字面值
	}
	if field == "word64x2" {
		return nil, fmt.Errorf("R_X86_64_TLSDESC must go through ApplyTLSDesc")
	}
	if !Fits(value, field) {
		return nil, fmt.Errorf("value 0x%x does not fit %s", value, field)
	}
	if place < base {
		return nil, fmt.Errorf("place 0x%x below base 0x%x", place, base)
	}
	lo := place - base
	if lo+uint64(size) > uint64(len(image)) {
		return nil, fmt.Errorf("place 0x%x out of image(len=%d)", place, len(image))
	}
	out := make([]byte, len(image))
	copy(out, image)
	switch size {
	case 1:
		out[lo] = byte(value)
	case 2:
		binary.LittleEndian.PutUint16(out[lo:], uint16(value))
	case 4:
		binary.LittleEndian.PutUint32(out[lo:], uint32(value))
	case 8:
		binary.LittleEndian.PutUint64(out[lo:], uint64(value))
	}
	return out, nil
}

// ApplyTLSDesc 落地一对 word64:(resolver 函数指针, argument)。
func ApplyTLSDesc(image []byte, place, base, resolver, argument uint64) ([]byte, error) {
	if place < base {
		return nil, fmt.Errorf("place below base")
	}
	lo := place - base
	if lo+16 > uint64(len(image)) {
		return nil, fmt.Errorf("TLS descriptor needs 16 bytes")
	}
	out := make([]byte, len(image))
	copy(out, image)
	binary.LittleEndian.PutUint64(out[lo:], resolver)
	binary.LittleEndian.PutUint64(out[lo+8:], argument)
	return out, nil
}

// ---- Elf64_Rela 记录 ----

// RelaInfo 打包 r_info:高 32 位是符号表索引,低 32 位是类型。
func RelaInfo(symIndex, rtype uint32) uint64 {
	return uint64(symIndex)<<32 | uint64(rtype)
}

// RelaSym / RelaType 是 RelaInfo 的逆运算。
func RelaSym(info uint64) uint32 { return uint32(info >> 32) }

func RelaType(info uint64) uint32 { return uint32(info & 0xFFFFFFFF) }

func main() {
	names := make([]string, 0, len(relocTable))
	for n := range relocTable {
		names = append(names, n)
	}
	sort.Strings(names)
	fmt.Println("Table 4.10 共", len(relocTable), "条;Large model 另", len(largeModelTable), "条")

	c := RelocContext{
		Sym: 0x401100, Addend: -4, Place: 0x401006,
		Base: 0x7f0000000000, PLT: 0x401020,
		GotOff: 0x18, GOT: 0x403ff0, SymSize: 0x40,
	}
	for _, n := range names {
		v, ok := RelocValue(n, c)
		f, _ := FieldOf(n)
		if ok {
			fmt.Printf("  %-26s #%-2d %-9s -> %#x\n", n, relocTable[n].Value, f, v)
		} else {
			fmt.Printf("  %-26s #%-2d %-9s -> (no generic formula)\n", n, relocTable[n].Value, f)
		}
	}

	img := make([]byte, 0x40)
	out, err := ApplyReloc(img, 0x401000, 0x401000, "word32", 0xEFBEADDE)
	fmt.Printf("\napply R_X86_64_PC32-like word32 err=%v first4=% X\n", err, out[:4])
	_, err = ApplyReloc(img, 0x401000, 0x401000, "word32", 0x100000000)
	fmt.Println("word32 overflow err:", err)
	fmt.Printf("Signed32OK(0x80000000)=%v Signed32OK(-0x80000001)=%v\n",
		Signed32OK(0x80000000), Signed32OK(-0x80000001))
}
