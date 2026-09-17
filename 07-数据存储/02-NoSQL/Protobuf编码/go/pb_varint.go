// pb_varint.go — Protobuf 线格式最底层:varint / ZigZag / tag(Go 实现)
//
// 权威来源(protobuf.dev 官方文档原文):
//   programming-guides/encoding/
//     * "Each byte in the varint has a continuation bit ... This is the most
//        significant bit (MSB) of the byte. The lower 7 bits are a payload"
//     * "These 7-bit payloads are in little-endian order."
//     * 150 编码为 `9601`;1 编码为 `01`
//     * "There are six wire types: VARINT, I64, LEN, SGROUP, EGROUP, and I32"
//     * "The 'tag' of a record is encoded as a varint formed from the field number
//        and the wire type via the formula (field_number << 3) | wire_type"
//     * intN 的负数用补码 → 占满十个字节;sintN 用 ZigZag
//     * ZigZag:正整数 p → 2p;负整数 n → 2|n| - 1;即 (n << 1) ^ (n >> 31|63)
//   programming-guides/proto3/
//     * "You must give each field in your message definition a number between
//        1 and 536,870,911"
//     * "Field numbers 19,000 to 19,999 are reserved for the Protocol Buffers
//        implementation."
//
// 运行: go run *.go
package main

import "fmt"

// 六种 wire type(官方表)
const (
	WireVarint = 0
	WireI64    = 1
	WireLen    = 2
	WireSGroup = 3
	WireEGroup = 4
	WireI32    = 5

	MaxVarintBytes  = 10
	MinFieldNumber  = 1
	MaxFieldNumber  = 536870911 // 官方 2^29 - 1
	ReservedFieldLo = 19000
	ReservedFieldHi = 19999
)

// WireName 便于断言与打印。
func WireName(wire int) string {
	switch wire {
	case WireVarint:
		return "VARINT"
	case WireI64:
		return "I64"
	case WireLen:
		return "LEN"
	case WireSGroup:
		return "SGROUP"
	case WireEGroup:
		return "EGROUP"
	case WireI32:
		return "I32"
	}
	return "UNKNOWN"
}

// ValidateFieldNumber 官方两条限制:必须在 [1, 2^29-1],且不能落在 19000..19999。
func ValidateFieldNumber(n int) error {
	if n < MinFieldNumber || n > MaxFieldNumber {
		return fmt.Errorf("field number out of range: %d", n)
	}
	if n >= ReservedFieldLo && n <= ReservedFieldHi {
		return fmt.Errorf("field number reserved: %d", n)
	}
	return nil
}

// EncodeVarint 无符号 varint。负数请先转 uint64(int64 的补码语义会自然得到十字节)。
func EncodeVarint(v uint64) []byte {
	out := make([]byte, 0, MaxVarintBytes)
	for {
		b := byte(v & 0x7F)
		v >>= 7
		if v != 0 {
			out = append(out, b|0x80)
			continue
		}
		return append(out, b)
	}
}

// VarintLength 编码后的字节数。
func VarintLength(v uint64) int {
	n := 1
	for v > 0x7F {
		v >>= 7
		n++
	}
	return n
}

// SignedVarintLength 有符号语义下的长度:负数一律补码成 64 位 → 十字节。
func SignedVarintLength(v int64) int {
	if v < 0 {
		return MaxVarintBytes
	}
	return VarintLength(uint64(v))
}

// DecodeVarint 返回 (无符号值, 新位置)。第 10 个字节的最高位必须是 0。
func DecodeVarint(data []byte, pos int) (uint64, int, error) {
	start := pos
	var result uint64
	shift := uint(0)
	for {
		if pos >= len(data) {
			return 0, pos, fmt.Errorf("truncated varint at %d", start)
		}
		if pos-start >= MaxVarintBytes {
			return 0, pos, fmt.Errorf("varint longer than %d bytes at %d", MaxVarintBytes, start)
		}
		b := data[pos]
		pos++
		result |= uint64(b&0x7F) << shift
		if b&0x80 == 0 {
			return result, pos, nil
		}
		shift += 7
	}
}

// AsSigned64 把无符号 64 位解释成有符号(int64 补码语义)。
func AsSigned64(v uint64) int64 { return int64(v) }

// AsSigned32 把无符号 32 位解释成有符号(int32 补码语义)。
func AsSigned32(v uint64) int32 { return int32(uint32(v)) }

// ZigZagEncode 官方:每个值 n 用 (n << 1) ^ (n >> 31)(sint32)或 (n >> 63)(sint64)。
func ZigZagEncode(n int64, bits int) uint64 {
	raw := (n << 1) ^ (n >> (bits - 1))
	if bits == 32 {
		return uint64(uint32(raw))
	}
	return uint64(raw)
}

// ZigZagDecode ZigZag 逆变换:最低位是符号位。
func ZigZagDecode(v uint64) int64 {
	return int64(v>>1) ^ -int64(v&1)
}

// EncodeTag 官方公式:(field_number << 3) | wire_type,整体按 varint 编码。
func EncodeTag(number, wire int) ([]byte, error) {
	if err := ValidateFieldNumber(number); err != nil {
		return nil, err
	}
	if wire != WireVarint && wire != WireI64 && wire != WireLen &&
		wire != WireSGroup && wire != WireEGroup && wire != WireI32 {
		return nil, fmt.Errorf("unknown wire type %d", wire)
	}
	return EncodeVarint(uint64(number)<<3 | uint64(wire)), nil
}

// DecodeTag 返回 (字段号, wire type, 新位置)。低 3 位是 wire type。
func DecodeTag(data []byte, pos int) (int, int, int, error) {
	raw, pos, err := DecodeVarint(data, pos)
	if err != nil {
		return 0, 0, pos, err
	}
	wire := int(raw & 0x07)
	number := int(raw >> 3)
	if wire > WireI32 {
		return 0, 0, pos, fmt.Errorf("unknown wire type %d", wire)
	}
	if number < MinFieldNumber {
		return 0, 0, pos, fmt.Errorf("field number 0 is not allowed")
	}
	return number, wire, pos, nil
}

// TagSize tag 编码后的字节数。
func TagSize(number, wire int) int {
	return VarintLength(uint64(number)<<3 | uint64(wire))
}
