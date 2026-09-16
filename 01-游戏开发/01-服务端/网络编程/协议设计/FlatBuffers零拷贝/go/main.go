// FlatBuffers 线格式最小实现(Go 版):构建 + 就地(零拷贝)读取。
// 复现 flatcc binary-format 文档教学示例(FooBar):数据
// { meal:42(Orange), say:"hello", height:-8000 } 的官方参考字节。
// 布局规则与 python/main.py 相同:uoffset 字段在前、标量按 id 序,
// vtable 放缓冲区末尾(flatcc 风格,soffset 为负)。
package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"os"
)

const (
	banana int8 = -1
	orange int8 = 42
)

func ru16(b []byte, o int) uint16 { return binary.LittleEndian.Uint16(b[o:]) }
func ru32(b []byte, o int) uint32 { return binary.LittleEndian.Uint32(b[o:]) }
func ri32(b []byte, o int) int32  { return int32(ru32(b, o)) }
func ri16(b []byte, o int) int16  { return int16(ru16(b, o)) }

// buildFooBar:单 schema 构建器。fields: meal(i8, id0, 默认 banana),
// height(i16, id3, 默认 0);children: say(string, id2)。
// 值等于默认值的字段不存储(vtable 条目 0),后续标量前移补位。
func buildFooBar(ident []byte, meal int8, height int16, say []byte) []byte {
	// 1. table 布局:soffset(4) | say uoffset(4) | meal(1)+填充 | height(2)
	mealStored, heightStored := meal != banana, height != 0
	off := 4 + 4 // soffset + say uoffset
	mealOff, heightOff := 0, 0
	if mealStored {
		mealOff = off
		off++
	}
	if heightStored {
		if off%2 != 0 {
			off++ // 对齐填充
		}
		heightOff = off
		off += 2
	}
	tableSize := off
	// 2. block 地址规划:header | table | string | 填充 | vtable
	tablePos := 4 + len(ident)
	end := tablePos + tableSize
	for end%4 != 0 {
		end++
	}
	strPos := end
	end += 4 + len(say) + 1
	for end%4 != 0 {
		end++
	}
	vtPos := end
	vtSize := 12 // vtable_size, table_size, meal, density, say, height 共 6 条目
	// 3. 拼装
	buf := make([]byte, vtPos+vtSize)
	binary.LittleEndian.PutUint32(buf[0:], uint32(tablePos)) // root uoffset
	copy(buf[4:], ident)
	binary.LittleEndian.PutUint32(buf[tablePos:], uint32(tablePos-vtPos)) // soffset(负)
	binary.LittleEndian.PutUint32(buf[tablePos+4:], uint32(strPos-(tablePos+4)))
	if mealStored {
		buf[tablePos+mealOff] = byte(meal)
	}
	if heightStored {
		binary.LittleEndian.PutUint16(buf[tablePos+heightOff:], uint16(height))
	}
	binary.LittleEndian.PutUint32(buf[strPos:], uint32(len(say)))
	copy(buf[strPos+4:], say)
	binary.LittleEndian.PutUint16(buf[vtPos:], uint16(vtSize))
	binary.LittleEndian.PutUint16(buf[vtPos+2:], uint16(tableSize))
	binary.LittleEndian.PutUint16(buf[vtPos+4:], uint16(mealOff))
	binary.LittleEndian.PutUint16(buf[vtPos+6:], 0) // density: deprecated/缺失
	binary.LittleEndian.PutUint16(buf[vtPos+8:], 4)
	binary.LittleEndian.PutUint16(buf[vtPos+10:], uint16(heightOff))
	return buf
}

// fieldOffset:vtable 查字段。越界(旧数据无该字段)或条目 0 → 返回 0 表示缺失。
func fieldOffset(buf []byte, t, fid int) int {
	vt := t - int(ri32(buf, t)) // soffset 是『减去』
	vtsize := int(ru16(buf, vt))
	slot := 4 + fid*2 // 跳过 vtable 长度/表长度两个头条目
	if slot >= vtsize {
		return 0
	}
	return int(ru16(buf, vt+slot))
}

func getScalarI8(buf []byte, t, fid int, def int8) (int8, bool) {
	off := fieldOffset(buf, t, fid)
	if off == 0 {
		return def, false
	}
	return int8(buf[t+off]), true
}

func getScalarI16(buf []byte, t, fid int, def int16) (int16, bool) {
	off := fieldOffset(buf, t, fid)
	if off == 0 {
		return def, false
	}
	return ri16(buf, t+off), true
}

// getString:返回共享底层数组的切片(零拷贝:改 buf 即改字符串内容)。
func getString(buf []byte, t, fid int) []byte {
	off := fieldOffset(buf, t, fid)
	if off == 0 {
		return nil
	}
	s := t + off + int(ru32(buf, t+off)) // uoffset 加在自身存储地址上
	n := int(ru32(buf, s))
	return buf[s+4 : s+4+n]
}

var failures int

func check(label string, cond bool) {
	if !cond {
		failures++
		fmt.Printf("[FAIL] %s\n", label)
		return
	}
	fmt.Printf("[ok] %s\n", label)
}

