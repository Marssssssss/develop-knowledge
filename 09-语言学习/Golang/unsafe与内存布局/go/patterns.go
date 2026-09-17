package main

// unsafe.Pointer 的六种合法转换模式 —— 可执行的合法性校验器。
// 规则来自 unsafe 包文档：文档列出 (1)~(6) 六种合法模式，并给出若干
// "// INVALID:" 反例。本模块把一条"转换链"表示成步骤序列，逐条检查它是否
// 踩中文档点名的非法形式。

import "fmt"

// Step 是转换链上的一步。N 依 Kind 解释：
// add_offset / and_not 用字节数，unsafe_to_ptr 用 T2 的尺寸。
type Step struct {
	Kind    string
	N       int
	NilBase bool // 仅 uintptr_to_unsafe 使用：基址是否为 nil
}

// CheckChain 返回违规原因列表；空列表表示这条链合法。
func CheckChain(objSize, t1Size int, steps []Step) []string {
	bad := []string{}   // 非 nil 切片，便于外部用 %#v 直接比对
	origin := "" // "" | "expr" | "temp"
	stored := false
	offset := 0
	hasPtr := false
	for _, s := range steps {
		switch s.Kind {
		case "ptr_to_unsafe":
			hasPtr, origin, offset = true, "expr", 0
		case "unsafe_to_ptr":
			if t1Size > 0 && s.N > t1Size {
				bad = append(bad, fmt.Sprintf(
					"模式 1 要求 T2 不大于 T1（T2=%d > T1=%d）", s.N, t1Size))
			}
			if !hasPtr {
				bad = append(bad, "unsafe.Pointer 必须先由某个指针转来")
			}
		case "unsafe_to_uintptr":
			if hasPtr {
				origin = "expr"
			} else {
				origin = ""
			}
			hasPtr, stored = false, false
		case "add_offset":
			if origin == "" {
				bad = append(bad, "uintptr 不是来自同一表达式内的 Pointer 转换")
			}
			offset += s.N
			if objSize > 0 && (offset < 0 || offset >= objSize) {
				bad = append(bad, fmt.Sprintf(
					"结果必须仍指向原对象内部（offset=%d, size=%d）", offset, objSize))
			}
		case "and_not":
			offset &^= s.N
		case "store_temp":
			if origin == "expr" {
				stored, origin = true, "temp"
			}
		case "uintptr_to_unsafe":
			if origin == "temp" || stored {
				bad = append(bad, "uintptr 存进过变量再转回 Pointer")
			}
			if s.NilBase {
				bad = append(bad, "Pointer 必须指向已分配对象，不能是 nil")
			}
			origin, stored, hasPtr = "", false, true
		case "syscall_arg":
			if stored {
				bad = append(bad, "系统调用实参必须就地转换，不能先存变量")
			}
			origin = ""
		case "print_only":
			origin = ""
		case "header_declared":
			bad = append(bad, "不得声明 reflect.SliceHeader/StringHeader 普通变量")
		case "header_of_real":
			// 合法起点：指向真实 string/slice 的 header 指针
		case "reflect_ptr_field":
			origin = "expr"
		default:
			panic("未知步骤 " + s.Kind)
		}
	}
	return bad
}

// 六个正例（对应文档 (1)~(6)）
var (
	p1Float64Bits = []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_ptr", N: 8}}
	p2Print       = []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_uintptr"},
		{Kind: "print_only"}}
	p3FieldAddr = []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_uintptr"},
		{Kind: "add_offset", N: 8}, {Kind: "uintptr_to_unsafe"}}
	p3Round = []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_uintptr"},
		{Kind: "add_offset", N: 100}, {Kind: "and_not", N: 63},
		{Kind: "uintptr_to_unsafe"}}
	p4Syscall = []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_uintptr"},
		{Kind: "syscall_arg"}}
	p5Reflect = []Step{{Kind: "reflect_ptr_field"}, {Kind: "uintptr_to_unsafe"}}
	p6Header  = []Step{{Kind: "header_of_real"}, {Kind: "unsafe_to_uintptr"}}
)

// 文档点名的 INVALID 反例
var (
	invalidTempBeforePtr = []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_uintptr"},
		{Kind: "store_temp"}, {Kind: "add_offset", N: 8}, {Kind: "uintptr_to_unsafe"}}
	invalidPastEnd = []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_uintptr"},
		{Kind: "add_offset", N: 24}, {Kind: "uintptr_to_unsafe"}}
	invalidNil = []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_uintptr"},
		{Kind: "add_offset", N: 8}, {Kind: "uintptr_to_unsafe", NilBase: true}}
	invalidSyscallTemp = []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_uintptr"},
		{Kind: "store_temp"}, {Kind: "syscall_arg"}}
	invalidReflectTemp = []Step{{Kind: "reflect_ptr_field"}, {Kind: "store_temp"},
		{Kind: "uintptr_to_unsafe"}}
	invalidHeaderDecl = []Step{{Kind: "header_declared"}, {Kind: "unsafe_to_uintptr"}}
	invalidSmallerT1  = []Step{{Kind: "ptr_to_unsafe"}, {Kind: "unsafe_to_ptr", N: 16}}
)

type namedChain struct {
	name    string
	objSize int
	t1Size  int
	steps   []Step
}

var validCases = []namedChain{
	{"P1 math.Float64bits", 8, 8, p1Float64Bits},
	{"P2 打印地址", 8, 8, p2Print},
	{"P3 &s.f", 24, 8, p3FieldAddr},
	{"P3 &^ 取整", 256, 8, p3Round},
	{"P4 Syscall 实参", 8, 8, p4Syscall},
	{"P5 reflect.Value.Pointer", 8, 8, p5Reflect},
	{"P6 真实 header", 16, 8, p6Header},
}
