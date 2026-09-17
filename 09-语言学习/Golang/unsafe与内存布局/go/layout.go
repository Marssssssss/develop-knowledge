package main

// Go 结构体内存布局模型。
// 算法照抄 golang.org/x/tools 的 fieldalignment 分析器里内嵌的 gcSizes
// （sizeof / alignof / ptrdata，复刻 cmd/compile 的布局规则），以及规范
// "Size and alignment guarantees" 一节。

import (
	"fmt"
	"sort"
)

const (
	Word     = 8       // 64 位平台字长
	MaxAlign = 8       // 最大对齐
	MaxSmall = 32768   // runtime 的 maxSmallSize
)

// runtime/sizeclasses.go 的 class_to_size（索引即 size class，class 0 未使用）
var classToSize = []int{
	0, 8, 16, 24, 32, 48, 64, 80, 96, 112,
	128, 144, 160, 176, 192, 208, 224, 240, 256, 288,
	320, 352, 384, 416, 448, 480, 512, 576, 640, 704,
	768, 896, 1024, 1152, 1280, 1408, 1536, 1792, 2048, 2304,
	2688, 3072, 3200, 3456, 4096, 4864, 5376, 6144, 6528, 6784,
	6912, 8192, 9472, 9728, 10240, 10880, 12288, 13568, 14336, 16384,
	18432, 19072, 20480, 21760, 24576, 27264, 28672, 32768,
}

// Typ 是极简类型描述。Tag 取基本类型名，或 ptr/slice/map/chan/func/iface/array/struct。
type Typ struct {
	Tag    string
	Elem   *Typ
	N      int
	Fields []TField
	Name   string
}

type TField struct {
	Name string
	T    *Typ
}

// 照抄 fieldalignment 的 basicSizes：只列显式给出的项，Int/Uint/Uintptr 落到 catch-all。
var basicSizes = map[string]int{
	"bool": 1, "int8": 1, "int16": 2, "int32": 4, "int64": 8,
	"uint8": 1, "uint16": 2, "uint32": 4, "uint64": 8,
	"float32": 4, "float64": 8, "complex64": 8, "complex128": 16,
}

func isPtrLike(tag string) bool {
	switch tag {
	case "ptr", "map", "chan", "func", "unsafeptr":
		return true
	}
	return false
}

// Struct 便于构造结构体类型。
func Struct(name string, fields ...TField) *Typ {
	return &Typ{Tag: "struct", Name: name, Fields: fields}
}

// Fld 构造一个字段。
func Fld(name, tag string) TField { return TField{name, &Typ{Tag: tag}} }

// Sizeof 对应 unsafe.Sizeof 的语义（不含被引用的内存）。
func Sizeof(t *Typ) int {
	if n, ok := basicSizes[t.Tag]; ok {
		return n
	}
	switch t.Tag {
	case "string":
		return Word * 2
	case "int", "uint", "uintptr":
		return Word // catch-all：源码里落到 return s.wordSize
	case "iface":
		return Word * 2
	case "slice":
		return Word * 3
	case "array":
		return t.N * Sizeof(t.Elem)
	case "struct":
		sz, _, _ := structWalk(t)
		return sz
	}
	if isPtrLike(t.Tag) {
		return Word
	}
	panic("未知类型标签 " + t.Tag)
}

// Alignof 对应 unsafe.Alignof；规范只保证它是**最小**对齐。
func Alignof(t *Typ) int {
	switch t.Tag {
	case "array":
		return Alignof(t.Elem)
	case "struct":
		m := 1
		for _, f := range t.Fields {
			if a := Alignof(f.T); a > m {
				m = a
			}
		}
		return m
	}
	a := Sizeof(t)
	if a < 1 {
		return 1
	}
	if a > MaxAlign {
		return MaxAlign
	}
	return a
}

// Ptrdata 是 GC 需要扫描的**前缀**字节数（最后一个含指针字段的结束偏移）。
func Ptrdata(t *Typ) int {
	switch t.Tag {
	case "string", "unsafeptr", "ptr", "chan", "map", "func", "slice":
		return Word
	case "iface":
		return 2 * Word
	case "array":
		if t.N == 0 {
			return 0
		}
		a := Ptrdata(t.Elem)
		if a == 0 {
			return 0
		}
		return (t.N-1)*Sizeof(t.Elem) + a
	case "struct":
		_, _, p := structWalk(t)
		return p
	}
	return 0
}

// Align 返回最小的 y >= x 且 y % a == 0（源码里的 align 函数）。
func Align(x, a int) int {
	y := x + a - 1
	return y - y%a
}

