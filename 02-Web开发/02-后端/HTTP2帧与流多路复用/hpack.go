// Go 侧对照实现（二）：RFC 7541 的整数表示、静态表与动态表解码。
//
// 官方口径：
//   §5.1   整数表示：小于 2^N-1 直接放 N 位前缀；否则前缀置全 1，
//          余下的值按 128 进制续写，每个续字节最高位置 1，最后一字节不置
//   附录 C.1 10 用 5 位前缀 → 0x0A；1337 用 5 位前缀 → 1F 9A 0A；42 用 8 位前缀 → 0x2A
//   §4.1   动态表条目大小 = len(name) + len(value) + 32
//   附录 C.3.1 首个请求解码后动态表为 [1] (s = 57) :authority: www.example.com
package main

import (
	"errors"
	"fmt"
)

var errProtocol = errors.New("PROTOCOL_ERROR")

// EntryOverhead 是 §4.1 规定的每条目固定开销。
const EntryOverhead = 32

// StaticTable 逐项照抄 RFC 7541 附录 A 的 Table 1（共 61 项）。
var StaticTable = [][2]string{
	{":authority", ""}, {":method", "GET"}, {":method", "POST"},
	{":path", "/"}, {":path", "/index.html"}, {":scheme", "http"},
	{":scheme", "https"}, {":status", "200"}, {":status", "204"},
	{":status", "206"}, {":status", "304"}, {":status", "400"},
	{":status", "404"}, {":status", "500"}, {"accept-charset", ""},
	{"accept-encoding", "gzip, deflate"}, {"accept-language", ""},
	{"accept-ranges", ""}, {"accept", ""}, {"access-control-allow-origin", ""},
	{"age", ""}, {"allow", ""}, {"authorization", ""}, {"cache-control", ""},
	{"content-disposition", ""}, {"content-encoding", ""}, {"content-language", ""},
	{"content-length", ""}, {"content-location", ""}, {"content-range", ""},
	{"content-type", ""}, {"cookie", ""}, {"date", ""}, {"etag", ""},
	{"expect", ""}, {"expires", ""}, {"from", ""}, {"host", ""},
	{"if-match", ""}, {"if-modified-since", ""}, {"if-none-match", ""},
	{"if-range", ""}, {"if-unmodified-since", ""}, {"last-modified", ""},
	{"link", ""}, {"location", ""}, {"max-forwards", ""}, {"proxy-authenticate", ""},
	{"proxy-authorization", ""}, {"range", ""}, {"referer", ""}, {"refresh", ""},
	{"retry-after", ""}, {"server", ""}, {"set-cookie", ""},
	{"strict-transport-security", ""}, {"transfer-encoding", ""}, {"user-agent", ""},
	{"vary", ""}, {"via", ""}, {"www-authenticate", ""},
}

// EncodeInt 是 §5.1 的编码。
func EncodeInt(value int, prefixBits uint) []byte {
	limit := 1<<prefixBits - 1
	if value < limit {
		return []byte{byte(value)}
	}
	out := []byte{byte(limit)}
	value -= limit
	for value >= 128 {
		out = append(out, byte(value%128+128))
		value /= 128
	}
	return append(out, byte(value))
}

// DecodeInt 是 §5.1 的解码，返回 (值, 新位置)。
func DecodeInt(data []byte, pos int, prefixBits uint) (int, int) {
	limit := 1<<prefixBits - 1
	value := int(data[pos]) & limit
	if value < limit {
		return value, pos + 1
	}
	m := uint(0)
	pos++
	for {
		b := data[pos]
		value += int(b&127) << m
		m += 7
		pos++
		if b&128 == 0 {
			break
		}
	}
	return value, pos
}

// EntrySize 对应 §4.1 的条目大小公式。
func EntrySize(name, value string) int {
	return len(name) + len(value) + EntryOverhead
}

// Decoder 是极简 HPACK 解码器：支持索引表示与带索引的字面量。
// Huffman（附录 B，257 项码表）未实现，故只处理原始字节字符串 —— 与附录 C.2/C.3 一致。
type Decoder struct {
	MaxSize int
	Dynamic [][2]string // 索引 1 在表头（最新）
	Size    int
}

// NewDecoder 建立解码器。
func NewDecoder(maxSize int) *Decoder {
	return &Decoder{MaxSize: maxSize}
}

// Lookup 解析索引：先静态表，再动态表。
func (d *Decoder) Lookup(index int) ([2]string, error) {
	if index <= 0 {
		return [2]string{}, errProtocol
	}
	if index <= len(StaticTable) {
		return StaticTable[index-1], nil
	}
	i := index - len(StaticTable) - 1
	if i >= len(d.Dynamic) {
		return [2]string{}, errProtocol
	}
	return d.Dynamic[i], nil
}

// Add 插入动态表并按需驱逐最旧条目。
func (d *Decoder) Add(name, value string) {
	d.Dynamic = append([][2]string{{name, value}}, d.Dynamic...)
	d.Size += EntrySize(name, value)
	for d.Size > d.MaxSize && len(d.Dynamic) > 0 {
		last := d.Dynamic[len(d.Dynamic)-1]
		d.Dynamic = d.Dynamic[:len(d.Dynamic)-1]
		d.Size -= EntrySize(last[0], last[1])
	}
}

func readStr(data []byte, pos int) (string, int) {
	n, pos := DecodeInt(data, pos, 7)
	return string(data[pos : pos+n]), pos + n
}

// Decode 解码一个字段块。
func (d *Decoder) Decode(data []byte) ([][2]string, error) {
	out := [][2]string{}
	pos := 0
	for pos < len(data) {
		b := data[pos]
		switch {
		case b&0x80 != 0: // 1xxxxxxx 索引表示
			idx, np := DecodeInt(data, pos, 7)
			e, err := d.Lookup(idx)
			if err != nil {
				return nil, err
			}
			out = append(out, e)
			pos = np
		case b&0x40 != 0: // 01xxxxxx 带索引的字面量
			idx, np := DecodeInt(data, pos, 6)
			pos = np
			name := ""
			if idx > 0 {
				e, err := d.Lookup(idx)
				if err != nil {
					return nil, err
				}
				name = e[0]
			} else {
				name, pos = readStr(data, pos)
			}
			value, np2 := readStr(data, pos)
			pos = np2
			out = append(out, [2]string{name, value})
			d.Add(name, value)
		default:
			return nil, errProtocol
		}
	}
	return out, nil
}

// DemoHpack 跑一遍附录 C.3.1 的官方字节序列。
func DemoHpack() {
	fmt.Printf("encode 10 (5bit)   = %x\n", EncodeInt(10, 5))
	fmt.Printf("encode 1337 (5bit) = %x\n", EncodeInt(1337, 5))
	fmt.Printf("encode 42 (8bit)   = %x\n", EncodeInt(42, 8))

	wire := []byte{
		0x82, 0x86, 0x84, 0x41, 0x0f,
		'w', 'w', 'w', '.', 'e', 'x', 'a', 'm', 'p', 'l', 'e', '.', 'c', 'o', 'm',
	}
	d := NewDecoder(InitHeaderTableSize)
	headers, err := d.Decode(wire)
	if err != nil {
		fmt.Println("decode err:", err)
		return
	}
	fmt.Printf("headers = %v\n", headers)
	fmt.Printf("dynamic = %v size=%d\n", d.Dynamic, d.Size)
}
