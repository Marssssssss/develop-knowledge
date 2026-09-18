// Discoverer demo 自检入口：go run . （自检失败即 panic）
package main

import "fmt"

func binMsg(cmd byte, body []byte) []byte {
	magic := []byte{0xff, 0x00, 0x00, 0x00}
	out := append([]byte{}, magic...)
	out = append(out, cmd, byte(len(body)>>8), byte(len(body)))
	return append(out, body...)
}

func familyA() [][]byte {
	return [][]byte{binMsg(0x01, []byte("HELLO")), binMsg(0x01, []byte("BYE")),
		binMsg(0x01, []byte("USERNAME")), binMsg(0x03, []byte("GETTING")),
		binMsg(0x03, []byte("DELETE"))}
}

func familyB() [][]byte {
	mk := func(off int, body string) []byte {
		out := []byte{0xee, byte(off >> 8), byte(off)}
		return append(out, []byte(body)...)
	}
	return [][]byte{mk(11, "ABC DEF GHI JKL"), mk(17, "ABCDEF DEFGHI GHI JKLMNOP")}
}

func check(label string, cond bool, detail string) {
	if !cond {
		panic("FAIL " + label + " " + detail)
	}
	fmt.Printf("  ok  %-50s %s\n", label, detail)
}

func main() {
	a := familyA()
	t := tokenize(a[0])
	check("magic → 4 个 binary token", len(t) == 8 && t[0].cls == clsB && t[3].cls == clsB,
		tokenPattern("", t))
	check("body 'HELLO' → 1 个 text token", t[7].cls == clsT && string(t[7].val) == "HELLO", "")
	odd := [][]byte{binMsg(0x01, []byte("OK"))}
	check("'OK' 2 字节 < textMin → 9 个 binary token",
		len(tokenize(odd[0])) == 9, tokenPattern("", tokenize(odd[0])))

	fmt_ := inferFormat(a)
	check("magic 4 字节全常量", fmt_[0].isConst && fmt_[1].isConst &&
		fmt_[2].isConst && fmt_[3].isConst, "")
	check("cmd 是变量", !fmt_[4].isConst, "")
	check("token[5:7] 判为 length", fmt_[5].sem == "length" && fmt_[6].sem == "length", "")
	fb := inferFormat(familyB())
	check("家族 B token[1:3] 判为 offset", fb[1].sem == "offset" && fb[2].sem == "offset", "")
	check("offset 未被误判为 length",
		fb[1].sem != "length" && fb[2].sem != "length", "")

	k := findFD(a)
	check("FD token 落在下标 4", k == 4, fmt.Sprintf("k=%d", k))
	check("同一子簇内已无 FD", findFD(a[:3]) == -1, "")

	ok, mm := canMerge(inferFormat(a[:3]), inferFormat(odd))
	check("'OK' 簇可并入主流簇", ok, fmt.Sprintf("mismatch=%d", mm))
	check("失配恰为 1 处", mm == 1, "")
	ok2, _ := canMerge(inferFormat(a[:3]), inferFormat(familyB()))
	check("异构格式不可合并", !ok2, "")
	fmt.Println("Discoverer(Go): ALL ASSERTIONS PASSED")
}
