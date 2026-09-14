// Bolt 协议三层最小实现 (Go 版): 握手 / 分块传输 / PackStream.
// 语义同 Python 版, 依据 Neo4j Bolt Protocol 官方文档.
package main

import (
	"encoding/binary"
	"fmt"
	"math"
	"unicode/utf8"
)

var magic = []byte{0x60, 0x60, 0xB0, 0x17} // Bolt 协议标识

// ---------- 1. 握手 ----------

// BuildHandshake 客户端握手: magic + 恰好 4 个大端 32 位版本(不足补 0).
func BuildHandshake(versions []uint32) []byte {
	out := append([]byte{}, magic...)
	for i := 0; i < 4; i++ {
		v := uint32(0)
		if i < len(versions) {
			v = versions[i]
		}
		out = binary.BigEndian.AppendUint32(out, v)
	}
	return out
}

// ServerNegotiate 按客户端偏好顺序取第一个双方支持的版本; 0 = 无匹配 -> 断连.
func ServerNegotiate(clientVersions []uint32, supported map[uint32]bool) uint32 {
	for _, v := range clientVersions {
		if v != 0 && supported[v] {
			return v
		}
	}
	return 0
}

// ---------- 2. 分块传输 (Chunking) ----------

// ChunkMessage 消息 -> chunk 流: 每 chunk = 2B 大端长度 + 数据, 尾接 00 00.
func ChunkMessage(payload []byte, maxChunk int) []byte {
	if maxChunk <= 0 || maxChunk > 0xFFFF {
		maxChunk = 0xFFFF
	}
	var out []byte
	for i := 0; i < len(payload); i += maxChunk {
		end := i + maxChunk
		if end > len(payload) {
			end = len(payload)
		}
		out = binary.BigEndian.AppendUint16(out, uint16(end-i))
		out = append(out, payload[i:end]...)
	}
	out = append(out, 0x00, 0x00) // 0x0000: 消息边界标记
	return out
}

// ChunkReader 接收方: 从字节流逐 chunk 重组完整消息(TCP 无消息边界).
// 先扫描确认整条消息(含结束标记)已到达, 才一次性消费缓冲.
type ChunkReader struct {
	buf []byte
}

func (r *ChunkReader) Feed(data []byte) { r.buf = append(r.buf, data...) }

// NextMessage 返回下一条完整消息; 数据不足返回 nil 且缓冲不变.
func (r *ChunkReader) NextMessage() []byte {
	var msg []byte
	pos := 0
	for {
		if len(r.buf)-pos < 2 {
			return nil // chunk 头未收全
		}
		size := int(binary.BigEndian.Uint16(r.buf[pos : pos+2]))
		if size == 0 { // 00 00: 消息完整, 一次性消费
			r.buf = r.buf[pos+2:]
			return msg
		}
		if len(r.buf)-pos < 2+size {
			return nil // chunk 数据未收全
		}
		msg = append(msg, r.buf[pos+2:pos+2+size]...)
		pos += 2 + size
	}
}

// ---------- 3. PackStream ----------

// Packer 简化: 只实现 Bolt 消息实际会用到的类型.
type Packer struct{ buf []byte }

func (p *Packer) Bytes() []byte { return p.buf }

func (p *Packer) PackNil()    { p.buf = append(p.buf, 0xC0) }
func (p *Packer) PackBool(b bool) {
	if b {
		p.buf = append(p.buf, 0xC3)
	} else {
		p.buf = append(p.buf, 0xC2)
	}
}

func (p *Packer) PackInt(v int64) {
	switch {
	case v >= -16 && v <= 127: // TINY_INT 单字节
		p.buf = append(p.buf, byte(v))
	case v >= math.MinInt8 && v <= math.MaxInt8:
		p.buf = append(p.buf, 0xC8, byte(v))
	case v >= math.MinInt16 && v <= math.MaxInt16:
		p.buf = append(p.buf, 0xC9)
		p.buf = binary.BigEndian.AppendUint16(p.buf, uint16(v))
	case v >= math.MinInt32 && v <= math.MaxInt32:
		p.buf = append(p.buf, 0xCA)
		p.buf = binary.BigEndian.AppendUint32(p.buf, uint32(v))
	default:
		p.buf = append(p.buf, 0xCB)
		p.buf = binary.BigEndian.AppendUint64(p.buf, uint64(v))
	}
}