func structWalk(t *Typ) (size, maxAlign, ptrdata int) {
	nf := len(t.Fields)
	if nf == 0 {
		return 0, 1, 0
	}
	o, mx, p := 0, 1, 0
	for i, f := range t.Fields {
		a, sz := Alignof(f.T), Sizeof(f.T)
		if a > mx {
			mx = a
		}
		if i == nf-1 && sz == 0 && o != 0 {
			sz = 1 // 尾部零尺寸字段占 1 字节，避免和下一对象同址
		}
		o = Align(o, a)
		if fp := Ptrdata(f.T); fp != 0 {
			p = o + fp
		}
		o += sz
	}
	return Align(o, mx), mx, p
}

// StructLayout 返回 (尺寸, pointer bytes)。
func StructLayout(t *Typ) (int, int) {
	sz, _, p := structWalk(t)
	return sz, p
}

// ClassSize 对应分析器的 cap(bytes.Clone(make([]byte, size)))，-1 表示走大对象分配。
func ClassSize(size int) int {
	if size > MaxSmall {
		return -1
	}
	for _, c := range classToSize[1:] {
		if size <= c {
			return c
		}
	}
	return -1
}

type elem struct {
	index   int
	alignof int
	sizeof  int
	ptrdata int
}

// OptimalOrder 返回最优字段下标序列（比较规则逐条照抄分析器的 optimalOrder）。
// 分析器对各项全等的字段未规定顺序，这里补一个"按下标升序"的确定性收尾，
// 使输出可复现；它不影响尺寸与 pointer bytes 的结果。
func OptimalOrder(t *Typ) []int {
	nf := len(t.Fields)
	elems := make([]elem, nf)
	for i, f := range t.Fields {
		elems[i] = elem{i, Alignof(f.T), Sizeof(f.T), Ptrdata(f.T)}
	}
	sort.Slice(elems, func(i, j int) bool {
		ei, ej := &elems[i], &elems[j]
		zeroi, zeroj := ei.sizeof == 0, ej.sizeof == 0
		if zeroi != zeroj {
			return zeroi
		}
		if ei.alignof != ej.alignof {
			return ei.alignof > ej.alignof
		}
		noptrsi, noptrsj := ei.ptrdata == 0, ej.ptrdata == 0
		if noptrsi != noptrsj {
			return noptrsj
		}
		if !noptrsi {
			traili, trailj := ei.sizeof-ei.ptrdata, ej.sizeof-ej.ptrdata
			if traili != trailj {
				return traili < trailj
			}
		}
		if ei.sizeof != ej.sizeof {
			return ei.sizeof > ej.sizeof
		}
		return ei.index < ej.index
	})
	out := make([]int, nf)
	for i, e := range elems {
		out[i] = e.index
	}
	return out
}

// Reorder 按 OptimalOrder 重排字段，返回新类型。
func Reorder(t *Typ) *Typ {
	out := &Typ{Tag: "struct", Name: t.Name}
	for _, i := range OptimalOrder(t) {
		out.Fields = append(out.Fields, t.Fields[i])
	}
	return out
}

// Diagnostic 复刻分析器的诊断文本（判别顺序：先尺寸，后 pointer bytes）。
func Diagnostic(t *Typ) string {
	opt := Reorder(t)
	actualSize, actualPtrs := StructLayout(t)
	optimalSize, optimalPtrs := StructLayout(opt)
	if actualSize != optimalSize {
		ac, oc := ClassSize(actualSize), ClassSize(optimalSize)
		msg := fmt.Sprintf("%s has size %d", t.Name, actualSize)
		if ac == -1 {
			ac = actualSize
			msg += " (uses global allocator)"
		} else if ac != actualSize {
			msg += fmt.Sprintf(" (allocator size class %d)", ac)
		}
		msg += fmt.Sprintf(" but the optimal size is %d", optimalSize)
		if oc == -1 {
			oc = optimalSize
		} else if oc != optimalSize {
			msg += fmt.Sprintf(" (allocator size class %d)", oc)
		}
		if waste := ac - oc; waste > 0 {
			msg += fmt.Sprintf(" leading to a waste of %d bytes (%d%%)",
				waste, waste*100/ac)
		}
		return msg
	}
	if actualPtrs != optimalPtrs {
		return fmt.Sprintf("%s has %d leading bytes of pointer data but optimal value is %d",
			t.Name, actualPtrs, optimalPtrs)
	}
	return ""
}
