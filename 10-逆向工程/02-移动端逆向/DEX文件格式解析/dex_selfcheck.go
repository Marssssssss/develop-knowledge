// dex_selfcheck.go — 与 selfcheck_dex.py 同判据的 Go 自检入口。
package main

import (
	"encoding/binary"
	"fmt"
)

// ---------- 自检 ----------

var passed int

func check(cond bool, label string) {
	if !cond {
		panic("FAILED: " + label)
	}
	passed++
}

func main() {
	// 官方 LEB128 示例表: 编码 / sleb128 / uleb128 / uleb128p1
	vectors := []struct {
		enc        []byte
		sleb, uleb int32
		p1         int32
	}{
		{[]byte{0x00}, 0, 0, -1},
		{[]byte{0x01}, 1, 1, 0},
		{[]byte{0x7f}, -1, 127, 126},
		{[]byte{0x80, 0x7f}, -128, 16256, 16255},
	}
	for _, v := range vectors {
		got, _ := sleb128Decode(v.enc)
		check(got == v.sleb, "sleb128 解码")
		ugot, _ := uleb128Decode(v.enc)
		check(int32(ugot) == v.uleb, "uleb128 解码")
		pgot, _ := uleb128p1Decode(v.enc)
		check(pgot == v.p1, "uleb128p1 解码")
		check(bytesEqual(uleb128Encode(uint32(v.uleb)), v.enc), "uleb128 编码回写")
		check(bytesEqual(sleb128Encode(v.sleb), v.enc), "sleb128 编码回写")
	}
	check(len(uleb128Encode(0xffffffff)) == 5, "uleb128(0xffffffff) 占满 5 字节")
	check(bytesEqual(uleb128p1Encode(-1), []byte{0x00}), "uleb128p1(-1) 是单字节 0x00")

	// MUTF-8
	check(bytesEqual(mutf8Encode("\x00"), []byte{0xc0, 0x80, 0x00}), "U+0000 编成 c0 80")
	check(utf16Size("\x00") == 1, "U+0000 的 utf16_size 是 1")
	check(len(mutf8Encode("\U0001F600")) == 7, "增补平面字符 6 字节 + 终止字节")
	check(utf16Size("\U0001F600") == 2, "增补平面字符 utf16_size 是 2")
	check(len(mutf8Decode(mutf8Encode("a\x00b"))) == 3, "串内 U+0000 不截断解码")

	// 常量
	check(endianConstant == 0x12345678, "ENDIAN_CONSTANT")
	check(noIndex == uint32(0xffffffff), "NO_INDEX")
	check(headerSizeV40 == 0x70 && headerSizeV41 == 0x78, "header_size v40/v41")

	// padding 规则
	check(codeItemPadding(0, 3) == 0, "tries 为 0 时无 padding")
	check(codeItemPadding(1, 2) == 0, "insns 偶数时无 padding")
	check(codeItemPadding(1, 3) == 1, "tries 非零且 insns 奇数时有 padding")
	check(codeItemUnits(1, 3) == 12, "含 padding 的 code_item 共 12 单元")

	// 解析一段最小 header(仅校验字段落位, 内容与 Python 侧构造一致)
	buf := make([]byte, headerSizeV40)
	copy(buf, dexFileMagic[:])
	binary.LittleEndian.PutUint32(buf[32:36], 0x70) // file_size
	binary.LittleEndian.PutUint32(buf[36:40], headerSizeV40)
	binary.LittleEndian.PutUint32(buf[40:44], endianConstant)
	binary.LittleEndian.PutUint32(buf[52:56], 0x40)   // map_off
	binary.LittleEndian.PutUint32(buf[56:60], 3)      // string_ids_size
	binary.LittleEndian.PutUint32(buf[60:64], 0x70)   // string_ids_off
	binary.LittleEndian.PutUint32(buf[64:68], 2)      // type_ids_size
	binary.LittleEndian.PutUint32(buf[68:72], 0x7c)   // type_ids_off
	binary.LittleEndian.PutUint32(buf[88:92], 1)      // method_ids_size
	binary.LittleEndian.PutUint32(buf[96:100], 1)     // class_defs_size
	h, err := parseHeader(buf)
	check(err == nil, "header 解析无错")
	check(h.fileSize == 0x70 && h.headerSize == headerSizeV40, "file_size / header_size 落位")
	check(h.endianTag == endianConstant, "endian_tag 落位")
	check(h.mapOff == 0x40 && h.stringIDsSize == 3 && h.stringIDsOff == 0x70, "map_off 与 string_ids 落位")
	check(h.typeIDsSize == 2 && h.typeIDsOff == 0x7c, "type_ids 落位")
	check(h.methodIDsSize == 1 && h.classDefsSize == 1, "method_ids / class_defs 落位")

	bad := make([]byte, headerSizeV40)
	copy(bad, buf)
	bad[0] = 'X'
	_, err = parseHeader(bad)
	check(err != nil, "magic 被改写后解析报错")

	fmt.Printf("PASS %d 项断言全部通过\n", passed)
}

func bytesEqual(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