func (p *Packer) PackFloat(f float64) {
	p.buf = append(p.buf, 0xC1)
	p.buf = binary.BigEndian.AppendUint64(p.buf, math.Float64bits(f))
}

func (p *Packer) PackString(s string) {
	b := len(s) // demo 只用 ASCII; 生产实现须按 UTF-8 字节数计
	if b < 16 {
		p.buf = append(p.buf, 0x80|byte(b))
	} else if b <= 0xFF {
		p.buf = append(p.buf, 0xD0, byte(b))
	} else {
		p.buf = append(p.buf, 0xD1)
		p.buf = binary.BigEndian.AppendUint16(p.buf, uint16(b))
	}
	p.buf = append(p.buf, s...)
}

// PackStruct marker B{n} + tag byte + fields(调用方逐个 pack).
func (p *Packer) PackStruct(tag byte, fieldCount int) {
	if fieldCount < 16 {
		p.buf = append(p.buf, 0xB0|byte(fieldCount))
	} else {
		p.buf = append(p.buf, 0xDC, byte(fieldCount)) // 8-bit 扩展
	}
	p.buf = append(p.buf, tag)
}

// Unpacker 递归下降解码, 返回 (值, 消耗字节数). 值类型:
// nil / bool / int64 / float64 / string / []any / map[string]any / StructValue.
type Unpacker struct{}

type StructValue struct {
	Tag    byte
	Fields []any
}

func (Unpacker) Unpack(data []byte) (any, int) {
	m := data[0]
	switch {
	case m == 0xC0:
		return nil, 1
	case m == 0xC2:
		return false, 1
	case m == 0xC3:
		return true, 1
	case m == 0xC1:
		return math.Float64frombits(binary.BigEndian.Uint64(data[1:9])), 9
	case m == 0xC8:
		return int64(int8(data[1])), 2
	case m == 0xC9:
		return int64(int16(binary.BigEndian.Uint16(data[1:3]))), 3
	case m == 0xCA:
		return int64(int32(binary.BigEndian.Uint32(data[1:5]))), 5
	case m == 0xCB:
		return int64(binary.BigEndian.Uint64(data[1:9])), 9
	case m <= 0x7F:
		return int64(m), 1
	case m >= 0xF0:
		return int64(int8(m)), 1
	}
	hi, lo := m>>4, m&0x0F
	switch hi {
	case 0x8: // String (tiny)
		return string(data[1 : 1+lo]), 1 + int(lo)
	case 0x9: // List (tiny)
		vals, used := unpackSeq(data[1:], int(lo))
		return vals, 1 + used
	case 0xA: // Dictionary (tiny): n 对 key-value
		vals, used := unpackSeq(data[1:], int(lo)*2)
		return toMap(vals), 1 + used
	case 0xB: // Structure: marker + tag + fields
		fields, used := unpackSeq(data[2:], int(lo))
		return StructValue{data[1], fields}, 2 + used
	case 0xD:
		return unpackSized(data)
	}
	panic(fmt.Sprintf("reserved marker 0x%02x", m))
}

func unpackSeq(data []byte, count int) ([]any, int) {
	vals := make([]any, 0, count)
	off := 0
	u := Unpacker{}
	for i := 0; i < count; i++ {
		v, used := u.Unpack(data[off:])
		vals = append(vals, v)
		off += used
	}
	return vals, off
}

func toMap(flat []any) map[string]any {
	m := map[string]any{}
	for i := 0; i+1 < len(flat); i += 2 {
		m[flat[i].(string)] = flat[i+1]
	}
	return m
}

// unpackSized 处理 D0-DA 扩展 marker(字符串/列表/字典的 8/16/32 位规模).
func unpackSized(data []byte) (any, int) {
	m := data[0]
	var kind byte
	var width int
	switch m {
	case 0xD0, 0xD4, 0xD8: // str / list / dict, 8-bit size
		kind, width = m&0x0C, 1
	case 0xD1, 0xD5, 0xD9:
		kind, width = m&0x0C, 2
	case 0xD2, 0xD6, 0xDA:
		kind, width = m&0x0C, 4
	default:
		panic(fmt.Sprintf("unsupported D-marker 0x%02x", m))
	}
	var n int
	switch width {
	case 1:
		n = int(data[1])
	case 2:
		n = int(binary.BigEndian.Uint16(data[1:3]))
	case 4:
		n = int(binary.BigEndian.Uint32(data[1:5]))
	}
	off := 1 + width
	switch kind {
	case 0x0: // 0xD0&Dx -> str
		return string(data[off : off+n]), off + n
	case 0x4: // 0xD4& -> list
		vals, used := unpackSeq(data[off:], n)
		return vals, off + used
	default: // 0xD8& -> dict
		vals, used := unpackSeq(data[off:], n*2)
		return toMap(vals), off + used
	}
}