func main() {
	// ---- 断言 1:逐字节复现 flatcc 参考缓冲区 ----
	buf := buildFooBar([]byte("NOOB"), orange, -8000, []byte("hello"))
	expected := append([]byte{},
		0x08, 0x00, 0x00, 0x00, 'N', 'O', 'O', 'B',
		0xe8, 0xff, 0xff, 0xff, 0x08, 0x00, 0x00, 0x00,
		0x2a, 0x00, 0xc0, 0xe0, 0x05, 0x00, 0x00, 0x00,
		'h', 'e', 'l', 'l', 'o', 0x00, 0x00, 0x00,
		0x0c, 0x00, 0x0c, 0x00, 0x08, 0x00, 0x00, 0x00,
		0x04, 0x00, 0x0a, 0x00)
	check("builder 复现 flatcc 参考字节", bytes.Equal(buf, expected))

	// ---- 断言 2:就地读取 ----
	t := int(ru32(buf, 0))
	check("root uoffset -> table @8", t == 8)
	check("soffset(负) -> vtable @0x20", t-int(ri32(buf, t)) == 0x20)
	meal, present := getScalarI8(buf, t, 0, banana)
	check("meal=42(Orange)", meal == orange && present)
	height, _ := getScalarI16(buf, t, 3, 0)
	check("height=-8000(int16 小端)", height == -8000)
	check("say=='hello'", string(getString(buf, t, 2)) == "hello")
	check("deprecated density 条目为 0(缺失)", fieldOffset(buf, t, 1) == 0)

	// ---- 断言 3:零拷贝 —— 切片直接落在原缓冲区上 ----
	view := getString(buf, t, 2)
	view[0] = 'j'
	check("零拷贝:经切片改写字节直落缓冲区", buf[0x18] == 'j')
	view[0] = 'h'

	// ---- 断言 4:缺省值不存储 + 前向兼容读取 ----
	buf2 := buildFooBar([]byte("NOOB"), banana, -8000, []byte("hi"))
	check("值==默认值时 vtable 条目置 0", fieldOffset(buf2, int(ru32(buf2, 0)), 0) == 0)
	meal2, present2 := getScalarI8(buf2, int(ru32(buf2, 0)), 0, banana)
	check("缺省读取返回默认 -1 且不占存储", meal2 == banana && !present2)
	_, present4 := getScalarI16(buf, t, 4, 7) // 新 schema 的 fid=4
	check("新代码读旧数据:越界字段返回默认", !present4)

	// ---- 断言 5:同布局两表共享同一 vtable(手工拼装单缓冲区) ----
	// header(4) | tableA(8) | tableB(8) | vtable(8):字段 f0:i16@4, f1:i8@6
	vtShared := []byte{0x08, 0x00, 0x08, 0x00, 0x04, 0x00, 0x06, 0x00}
	mkTable := func(so int32) []byte {
		b := make([]byte, 8)
		binary.LittleEndian.PutUint32(b[0:], uint32(so))
		binary.LittleEndian.PutUint16(b[4:], 11)
		b[6] = 2
		return b
	}
	buf3 := append(append(append([]byte{0x04, 0x00, 0x00, 0x00},
		mkTable(-16)...), mkTable(-8)...), vtShared...)
	tA, tB := 4, 12
	check("两表 soffset 均解析到同一 vtable @20",
		tA-int(ri32(buf3, tA)) == 20 && tB-int(ri32(buf3, tB)) == 20)
	check("vtable 在缓冲区中仅出现一份", bytes.Count(buf3, vtShared) == 1)

	// ---- 断言 6:vector(长度=元素个数)读取 ----
	// header|table(soffset+uoffset)|vtable|填充|vector: f0 -> [10,20,30]
	bufv := []byte{
		0x04, 0x00, 0x00, 0x00, // root uoffset
		0xf8, 0xff, 0xff, 0xff, // soffset = -8 -> vtable @12
		0x0c, 0x00, 0x00, 0x00, // uoffset = 12 -> vector 长度字段 @20
		0x06, 0x00, 0x08, 0x00, 0x04, 0x00, // vtable: size=6, table=8, f0=+4
		0x00, 0x00, // 对齐填充
		0x03, 0x00, 0x00, 0x00, // vector 元素个数 = 3
		0x0a, 0x00, 0x14, 0x00, 0x1e, 0x00, // [10, 20, 30]
	}
	vec := 4 + fieldOffset(bufv, 4, 0) + int(ru32(bufv, 8))
	check("vector 长度字段=元素个数 3", ru32(bufv, vec) == 3)
	items := []int16{ri16(bufv, vec+4), ri16(bufv, vec+6), ri16(bufv, vec+8)}
	check("vector 内容 [10,20,30]", items[0] == 10 && items[1] == 20 && items[2] == 30)

	if failures > 0 {
		fmt.Printf("\n%d 项断言失败\n", failures)
		os.Exit(1)
	}
	fmt.Println("\n全部断言通过")
}
