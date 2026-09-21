package main

import "fmt"

var checks, failed int

func check(cond bool, label string) {
	checks++
	if !cond {
		failed++
		fmt.Println("FAIL:", label)
	}
}

func checkEq(got, want interface{}, label string) {
	checks++
	if fmt.Sprint(got) != fmt.Sprint(want) {
		failed++
		fmt.Printf("FAIL: %s (got=%v want=%v)\n", label, got, want)
	}
}

func main() {
	// 1) 常量
	checkEq(runeError, 0xFFFD, "RuneError=U+FFFD")
	checkEq(runeSelf, 0x80, "RuneSelf=0x80")
	checkEq(maxRune, 0x10FFFF, "MaxRune=U+10FFFF")
	checkEq(utfMax, 4, "UTFMax=4")

	// 2) first 表分区
	checkEq(first[0x41], uint8(as), "0x41 是 ASCII")
	checkEq(first[0x80], uint8(xx), "0x80 非法")
	checkEq(first[0xC2], uint8(s1), "0xC2 两字节")
	checkEq(first[0xE0], uint8(s2), "0xE0 走 acceptRanges[1]")
	checkEq(first[0xED], uint8(s4), "0xED 走 acceptRanges[2]（挡代理区）")
	checkEq(first[0xF0], uint8(s5), "0xF0 走 acceptRanges[3]")
	checkEq(first[0xF4], uint8(s7), "0xF4 走 acceptRanges[4]（挡超上限）")
	checkEq(first[0xF5], uint8(xx), "0xF5 非法")

	// 3) 三类被 acceptRanges 拦住的非法序列
	checkEq(decodeRune([]byte{0xE0, 0x80, 0x80}), Rune{runeError, 1}, "overlong E0 80 80")
	checkEq(decodeRune([]byte{0xED, 0xA0, 0x80}), Rune{runeError, 1}, "代理区 ED A0 80")
	checkEq(decodeRune([]byte{0xF4, 0x90, 0x80, 0x80}), Rune{runeError, 1}, "超上限 F4 90 80 80")
	checkEq(decodeRune([]byte{0xF0, 0x80, 0x80, 0x80}), Rune{runeError, 1}, "overlong F0 80 80 80")
	// 边界内侧合法
	checkEq(decodeRune([]byte{0xED, 0x9F, 0xBF}), Rune{0xD7FF, 3}, "ED 9F BF = U+D7FF")
	checkEq(decodeRune([]byte{0xF4, 0x8F, 0xBF, 0xBF}), Rune{0x10FFFF, 4}, "F4 8F BF BF = U+10FFFF")

	// 4) 与官方编码对拍（Go 自己的 encodeRune 应能还原标准编码）
	for _, cp := range []rune{0, 0x7F, 0x80, 0x7FF, 0x800, 0xFFFF, 0x10000, maxRune} {
		enc := encodeRune(cp)
		want := []byte(string(cp))
		checkEq(fmt.Sprint(enc), fmt.Sprint(want), fmt.Sprintf("编码 U+%04X 与标准库一致", cp))
		d := decodeRune(enc)
		checkEq(d, Rune{cp, len(enc)}, fmt.Sprintf("解码 U+%04X 往返一致", cp))
	}

	// 5) 非法输入与空输入的区别
	checkEq(decodeRune([]byte{0x80}), Rune{runeError, 1}, "非法单字节 size=1")
	checkEq(decodeRune(nil), Rune{runeError, 0}, "空输入 size=0")
	checkEq(decodeRune([]byte{0xE4, 0xB8}), Rune{runeError, 1}, "截断序列 size=1")

	// 6) RuneLen 的代理区
	checkEq(runeLen(-1), -1, "负数")
	checkEq(runeLen(0x7F), 1, "U+007F")
	checkEq(runeLen(0x80), 2, "U+0080")
	checkEq(runeLen(0x7FF), 2, "U+07FF")
	checkEq(runeLen(0x800), 3, "U+0800")
	checkEq(runeLen(0xD7FF), 3, "U+D7FF")
	checkEq(runeLen(0xD800), -1, "U+D800 代理区")
	checkEq(runeLen(0xDFFF), -1, "U+DFFF 代理区")
	checkEq(runeLen(0xE000), 3, "U+E000")
	checkEq(runeLen(maxRune), 4, "MaxRune")
	checkEq(runeLen(maxRune+1), -1, "超上限")

	// 7) 越界 rune 编码成 RuneError
	checkEq(fmt.Sprint(encodeRune(0xD800)), fmt.Sprint([]byte{0xEF, 0xBF, 0xBD}), "代理区→RuneError")
	checkEq(fmt.Sprint(encodeRune(0x110000)), fmt.Sprint([]byte{0xEF, 0xBF, 0xBD}), "超上限→RuneError")

	// 8) 字节数 vs rune 数
	world := []byte("世界")
	checkEq(len(world), 6, "len 是字节数 6")
	checkEq(runeCountInString(world), 2, "rune 数是 2")
	checkEq(runeCountInString([]byte{0x80, 0x80}), 2, "两个非法字节算 2 个 rune")
	checkEq(runeCountInString([]byte{0xE4, 0xB8}), 2, "截断序列算 2 个")

	// 9) RuneStart / Valid
	check(runeStart(0x41), "0x41 是首字节")
	check(!runeStart(0x80), "0x80 是续字节")
	check(valid([]byte("hello")), "合法 ASCII")
	check(!valid([]byte{0xFF}), "0xFF 非法")
	check(!valid([]byte{0xED, 0xA0, 0x80}), "代理区非法")

	fmt.Printf("checks=%d failed=%d\n", checks, failed)
	if failed > 0 {
		panic("selfcheck failed")
	}
}
