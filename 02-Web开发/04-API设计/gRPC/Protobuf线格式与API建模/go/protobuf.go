// 609 Protobuf 线格式 —— Go 版（仅标准库）：varint / ZigZag / tag / 六种线类型 / 记录级解析。
//
// 口径来源（与 Python 版同一批实读资料）：
//   - protobuf.dev/programming-guides/encoding/：varint 低 7 位小端拼接、MSB 续接；
//     tag = (field<<3)|wire_type；六种线类型；intN 负数按补码占满 10 字节，
//     sintN 用 ZigZag (n<<1)^(n>>31/63)；I32/I64 小端；packed 是单个 LEN 记录且
//     解析器必须接受多个键值对并拼接；group 的 SGROUP/EGROUP 字段号必须配对。
//
// 字段号治理与 gRPC 状态判定见 proto.go。
// 运行：go run protobuf.go proto.go（本机无 Go 工具链，代码经人工审查 + 结构校验）
package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"math"
)

// 六种线类型。
const (
	WireVarint = 0
	WireI64    = 1
	WireLen    = 2
	WireSGroup = 3
	WireEGroup = 4
	WireI32    = 5
)

const maxUint64 = ^uint64(0)

// VarintEncode Base 128 varint：低 7 位小端在前，MSB 作续接位。
func VarintEncode(value uint64) []byte {
	out := []byte{}
	for {
		b := byte(value & 0x7F)
		value >>= 7
		if value != 0 {
			out = append(out, b|0x80)
		} else {
			out = append(out, b)
			return out
		}
	}
}

// VarintDecode 返回 (值, 消耗的字节数)。
func VarintDecode(buf []byte) (uint64, int, bool) {
	var result uint64
	shift := uint(0)
	for i, b := range buf {
		if shift > 63 {
			return 0, 0, false
		}
		result |= uint64(b&0x7F) << shift
		if b&0x80 == 0 {
			return result, i + 1, true
		}
		shift += 7
	}
	return 0, 0, false
}

// ZigZagEncode sint32 用 32 位，sint64 用 64 位。
func ZigZagEncode(value int64, bits int) uint64 {
	shifted := value << 1
	sign := value >> (bits - 1)
	return uint64(shifted ^ sign)
}

// ZigZagDecode 还原有符号值。
func ZigZagDecode(value uint64) int64 {
	v := int64(value)
	return (v >> 1) ^ -(v & 1)
}

// TagBytes tag = (field << 3) | wireType。
func TagBytes(field int, wireType int) []byte {
	return VarintEncode(uint64(field)<<3 | uint64(wireType))
}

// SplitTag 低 3 位是线类型，其余是字段号。
func SplitTag(value uint64) (int, int) { return int(value >> 3), int(value & 0x07) }

// EncodeVarintField VARINT 字段。
func EncodeVarintField(field int, value uint64) []byte {
	return append(TagBytes(field, WireVarint), VarintEncode(value)...)
}

// EncodeSintField sint 字段（先 ZigZag 再 varint）。
func EncodeSintField(field int, value int64, bits int) []byte {
	return append(TagBytes(field, WireVarint), VarintEncode(ZigZagEncode(value, bits))...)
}

// EncodeLenField LEN 字段：长度 varint + 载荷。
func EncodeLenField(field int, payload []byte) []byte {
	out := append(TagBytes(field, WireLen), VarintEncode(uint64(len(payload)))...)
	return append(out, payload...)
}

// EncodeStringField string 字段（UTF-8）。
func EncodeStringField(field int, text string) []byte {
	return EncodeLenField(field, []byte(text))
}

// EncodeFixed32Field I32：小端四字节。
func EncodeFixed32Field(field int, value uint32) []byte {
	out := append(TagBytes(field, WireI32), 0, 0, 0, 0)
	binary.LittleEndian.PutUint32(out[len(out)-4:], value)
	return out
}

