package main

import "fmt"

var scCount int
var scFails []string

func ok(cond bool, msg string) {
	scCount++
	if !cond {
		scFails = append(scFails, msg)
	}
}

func eq(got, want int64, msg string) { ok(got == want, msg) }
func eqi(got, want int, msg string)  { ok(got == want, msg) }

func expectFrameErr(err error, msg string) {
	scCount++
	if err == nil {
		scFails = append(scFails, msg+" -> 没有返回错误")
		return
	}
	if errKind(err) != frameEncodingError {
		scFails = append(scFails, msg+" -> kind "+errKind(err))
	}
}

func expectProtoErr(err error, msg string) {
	scCount++
	if err == nil {
		scFails = append(scFails, msg+" -> 没有返回错误")
		return
	}
	if errKind(err) != protocolViolation {
		scFails = append(scFails, msg+" -> kind "+errKind(err))
	}
}

func selfcheck() (int, []string) {
	scCount, scFails = 0, nil

	eq(max1, 63, "MAX_1")
	eq(max2, 16383, "MAX_2")
	eq(max4, 1073741823, "MAX_4")
	eq(max8, 4611686018427387903, "MAX_8")

	// RFC 9000 附录 A.1
	cases := []struct {
		raw []byte
		v   int64
	}{
		{[]byte{0x25}, 37},
		{[]byte{0x7b, 0xbd}, 15293},
		{[]byte{0x9d, 0x7f, 0x3e, 0x7d}, 494878333},
		{[]byte{0xc2, 0x19, 0x7c, 0x5e, 0xff, 0x14, 0xe8, 0x8c}, 151288809941952652},
		{[]byte{0x40, 0x25}, 37},
	}
	for _, c := range cases {
		v, used, err := VarintDecode(c.raw, 0)
		if err != nil {
			scFails = append(scFails, "decode error")
			continue
		}
		eq(v, c.v, "解码")
		eqi(used, len(c.raw), "消耗字节")
	}

	// 往返与最短长度
	for _, v := range []int64{0, 1, 37, 63, 64, 15293, 16383, 16384, max4, max4 + 1, max8} {
		raw, err := VarintEncode(v, 0)
		if err != nil {
			scFails = append(scFails, "encode error")
			continue
		}
		back, _, err := VarintDecode(raw, 0)
		if err != nil {
			scFails = append(scFails, "decode error")
			continue
		}
		eq(back, v, "往返")
		n, _ := VarintLenFor(v)
		eqi(len(raw), n, "最短长度")
	}
	eqi(mustLen(63), 1, "63 -> 1")
	eqi(mustLen(64), 2, "64 -> 2")
	eqi(mustLen(16384), 4, "16384 -> 4")
	eqi(mustLen(max4+1), 8, "2^30 -> 8")

	// 显式更长的长度
	raw, _ := VarintEncode(37, 2)
	ok(len(raw) == 2 && raw[0] == 0x40 && raw[1] == 0x25, "37 写成 2 字节 = 0x4025")
	_, err := VarintEncode(64, 1)
	expectFrameErr(err, "64 装不进 1 字节")
	_, err = VarintEncode(max8+1, 0)
	expectFrameErr(err, "超出 2^62-1")

	// 解码截断
	_, _, err = VarintDecode([]byte{}, 0)
	expectFrameErr(err, "空输入")
	_, _, err = VarintDecode([]byte{0x40}, 0)
	expectFrameErr(err, "2 字节前缀只有 1 字节")
	_, _, err = VarintDecode([]byte{0x80, 0x00}, 0)
	expectFrameErr(err, "4 字节前缀只有 2 字节")

	// 最短编码判定
	ok(IsShortestEncoding([]byte{0x25}), "0x25 最短")
	ok(!IsShortestEncoding([]byte{0x40, 0x25}), "0x4025 非最短(成对用例)")

	// ACK Range
	rs, err := DecodeAckRanges(10, 2, [][2]int64{{1, 1}})
	if err == nil {
		eq(rs[0].Lo, 8, "[8,10] 下界")
		eq(rs[0].Hi, 10, "[8,10] 上界")
		eq(rs[1].Lo, 4, "[4,5] 下界")
		eq(rs[1].Hi, 5, "[4,5] 上界")
	}
	rs, err = DecodeAckRanges(10, 2, [][2]int64{{0, 1}})
	if err == nil {
		eq(rs[1].Lo, 5, "gap=0 -> 下一区间 [5,6]")
		eq(rs[1].Hi, 6, "[5,6] 上界")
	}
	eq(GapPacketCount(0), 1, "gap=0 -> 1 个未确认包")
	eq(GapPacketCount(1), 2, "gap=1 -> 2 个未确认包")
	_, err = DecodeAckRanges(2, 0, [][2]int64{{1, 0}})
	expectFrameErr(err, "largest = 2-1-2 = -1")
	_, err = DecodeAckRanges(0, 1, nil)
	expectFrameErr(err, "smallest = 0-1 = -1")

	// 帧解析
	_, err = ParseFrames([]byte{})
	expectProtoErr(err, "空负载")
	fs, err := ParseFrames([]byte{0x00, 0x00, 0x00})
	if err == nil {
		eqi(len(fs), 3, "3 个 PADDING")
	}
	fs, err = ParseFrames([]byte{0x01, 0x00})
	if err == nil {
		eqi(len(fs), 2, "PING + PADDING")
		eq(fs[0].Type, int64(ping), "第一帧 PING")
	}
	_, err = ParseFrames([]byte{0x40, 0x00})
	expectFrameErr(err, "PADDING 用了 2 字节编码")
	_, err = ParseFrames([]byte{0x2a})
	expectFrameErr(err, "未知帧类型")

	return scCount, scFails
}

func mustLen(v int64) int {
	n, _ := VarintLenFor(v)
	return n
}

func main() {
	fmt.Println("== Go 侧自检 ==")
	n, fails := selfcheck()
	fmt.Printf("  assertions=%d  fails=%d\n", n, len(fails))
	for _, f := range fails {
		fmt.Println("  FAIL:", f)
	}

	fmt.Println("\n== 附录 A.1 的四个例子 ==")
	for _, raw := range [][]byte{
		{0x25}, {0x7b, 0xbd}, {0x9d, 0x7f, 0x3e, 0x7d},
		{0xc2, 0x19, 0x7c, 0x5e, 0xff, 0x14, 0xe8, 0x8c}, {0x40, 0x25},
	} {
		v, used, _ := VarintDecode(raw, 0)
		fmt.Printf("  %-20s -> %-22d (占 %d 字节) 最短=%v\n",
			hexStr(raw), v, used, IsShortestEncoding(raw))
	}

	fmt.Println("\n== ACK Range 解码 ==")
	rs, _ := DecodeAckRanges(20, 1, [][2]int64{{1, 0}, {2, 3}})
	for _, r := range rs {
		fmt.Printf("  [%d,%d]\n", r.Lo, r.Hi)
	}
}

func hexStr(b []byte) string {
	s := "0x"
	for _, x := range b {
		s += fmt.Sprintf("%02x", x)
	}
	return s
}
