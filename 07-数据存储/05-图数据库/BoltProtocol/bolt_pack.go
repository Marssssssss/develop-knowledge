// PackStream 编解码 (Go 版): marker byte + 规模 + body.
// 依据 Neo4j Bolt Protocol 官方文档 PackStream 章节(语义详见 bolt.py).
package main

import (
	"encoding/binary"
	"fmt"
	"math"
)

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

