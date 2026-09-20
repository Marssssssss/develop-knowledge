// dalvik_selfcheck.go — 与 selfcheck_dalvik.py 同判据的 Go 自检入口。
package main

import "fmt"

var passed int

func check(cond bool, label string) {
	if !cond {
		panic("FAILED: " + label)
	}
	passed++
}

func selfcheck() {
	// 格式 ID 命名规则
	if n, r, k := parseFormatID("21t"); !(n == 2 && r == '1' && k == 't') {
		panic("FAILED: 21t 解析")
	}
	passed++
	if n, r, k := parseFormatID("35c"); !(n == 3 && r == '5' && k == 'c') {
		panic("FAILED: 35c 解析")
	}
	passed++
	if n, r, _ := parseFormatID("3rc"); !(n == 3 && r == 'r') {
		panic("FAILED: 3rc 的 r 标志")
	}
	passed++
	if n, _, _ := parseFormatID("51l"); n != 5 {
		panic("FAILED: 51l 单元数")
	}
	passed++
	if n, _, _ := parseFormatID("22cs"); n != 2 {
		panic("FAILED: 后缀 s 仍是 2 单元")
	}
	passed++

	// 类型代码字母位宽
	for _, pair := range []struct {
		l byte
		b int
	}{{'b', 8}, {'c', 16}, {'f', 16}, {'h', 16}, {'i', 32}, {'l', 64},
		{'m', 16}, {'n', 4}, {'s', 16}, {'t', 8}, {'x', 0}} {
		check(typeLetterBits[pair.l] == pair.b, "类型字母位宽")
	}

	// opcode 表
	check(opcodes[0x0E].name == "return-void" && opcodes[0x0E].fmt == "10x", "return-void")
	check(opcodes[0x1A].name == "const-string" && opcodes[0x1A].fmt == "21c", "const-string 21c")
	check(opcodes[0x28].name == "goto" && opcodes[0x28].fmt == "10t", "goto 10t")
	check(opcodes[0x2A].name == "goto/32" && opcodes[0x2A].fmt == "30t", "goto/32 30t")
	for i, name := range []string{"if-eq", "if-ne", "if-lt", "if-ge", "if-gt", "if-le"} {
		check(opcodes[uint8(0x32+i)].name == name && opcodes[uint8(0x32+i)].fmt == "22t", name)
	}
	for i, name := range []string{"if-eqz", "if-nez", "if-ltz", "if-gez", "if-gtz", "if-lez"} {
		check(opcodes[uint8(0x38+i)].name == name && opcodes[uint8(0x38+i)].fmt == "21t", name)
	}
	for i, name := range []string{"invoke-virtual", "invoke-super", "invoke-direct", "invoke-static", "invoke-interface"} {
		check(opcodes[uint8(0x6E+i)].name == name && opcodes[uint8(0x6E+i)].fmt == "35c", name)
		check(opcodes[uint8(0x74+i)].name == name+"/range" && opcodes[uint8(0x74+i)].fmt == "3rc", name+"/range")
	}
	check(opcodes[0xFA].fmt == "45cc" && opcodes[0xFB].fmt == "4rcc", "invoke-polymorphic 用 45cc/4rcc")
	// 每个 opcode 的格式都必须登记在格式表里
	for op, e := range opcodes {
		if _, ok := formatUnits[e.fmt]; !ok {
			panic(fmt.Sprintf("FAILED: opcode %#x 的格式 %s 未登记", op, e.fmt))
		}
		passed++
	}

	// 35c 与 3rc 的参数寄存器语义
	_, _, _, regs5 := decode35c(encode35c(0x6E, 5, 11, 0x1234, 1, 2, 3, 4))
	check(len(regs5) == 5 && regs5[4] == 11, "A=5 时第五个寄存器取自 G 位")
	_, _, _, regs3 := decode35c(encode35c(0x71, 3, 0, 0x1234, 7, 8, 9, 0))
	check(len(regs3) == 3 && regs3[0] == 7 && regs3[2] == 9, "A=3 时只用 C/D/E")
	_, _, _, regs0 := decode35c(encode35c(0x71, 0, 0, 0x1234, 0, 0, 0, 0))
	check(len(regs0) == 0, "A=0 时无参数寄存器")
	for a := 0; a <= 5; a++ {
		_, _, _, rr := decode35c(encode35c(0x6E, a, 0, 1, 0, 1, 2, 3))
		check(len(rr) == a, "A 决定参数寄存器个数")
	}

	// 3rc: NNNN = CCCC + AA - 1
	_, _, _, rrange := decode3rc(encode3rc(0x74, 4, 0x11, 16))
	check(len(rrange) == 4 && rrange[0] == 16 && rrange[3] == 19, "3rc 范围 C .. C+A-1")
	_, _, _, r1 := decode3rc(encode3rc(0x74, 1, 0x11, 5))
	check(len(r1) == 1 && r1[0] == 5, "A=1 时单寄存器")
	_, _, _, r0 := decode3rc(encode3rc(0x74, 0, 0x11, 5))
	check(len(r0) == 0, "A=0 时 3rc 是空范围")

	// payload 公式
	check(identPackedSwitch == 0x0100 && identSparseSwitch == 0x0200 && identFillArrayData == 0x0300,
		"三个 payload 的 ident")
	check(packedSwitchUnits(3) == 10, "packed-switch = size*2+4")
	check(sparseSwitchUnits(2) == 10, "sparse-switch = size*4+2")
	check(fillArrayDataUnits(4, 2) == 8, "fill-array-data = (size*width+1)/2+4")
	check(fillArrayDataUnits(3, 1) == 6, "fill-array-data 奇数字节的整除口径")

	// 分支偏移
	check(branchOffsetOK(1) && branchOffsetOK(-4) && !branchOffsetOK(0), "分支偏移不得为 0")

	// 符号扩展
	check(s8(0x7f) == 127 && s8(0x80) == -128, "s8 符号扩展")
	check(s16(0x7fff) == 32767 && s16(0x8000) == -32768, "s16 符号扩展")

	fmt.Printf("PASS %d 项断言全部通过\n", passed)
}
