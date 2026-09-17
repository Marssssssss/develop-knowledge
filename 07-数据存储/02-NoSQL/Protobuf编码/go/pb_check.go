// pb_check.go — 自检:逐条核对 protobuf.dev 官方编码文档里的明文规则(Go 实现)
//
// 与 pb_varint.go / pb_wire.go / pb_codec.go 同属 package main。
//
// 运行: go run *.go
package main

import (
	"encoding/binary"
	"fmt"
	"math"
	"os"
)

func main() {
	fmt.Println("[1] Base 128 Varints(官方:7 位 payload,低位在前)")
	check("1 编码为 01", hex(EncodeVarint(1)) == "01")
	check("150 编码为 9601", hex(EncodeVarint(150)) == "9601")
	check("150 的首字节 MSB 置位(还有后续字节)", EncodeVarint(150)[0]&0x80 == 0x80)
	check("150 的次字节 MSB 清零(已到末尾)", EncodeVarint(150)[1]&0x80 == 0)
	e150 := EncodeVarint(150)
	check("低位在前:0x16 + (0x01 << 7) == 150",
		int64(e150[0]&0x7F)+(int64(e150[1]&0x7F)<<7) == 150, hex(e150))
	roundOK := true
	for _, v := range []uint64{0, 1, 127, 128, 300, 1 << 40} {
		got, pos, err := DecodeVarint(EncodeVarint(v), 0)
		if err != nil || got != v || pos != len(EncodeVarint(v)) {
			roundOK = false
		}
	}
	check("varint 往返:0/1/127/128/300/2^40", roundOK)
	check("1 字节上界 127 → 1 字节", VarintLength(127) == 1)
	check("128 需要 2 字节", VarintLength(128) == 2)
	check("2^63-1 → 9 字节", VarintLength((1<<63)-1) == 9)
	check("2^64-1 → 10 字节(官方上限)", VarintLength(^uint64(0)) == MaxVarintBytes)
	check("超过 10 字节的 varint 视为损坏",
		raises(func() error { _, _, e := DecodeVarint([]byte{0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff}, 0); return e }))
	check("截断的 varint 视为损坏",
		raises(func() error { _, _, e := DecodeVarint([]byte{0x96}, 0); return e }))

	fmt.Println("[2] tag = (field_number << 3) | wire_type(官方公式)")
	check("字段 1 + VARINT → 0x08", hex(mustRecord(1, WireVarint, []byte{0x00})) == "0800")
	check("字段 2 + LEN → 0x12",
		hex(mustRecord(2, WireLen, []byte{})) == "1200")
	t3, err := EncodeTag(3, WireI32)
	check("字段 3 + I32 → 0x1d", err == nil && hex(t3) == "1d")
	number, wire, pos, err := DecodeTag([]byte{0x08}, 0)
	check("解码 0x08 → 字段 1 / wire type 0 / 位置 1",
		err == nil && number == 1 && wire == WireVarint && pos == 1)
	raw, _, _ := DecodeVarint([]byte{0x08}, 0)
	check("低 3 位就是 wire type", raw&0x07 == WireVarint)
	check("右移 3 位就是字段号", raw>>3 == 1)
	check("字段号下界是 1", MinFieldNumber == 1)
	check("字段号上界是 536870911(官方 2^29-1)", MaxFieldNumber == 536870911)
	check("字段号 0 不合法", raises(func() error { return ValidateFieldNumber(0) }))
	check("字段号超上界不合法",
		raises(func() error { return ValidateFieldNumber(MaxFieldNumber + 1) }))
	check("字段号上界本身合法", ValidateFieldNumber(MaxFieldNumber) == nil)
	check("19000..19999 被官方保留",
		ReservedFieldLo == 19000 && ReservedFieldHi == 19999 &&
			raises(func() error { return ValidateFieldNumber(19000) }) &&
			raises(func() error { return ValidateFieldNumber(19999) }))
	check("19999 之外已可用", ValidateFieldNumber(20000) == nil)
	big, err := EncodeTag(MaxFieldNumber, WireLen)
	n2, w2, _, e2 := DecodeTag(big, 0)
	check("大字段号 tag 仍可往返", err == nil && e2 == nil && n2 == MaxFieldNumber && w2 == WireLen)
	check("tag 长度随字段号增长", TagSize(15, WireVarint) == 1 && TagSize(16, WireVarint) == 2)

	fmt.Println("[3] 官方示例:message Test1 { int32 a = 1; } a = 150")
	test1 := &MessageDef{Name: "Test1", Fields: []*FieldDef{{Number: 1, Name: "a", Kind: "int32"}}}
	body := mustEncode(test1, map[string]interface{}{"a": int64(150)}, nil)
	check("序列化结果是 08 96 01", hex(body) == "089601", hex(body))
	check("长度 3 字节", len(body) == 3)
	check("反解回 {a: 150}", fmt.Sprint(mustDecode(test1, body)) == "map[a:150]")
	recs, rerr := Records(body)
	check("可以在 TLV 上读出 1:VARINT 150",
		rerr == nil && len(recs) == 1 && recs[0].Number == 1 &&
			recs[0].Wire == WireVarint && WireName(recs[0].Wire) == "VARINT")

	fmt.Println("[4] 六种 wire type(官方表)")
	check("VARINT=0 I64=1 LEN=2 SGROUP=3 EGROUP=4 I32=5",
		WireVarint == 0 && WireI64 == 1 && WireLen == 2 &&
			WireSGroup == 3 && WireEGroup == 4 && WireI32 == 5)
	want := map[string]int{"int32": 0, "int64": 0, "uint32": 0, "sint32": 0, "sint64": 0,
		"bool": 0, "enum": 0, "fixed64": 1, "double": 1, "sfixed64": 1,
		"string": 2, "bytes": 2, "message": 2, "fixed32": 5, "float": 5, "sfixed32": 5}
	allOK := true
	for k, v := range want {
		if w, ok := kindToWire[k]; !ok || w != v {
			allOK = false
		}
	}
	check("16 种标量类型全部映射正确", allOK)
	check("wire type 名称可读", WireName(WireLen) == "LEN")

	fmt.Println("[5] 非 varint 数字:定长块")
	d2 := &FieldDef{Number: 1, Name: "d", Kind: "double"}
	f2 := &FieldDef{Number: 1, Name: "f", Kind: "float"}
	x64 := &FieldDef{Number: 1, Name: "x", Kind: "fixed64"}
	w, _ := d2.WireType()
	check("double 走 I64,8 字节", w == WireI64 && len(mustScalar(d2, 25.4)) == 8)
	w, _ = f2.WireType()
	check("float 走 I32,4 字节", w == WireI32 && len(mustScalar(f2, 25.4)) == 4)
	fixed, _ := toInt64(200)
	want64 := make([]byte, 8)
	binary.LittleEndian.PutUint64(want64, uint64(fixed))
	check("fixed64 是 8 字节小端", hex(mustScalar(x64, 200)) == hex(want64))
	wantD := make([]byte, 8)
	binary.LittleEndian.PutUint64(wantD, math.Float64bits(25.4))
	check("double 用 IEEE754 双精度", hex(mustScalar(d2, 25.4)) == hex(wantD))
	wantF := make([]byte, 4)
	binary.LittleEndian.PutUint32(wantF, math.Float32bits(float32(25.4)))
	check("float 用 IEEE754 单精度", hex(mustScalar(f2, 25.4)) == hex(wantF))
	m5 := &MessageDef{Name: "M", Fields: []*FieldDef{d2}}
	check("double 记录 = tag(1) + 8",
		len(mustEncode(m5, map[string]interface{}{"d": 25.4}, nil)) == 9)

	fmt.Println("[6] 负数:补码 vs ZigZag(官方对照表)")
	// 注意:uint64(-2) 作为常量表达式会编译报错(负数溢出),必须先用 int64 变量中转。
	neg := int64(-2)
	negBytes := EncodeVarint(uint64(neg))
	check("int 负数用补码且占满 10 字节",
		hex(negBytes) == "feffffffffffffffff01", hex(negBytes))
	check("-2 的长度是 10", SignedVarintLength(-2) == MaxVarintBytes)
	v2, _, _ := DecodeVarint(negBytes, 0)
	check("AsSigned64 还原 -2", AsSigned64(v2) == -2)
	table := []struct{ n int64; e uint64 }{
		{0, 0}, {-1, 1}, {1, 2}, {-2, 3}, {0x7FFFFFFF, 0xFFFFFFFE}, {-0x80000000, 0xFFFFFFFF}}
	zz := true
	for _, c := range table {
		if ZigZagEncode(c.n, 32) != c.e {
			zz = false
		}
	}
	check("ZigZag 官方对照表逐行吻合", zz)
	roundZZ := true
	for _, n := range []int64{-1, 1, -2, 2, 0x7FFFFFFF, -0x80000000, 0} {
		if ZigZagDecode(ZigZagEncode(n, 64)) != n {
			roundZZ = false
		}
	}
	check("ZigZag 往返 -1/1/-2/2/极值", roundZZ)
	check("官方示例 -500 → 999", ZigZagEncode(-500, 64) == 999 && ZigZagDecode(999) == -500)
	check("sint 负数只占 2 字节,远短于 int 的 10 字节",
		VarintLength(ZigZagEncode(-500, 64)) < 10)
	ms := &MessageDef{Name: "M", Fields: []*FieldDef{{Number: 1, Name: "a", Kind: "int32"},
		{Number: 2, Name: "b", Kind: "sint32"}}}
	la := len(mustEncode(ms, map[string]interface{}{"a": int64(-2)}, nil))
	lb := len(mustEncode(ms, map[string]interface{}{"b": int64(-2)}, nil))
	check("同一负数 sint32 编码明显更短", lb < la, fmt.Sprintf("%d vs %d", lb, la))

	fmt.Println("[7] Length-Delimited:长度前缀紧跟 tag")
	m7 := &MessageDef{Name: "M", Fields: []*FieldDef{{Number: 4, Name: "s", Kind: "string"}}}
	b7 := mustEncode(m7, map[string]interface{}{"s": "hello"}, nil)
	check("string 记录 = tag + len(varint) + 5 字节", len(b7) == 7 && b7[0] == 0x22, hex(b7))
	check("长度前缀就是 payload 长度", b7[1] == 5)
	check("string 往返", fmt.Sprint(mustDecode(m7, b7)) == "map[s:hello]")
	check("UTF-8 多字节字符按字节计长度",
		len(mustEncode(m7, map[string]interface{}{"s": "中文"}, nil)) == 8)
	inner := &MessageDef{Name: "Inner", Fields: []*FieldDef{{Number: 1, Name: "x", Kind: "int32"}}}
	outer := &MessageDef{Name: "Outer", Fields: []*FieldDef{
		{Number: 1, Name: "in", Kind: "message", MsgDef: inner}}}
	b8 := mustEncode(outer, map[string]interface{}{"in": map[string]interface{}{"x": int64(150)}}, nil)
	check("嵌套消息 = 外层 LEN 包住内层字节", hex(b8) == "0a03089601", hex(b8))
	check("嵌套消息往返", fmt.Sprint(mustDecode(outer, b8)) == "map[in:map[x:150]]")

	fmt.Println("[8] packed 重复字段 vs 非 packed")
	packed := &MessageDef{Name: "P", Fields: []*FieldDef{
		{Number: 5, Name: "nums", Kind: "int32", Repeated: true, Packed: true}}}
	unpacked := &MessageDef{Name: "U", Fields: []*FieldDef{
		{Number: 5, Name: "nums", Kind: "int32", Repeated: true, Packed: false}}}
	bp := mustEncode(packed, map[string]interface{}{"nums": ints(1, 2, 3)}, nil)
	bu := mustEncode(unpacked, map[string]interface{}{"nums": ints(1, 2, 3)}, nil)
	rp, _ := Records(bp)
	ru, _ := Records(bu)
	check("packed → 单条 LEN 记录", len(rp) == 1)
	check("非 packed → 三条记录", len(ru) == 3)
	check("packed 的 payload 是三个 varint 首尾相连",
		hex(bp) == hex(mustRecord(5, WireLen, []byte{0x01, 0x02, 0x03})))
	check("两种形式解码结果一致",
		fmt.Sprint(mustDecode(packed, bp)) == fmt.Sprint(mustDecode(unpacked, bp)) &&
			fmt.Sprint(mustDecode(packed, bp)) == "map[nums:[1 2 3]]")
	check("非 packed 字节也能被 packed 声明的解析器接受",
		fmt.Sprint(mustDecode(packed, bu)) == "map[nums:[1 2 3]]")
	official := mustRecord(5, WireLen, []byte{0x01, 0x02}) +
		mustRecord(4, WireLen, []byte("hello")) + mustRecord(5, WireLen, []byte{0x03})
	mix := &MessageDef{Name: "M", Fields: []*FieldDef{
		{Number: 4, Name: "s", Kind: "string"},
		{Number: 5, Name: "nums", Kind: "int32", Repeated: true, Packed: true}}}
	check("官方:5:{1 2} 4:{\"hello\"} 5:{3} 必须被接受",
		fmt.Sprint(mustDecode(mix, official)) == "map[nums:[1 2 3] s:hello]",
		fmt.Sprint(mustDecode(mix, official)))

	fmt.Println("[9] 未知字段必须被跳过(前向兼容)")
	old := &MessageDef{Name: "Old", Fields: []*FieldDef{{Number: 1, Name: "a", Kind: "int32"}}}
	newer := &MessageDef{Name: "New", Fields: []*FieldDef{
		{Number: 1, Name: "a", Kind: "int32"},
		{Number: 2, Name: "s", Kind: "string"},
		{Number: 3, Name: "d", Kind: "double"}}}
	nb := mustEncode(newer, map[string]interface{}{"a": int64(7), "s": "x", "d": 1.5}, nil)
	check("旧 schema 读新消息不报错且拿到已知字段",
		fmt.Sprint(mustDecode(old, nb)) == "map[a:7]", fmt.Sprint(mustDecode(old, nb)))
	sk, e1 := SkipField([]byte{0x01}, 0, WireVarint)
	sk2, e2b := SkipField(make([]byte, 8), 0, WireI64)
	sk3, e3 := SkipField(make([]byte, 4), 0, WireI32)
	sk4, e4 := SkipField([]byte{0x05, 'h', 'e', 'l', 'l', 'o'}, 0, WireLen)
	check("skip_field 对四种 wire type 都正确",
		e1 == nil && sk == 1 && e2b == nil && sk2 == 8 && e3 == nil && sk3 == 4 &&
			e4 == nil && sk4 == 6)

	fmt.Println("[10] Last One Wins 与嵌入消息合并(官方原文)")
	m10 := &MessageDef{Name: "M", Fields: []*FieldDef{{Number: 1, Name: "a", Kind: "int32"}}}
	twice := mustRecord(1, WireVarint, []byte{0x01}) + mustRecord(1, WireVarint, []byte{0x02})
	check("标量出现两次 → 取最后一个值", fmt.Sprint(mustDecode(m10, twice)) == "map[a:2]")
	deep := &MessageDef{Name: "I", Fields: []*FieldDef{
		{Number: 1, Name: "x", Kind: "int32"}, {Number: 2, Name: "y", Kind: "int32"}}}
	outer10 := &MessageDef{Name: "O", Fields: []*FieldDef{
		{Number: 1, Name: "m", Kind: "message", MsgDef: deep}}}
	merged := mustRecord(1, WireLen,
		mustEncode(deep, map[string]interface{}{"x": int64(1), "y": int64(2)}, nil)) +
		mustRecord(1, WireLen, mustEncode(deep, map[string]interface{}{"x": int64(9)}, nil))
	got := fmt.Sprint(mustDecode(outer10, merged))
	check("嵌入消息出现两次 → 递归合并(后者覆盖标量)",
		got == "map[m:map[x:9 y:2]]", got)
	rep := &MessageDef{Name: "M", Fields: []*FieldDef{
		{Number: 1, Name: "r", Kind: "int32", Repeated: true}}}
	mergedRep := MergeMessage(map[string]interface{}{"r": ints(1)},
		map[string]interface{}{"r": ints(2, 3)}, rep)
	check("merge_message 对 repeated 做拼接", fmt.Sprint(mergedRep["r"]) == "[1 2 3]")

	fmt.Println("[11] 字段顺序:字节不稳定,语义稳定(官方 Implications)")
	m11 := &MessageDef{Name: "M", Fields: []*FieldDef{
		{Number: 1, Name: "a", Kind: "int32"}, {Number: 2, Name: "b", Kind: "int32"}}}
	vals := map[string]interface{}{"a": int64(1), "b": int64(2)}
	fwd := mustEncode(m11, vals, nil)
	rev := mustEncode(m11, vals, []string{"b", "a"})
	check("交换字段顺序后字节不同", hex(fwd) != hex(rev), hex(fwd)+" vs "+hex(rev))
	check("但语义相同",
		fmt.Sprint(mustDecode(m11, fwd)) == fmt.Sprint(mustDecode(m11, rev)) &&
			fmt.Sprint(mustDecode(m11, rev)) == "map[a:1 b:2]")

	fmt.Println("[12] group:SGROUP/EGROUP 字段号必须配对")
	sg, _ := EncodeTag(8, WireSGroup)
	eg, _ := EncodeTag(8, WireEGroup)
	check("group 记录 payload 为空",
		hex(mustRecord(8, WireSGroup, nil)) == hex(sg) && hex(mustRecord(8, WireEGroup, nil)) == hex(eg))
	gbody := mustRecord(1, WireVarint, []byte{0x02})
	gblob := append(append([]byte{}, gbody...), mustRecord(8, WireEGroup, nil)...)
	payload, end, gerr := ParseGroup(gblob, 0, 8)
	check("配对时能取出 group 内容",
		gerr == nil && hex(payload) == hex(gbody) && end == len(gblob))
	bad := append(append([]byte{}, gbody...), mustRecord(7, WireEGroup, nil)...)
	check("遇到 7:EGROUP 而期望 8:EGROUP → mal-formed",
		raises(func() error { _, _, e := ParseGroup(bad, 0, 8); return e }))
	check("缺少 EGROUP 也是损坏",
		raises(func() error { _, _, e := ParseGroup(gbody, 0, 8); return e }))

	fmt.Println("")
	fmt.Printf("断言总数 %d,失败 %d\n", pass+fail, fail)
	if fail > 0 {
		os.Exit(1)
	}
	fmt.Println("全部通过")
}
