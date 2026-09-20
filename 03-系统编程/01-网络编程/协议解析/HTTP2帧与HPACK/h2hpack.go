// HTTP/2 二进制分帧 + HPACK —— Go 版模型
//
// 与 h2hpack_model.py 同题。Go 侧重点放在位移与 []byte 切片边界：
// 24 位 Length、31 位 Stream ID、N 位前缀整数都要求显式掩码，
// 恰好是 Python 里 int 任意精度掩盖掉的那部分。
package main

import (
	"errors"
	"fmt"
)

// 帧类型
const (
	Data         = 0x0
	Headers      = 0x1
	Priority     = 0x2
	RstStream    = 0x3
	Settings     = 0x4
	PushPromise  = 0x5
	Ping         = 0x6
	GoAway       = 0x7
	WindowUpdate = 0x8
	Continuation = 0x9
)

const (
	HeaderLen       = 9
	DefaultMaxFrame = 1 << 14
	MinMaxFrame     = 1 << 14
	MaxMaxFrame     = (1 << 24) - 1
)

var frameNames = map[int]string{
	Data: "DATA", Headers: "HEADERS", Priority: "PRIORITY",
	RstStream: "RST_STREAM", Settings: "SETTINGS",
	PushPromise: "PUSH_PROMISE", Ping: "PING", GoAway: "GOAWAY",
	WindowUpdate: "WINDOW_UPDATE", Continuation: "CONTINUATION",
}

// Frame 是解出来的一帧
type Frame struct {
	Length   int
	Type     int
	Flags    int
	Reserved int
	StreamID int
	Payload  []byte
}

func (f *Frame) Name() string {
	if n, ok := frameNames[f.Type]; ok {
		return n
	}
	return fmt.Sprintf("UNKNOWN(0x%02x)", f.Type)
}

// EncodeFrame 组帧：Length(24) Type(8) Flags(8) R(1)|StreamID(31) Payload
func EncodeFrame(ftype, flags, streamID int, payload []byte, reserved int) ([]byte, error) {
	if len(payload) > MaxMaxFrame {
		return nil, errors.New("payload 超过 2^24-1")
	}
	if reserved != 0 && reserved != 1 {
		return nil, errors.New("reserved 只能是 1 位")
	}
	if streamID < 0 || streamID > 0x7FFFFFFF {
		return nil, errors.New("stream id 必须落在 31 位")
	}
	out := make([]byte, 0, HeaderLen+len(payload))
	out = append(out, byte(len(payload)>>16), byte(len(payload)>>8), byte(len(payload)))
	out = append(out, byte(ftype), byte(flags))
	word := uint32(reserved)<<31 | uint32(streamID)
	out = append(out, byte(word>>24), byte(word>>16), byte(word>>8), byte(word))
	return append(out, payload...), nil
}

// DecodeFrame 解一帧，返回帧与消费后的偏移
func DecodeFrame(buf []byte, pos int) (*Frame, int, error) {
	if len(buf)-pos < HeaderLen {
		return nil, pos, errors.New("不足 9 字节帧头")
	}
	length := int(buf[pos])<<16 | int(buf[pos+1])<<8 | int(buf[pos+2])
	ftype := int(buf[pos+3])
	flags := int(buf[pos+4])
	word := uint32(buf[pos+5])<<24 | uint32(buf[pos+6])<<16 |
		uint32(buf[pos+7])<<8 | uint32(buf[pos+8])
	reserved := int((word >> 31) & 1)
	streamID := int(word & 0x7FFFFFFF)
	end := pos + HeaderLen + length
	if len(buf) < end {
		return nil, pos, errors.New("payload 不完整")
	}
	return &Frame{length, ftype, flags, reserved, streamID,
		buf[pos+HeaderLen : end]}, end, nil
}

// ---------------------------------------------------------------- HPACK

const (
	Indexed      = 0x80
	Incremental  = 0x40
	SizeUpdate   = 0x20
	NeverIndexed = 0x10
	NoIndexing   = 0x00
)

// EncodeInt N 位前缀整数编码：strictly less than 2^N-1 走单字节
func EncodeInt(value, n, prefix int) []byte {
	limit := (1 << n) - 1
	if value < limit {
		return []byte{byte(prefix | value)}
	}
	out := []byte{byte(prefix | limit)}
	v := value - limit
	for v >= 128 {
		out = append(out, byte(v&0x7f|0x80))
		v >>= 7
	}
	return append(out, byte(v))
}

// DecodeInt 返回 (value, newpos)
func DecodeInt(buf []byte, pos, n int) (int, int, error) {
	limit := (1 << n) - 1
	if pos >= len(buf) {
		return 0, pos, errors.New("整数编码越界")
	}
	v := int(buf[pos]) & limit
	pos++
	if v < limit {
		return v, pos, nil
	}
	m := uint(0)
	for {
		if pos >= len(buf) {
			return 0, pos, errors.New("续字节越界")
		}
		b := buf[pos]
		pos++
		v += int(b&0x7f) << m
		if b&0x80 == 0 {
			return v, pos, nil
		}
		m += 7
	}
}

// EncodeStr H 位 + 7 位前缀长度
func EncodeStr(s []byte, huffman bool) []byte {
	h := 0
	if huffman {
		h = 0x80
	}
	return append(EncodeInt(len(s), 7, h), s...)
}