// EncodeFixed64Field I64：小端八字节。
func EncodeFixed64Field(field int, value uint64) []byte {
	out := append(TagBytes(field, WireI64), make([]byte, 8)...)
	binary.LittleEndian.PutUint64(out[len(out)-8:], value)
	return out
}

// EncodeDoubleField IEEE 754 双精度，小端。
func EncodeDoubleField(field int, value float64) []byte {
	return EncodeFixed64Field(field, math.Float64bits(value))
}

// EncodePacked packed repeated：单个 LEN 记录，载荷是各元素 varint 的拼接。
func EncodePacked(field int, values []uint64) []byte {
	payload := []byte{}
	for _, v := range values {
		payload = append(payload, VarintEncode(v)...)
	}
	return EncodeLenField(field, payload)
}

// ConcatPacked 解析器 MUST 接受多个 packed 键值对并把载荷拼接。
func ConcatPacked(chunks [][]byte) []uint64 {
	payload := []byte{}
	for _, c := range chunks {
		payload = append(payload, c...)
	}
	out := []uint64{}
	for len(payload) > 0 {
		v, n, ok := VarintDecode(payload)
		if !ok {
			break
		}
		out = append(out, v)
		payload = payload[n:]
	}
	return out
}

// EncodeGroup group：SGROUP / EGROUP 字段号必须相同。
func EncodeGroup(field int, body []byte) []byte {
	out := append(TagBytes(field, WireSGroup), body...)
	return append(out, TagBytes(field, WireEGroup)...)
}

// Record 是记录级解析的结果。
type Record struct {
	Field    int
	WireType int
	Payload  []byte
	Value    uint64
}

// DecodeRecords 无 schema 的记录级解析。
func DecodeRecords(buf []byte) []Record {
	out := []Record{}
	pos := 0
	for pos < len(buf) {
		tag, n, ok := VarintDecode(buf[pos:])
		if !ok {
			break
		}
		pos += n
		field, wireType := SplitTag(tag)
		switch wireType {
		case WireVarint:
			v, m, ok := VarintDecode(buf[pos:])
			if !ok {
				return out
			}
			out = append(out, Record{Field: field, WireType: wireType, Value: v})
			pos += m
		case WireI64:
			out = append(out, Record{Field: field, WireType: wireType, Payload: buf[pos : pos+8]})
			pos += 8
		case WireI32:
			out = append(out, Record{Field: field, WireType: wireType, Payload: buf[pos : pos+4]})
			pos += 4
		case WireLen:
			length, m, ok := VarintDecode(buf[pos:])
			if !ok {
				return out
			}
			pos += m
			out = append(out, Record{Field: field, WireType: wireType,
				Payload: buf[pos : pos+int(length)]})
			pos += int(length)
		default:
			out = append(out, Record{Field: field, WireType: wireType})
		}
	}
	return out
}

// GroupIssues 检查 group 的字段号是否配对。
func GroupIssues(records []Record) []string {
	out := []string{}
	stack := []int{}
	for i, r := range records {
		switch r.WireType {
		case WireSGroup:
			stack = append(stack, r.Field)
		case WireEGroup:
			if len(stack) == 0 {
				out = append(out, fmt.Sprintf("下标 %d：多余的 EGROUP", i))
			} else if stack[len(stack)-1] != r.Field {
				out = append(out, fmt.Sprintf("下标 %d：EGROUP %d 与 SGROUP %d 不符",
					i, r.Field, stack[len(stack)-1]))
				stack = stack[:len(stack)-1]
			} else {
				stack = stack[:len(stack)-1]
			}
		}
	}
	for _, f := range stack {
		out = append(out, fmt.Sprintf("SGROUP %d 未闭合", f))
	}
	return out
}

func hexOf(b []byte) string {
	var sb bytes.Buffer
	for i, x := range b {
		if i > 0 {
			sb.WriteString(" ")
		}
		fmt.Fprintf(&sb, "%02x", x)
	}
	return sb.String()
}