func assert(cond bool, msg string) {
	if !cond {
		panic("assert failed: " + msg)
	}
}

func main() {
	// --- PackStream: 官方文档字节示例 ---
	var p Packer
	p.PackInt(42)
	assert(string(p.Bytes()) == "\x2a", "TINY_INT 42 -> 2A")

	var f Packer
	f.PackFloat(1.23)
	assert(fmt.Sprintf("%x", f.Bytes()) == "c13ff3ae147ae147ae", "Float 1.23 官方字节")

	var s Packer
	s.PackString("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
	assert(fmt.Sprintf("%x", s.Bytes()) == "d01a4142434445464748494a4b4c4d4e4f505152535455565758595a",
		"26 字节字符串走 D0")

	// List [1,2,3] -> 93 01 02 03
	var l Packer
	l.buf = append(l.buf, 0x93)
	l.PackInt(1)
	l.PackInt(2)
	l.PackInt(3)
	assert(fmt.Sprintf("%x", l.Bytes()) == "93010203", "List 官方示例")

	// 嵌套解码: [nil, true, "x"]
	u := Unpacker{}
	v, used := u.Unpack([]byte{0x93, 0xC0, 0xC3, 0x81, 'x'})
	list := v.([]any)
	assert(list[0] == nil && list[1] == true && list[2] == "x" && used == 5, "嵌套解码")
	fmt.Println("PackStream 官方字节示例断言全部通过")

	// --- 握手 ---
	hs := BuildHandshake([]uint32{3, 2, 1})
	assert(string(hs[:4]) == string(magic), "magic 前缀")
	assert(len(hs) == 20, "握手总长 20 字节")
	assert(ServerNegotiate([]uint32{3, 2, 1}, map[uint32]bool{1: true, 2: true}) == 2,
		"按客户端偏好顺序选版本")
	assert(ServerNegotiate([]uint32{5, 0, 0, 0}, map[uint32]bool{4: true, 3: true}) == 0,
		"无匹配返回 0 -> 断连")
	fmt.Printf("握手字节: % x\n", hs)

	// --- 分块: 官方 20 字节拆两 chunk 示例(16 + 4) ---
	payload := make([]byte, 20)
	for i := range payload {
		payload[i] = byte(i)
	}
	wire := ChunkMessage(payload, 16)
	expect := append([]byte{0x00, 0x10}, payload[:16]...)
	expect = append(expect, 0x00, 0x04)
	expect = append(expect, payload[16:]...)
	expect = append(expect, 0x00, 0x00)
	assert(string(wire) == string(expect), "20 字节消息拆 16+4 两 chunk")

	// --- 接收方重组: 模拟 TCP 分段错位 ---
	r := &ChunkReader{}
	assert(r.NextMessage() == nil, "无数据返回 nil")
	for i := 0; i < len(wire); i += 3 {
		end := i + 3
		if end > len(wire) {
			end = len(wire)
		}
		r.Feed(wire[i:end])
		if end < len(wire) {
			assert(r.NextMessage() == nil, "未收全必须返回 nil")
		}
	}
	got := r.NextMessage()
	assert(string(got) == string(payload), "分段错位后正确重组")
	fmt.Println("分块重组: 消息跨 chunk + TCP 分段错位均正确重组")

	// --- 组合: RUN 消息(Structure tag=0x10) 完整链路 ---
	var run Packer
	run.PackStruct(0x10, 3)      // RUN
	run.PackString("RETURN 1")   // query
	run.buf = append(run.buf, 0xA0) // params = {}
	run.buf = append(run.buf, 0xA0) // extra = {}
	full := ChunkMessage(run.Bytes(), 0xFFFF)
	r2 := &ChunkReader{}
	r2.Feed(full)
	mv, _ := u.Unpack(r2.NextMessage())
	sv := mv.(StructValue)
	assert(sv.Tag == 0x10 && len(sv.Fields) == 3 &&
		sv.Fields[0] == "RETURN 1", "RUN 消息结构")
	fmt.Printf("RUN 消息完整链路(pack->chunk->feed->unpack): %d 字节, fields=%v\n",
		len(full), sv.Fields)
}