// DecodeStr 返回 (bytes, huffman, newpos)
func DecodeStr(buf []byte, pos int) ([]byte, bool, int, error) {
	if pos >= len(buf) {
		return nil, false, pos, errors.New("字符串越界")
	}
	h := buf[pos]&0x80 != 0
	n, p, err := DecodeInt(buf, pos, 7)
	if err != nil {
		return nil, h, pos, err
	}
	if len(buf) < p+n {
		return nil, h, pos, errors.New("字符串长度越界")
	}
	return buf[p : p+n], h, p + n, nil
}

// ---------------------------------------------------------------- 自检

var nAssert, nFail int

func check(label string, cond bool, detail string) {
	nAssert++
	if cond {
		fmt.Printf("ok   %-52s %s\n", label, detail)
		return
	}
	nFail++
	fmt.Printf("FAIL %-52s %s\n", label, detail)
}

func main() {
	fmt.Println("== 帧格式 ==")
	f, _ := EncodeFrame(Headers, 0x04, 1, []byte("abc"), 0)
	check("帧总长 = 9 + payload", len(f) == 12, fmt.Sprintf("len=%d", len(f)))
	fr, pos, _ := DecodeFrame(f, 0)
	check("Length 不含帧头", fr.Length == 3 && pos == 12, fmt.Sprintf("length=%d", fr.Length))
	check("Type/Flags/StreamID 还原", fr.Type == Headers && fr.Flags == 0x04 && fr.StreamID == 1, "")

	f3, _ := EncodeFrame(Data, 0x01, 7, []byte("x"), 1)
	fr3, _, _ := DecodeFrame(f3, 0)
	check("保留位=1 收到时忽略，流 ID 仍正确",
		fr3.Reserved == 1 && fr3.StreamID == 7, "")
	f5, _ := EncodeFrame(0xFE, 0, 3, []byte("junk"), 0)
	fr5, pos5, _ := DecodeFrame(f5, 0)
	check("未知类型可解且长度正确", fr5.Name()[:7] == "UNKNOWN" && fr5.Length == 4 && pos5 == 13,
		fr5.Name())
	check("默认上限 16384", DefaultMaxFrame == 16384 && MinMaxFrame == 16384 &&
		MaxMaxFrame == 16777215, "")

	fmt.Println("\n== HPACK 整数边界 ==")
	check("126 单字节 0xFE", string(EncodeInt(126, 7, Indexed)) == "\xfe", "")
	check("127 走双字节 0xFF 0x00", string(EncodeInt(127, 7, Indexed)) == "\xff\x00",
		fmt.Sprintf("%x", EncodeInt(127, 7, Indexed)))
	check("128 → 0xFF 0x01", string(EncodeInt(128, 7, Indexed)) == "\xff\x01", "")
	for _, v := range []int{0, 1, 10, 126, 127, 128, 1337, 65535, 1 << 20} {
		e := EncodeInt(v, 7, Indexed)
		got, np, _ := DecodeInt(append(append([]byte{}, e...), 0), 0, 7)
		check(fmt.Sprintf("往返一致 v=%d", v), got == v && np == len(e), "")
	}
	check("5 位前缀时 31 走双字节", string(EncodeInt(31, 5, SizeUpdate)) == "\x3f\x00", "")

	fmt.Println("\n== 静态表与寻址 ==")
	check("静态表 61 项", staticSize == 61 && len(staticTable) == 61, "")
	t := &Table{MaxSize: 4096}
	e2, _ := t.Lookup(2)
	check("索引 2 = (:method, GET)", e2 == [2]string{":method", "GET"}, e2[0]+" "+e2[1])
	e61, _ := t.Lookup(61)
	check("索引 61 = www-authenticate", e61[0] == "www-authenticate", e61[0])
	_, err := t.Lookup(0)
	check("负向：索引 0 报错", err != nil, "")

	fmt.Println("\n== 条目大小与逐出 ==")
	check("条目大小 = name+value+32", EntrySize("host", "a.io") == 40, "")
	t.Add("x-token", "v1")
	check("表内字节数 41", t.Size == 41, fmt.Sprintf("size=%d", t.Size))
	e62, _ := t.Lookup(62)
	check("动态索引 62 指向最新", e62 == [2]string{"x-token", "v1"}, e62[0])

	te := &Table{MaxSize: 100}
	te.Add("a", "b")
	te.Add("c", "d")
	te.Add("e", "f")
	check("逐出后仍 2 条、size=68", len(te.Dyn) == 2 && te.Size == 68,
		fmt.Sprintf("size=%d", te.Size))
	check("被逐出的是最旧条目 a", te.Dyn[len(te.Dyn)-1] == [2]string{"c", "d"},
		te.Dyn[len(te.Dyn)-1][0])
	tb := &Table{MaxSize: 100}
	check("负向：过大条目无法插入", !tb.Add(string(make([]byte, 5000)), "v"), "")
	check("过大条目导致整表清空", len(tb.Dyn) == 0 && tb.Size == 0, "")

	fmt.Println("\n== 字符串字面量 ==")
	s := EncodeStr([]byte("hello"), false)
	d, h, np, _ := DecodeStr(s, 0)
	check("H=0 原样返回", string(d) == "hello" && !h && np == len(s), "")
	_, h2, _, _ := DecodeStr([]byte{0x80 | 3, 'a', 'b', 'c'}, 0)
	check("H=1 被识别", h2, "")

	fmt.Printf("\n---- %d 项断言，失败 %d 项 ----\n", nAssert, nFail)
	if nFail > 0 {
		panic("有断言失败")
	}
	fmt.Println("ALL PASS")
}
