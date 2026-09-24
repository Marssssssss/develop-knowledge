// Package main 是 quic_varint.py 的 Go 转写。
//
// 显式落地的语言差异:
//   * Python 用异常表达协议错误,Go 里用 (值, error) 二元组;协议错误类型
//     通过字符串常量比较(FrameEncodingError / ProtocolViolation)。
//   * RFC 里的值最大到 2^62-1,Go 的 int 在 64 位平台够用;32 位平台请用 int64。
//     本文件统一用 int64 以免歧义。
package main

import "fmt"

const (
	max1 = (1 << 6) - 1
	max2 = (1 << 14) - 1
	max4 = (1 << 30) - 1
	max8 = (1 << 62) - 1

	frameEncodingError = "FRAME_ENCODING_ERROR"
	protocolViolation  = "PROTOCOL_VIOLATION"

	padding = 0x00
	ping    = 0x01
)

// PktRange 一个连续被确认的区间 [Lo, Hi](闭区间)。
type PktRange struct{ Lo, Hi int64 }

func quicErr(kind, detail string) error {
	return fmt.Errorf("%s: %s", kind, detail)
}

func errKind(err error) string {
	if err == nil {
		return ""
	}
	msg := err.Error()
	for i := 0; i < len(msg); i++ {
		if msg[i] == ':' {
			return msg[:i]
		}
	}
	return msg
}

// VarintLenFor 最短编码长度。
func VarintLenFor(v int64) (int, error) {
	switch {
	case v < 0:
		return 0, quicErr(frameEncodingError, "negative")
	case v <= max1:
		return 1, nil
	case v <= max2:
		return 2, nil
	case v <= max4:
		return 4, nil
	case v <= max8:
		return 8, nil
	}
	return 0, quicErr(frameEncodingError, "value out of range")
}

// VarintEncode 编码;length 为 0 时取最短编码。
func VarintEncode(v int64, length int) ([]byte, error) {
	if v < 0 {
		return nil, quicErr(frameEncodingError, "negative")
	}
	if length == 0 {
		n, err := VarintLenFor(v)
		if err != nil {
			return nil, err
		}
		length = n
	}
	var prefix int64
	switch length {
	case 1:
		prefix = 0
	case 2:
		prefix = 1
	case 4:
		prefix = 2
	case 8:
		prefix = 3
	default:
		return nil, quicErr(frameEncodingError, "bad length")
	}
	limit := []int64{max1, max2, max4, max8}[prefix]
	if v > limit {
		return nil, quicErr(frameEncodingError, "value too big for length")
	}
	out := make([]byte, length)
	out[0] = byte(prefix<<6) | byte((v>>(8*(length-1)))&0x3F)
	for i := 1; i < length; i++ {
		out[i] = byte((v >> (8 * (length - 1 - i))) & 0xFF)
	}
	return out, nil
}

// VarintDecode 解码,返回 (值, 消耗字节数)。对应 RFC 9000 附录 A.1 的伪码。
func VarintDecode(data []byte, pos int) (int64, int, error) {
	if pos >= len(data) {
		return 0, 0, quicErr(frameEncodingError, "truncated: no first byte")
	}
	first := data[pos]
	prefix := int64(first >> 6)
	length := 1 << prefix
	if pos+length > len(data) {
		return 0, 0, quicErr(frameEncodingError, "truncated")
	}
	v := int64(first & 0x3F)
	for i := 1; i < length; i++ {
		v = (v << 8) + int64(data[pos+i])
	}
	return v, length, nil
}

// IsShortestEncoding 帧类型必须最短编码(RFC 9000 §12.4)。
func IsShortestEncoding(raw []byte) bool {
	v, used, err := VarintDecode(raw, 0)
	if err != nil {
		return false
	}
	want, err := VarintEncode(v, 0)
	if err != nil {
		return false
	}
	if used != len(raw) || len(want) != len(raw) {
		return false
	}
	for i := range raw {
		if raw[i] != want[i] {
			return false
		}
	}
	return true
}

// DecodeAckRanges 解码 ACK 的区间序列。ranges = [(gap, ack_range_length), ...]。
func DecodeAckRanges(largest, firstAckRange int64, ranges [][2]int64) ([]PktRange, error) {
	out := []PktRange{}
	smallest := largest - firstAckRange
	if smallest < 0 || largest < 0 {
		return nil, quicErr(frameEncodingError, "negative packet number")
	}
	out = append(out, PktRange{Lo: smallest, Hi: largest})
	for _, r := range ranges {
		gap, ackRangeLength := r[0], r[1]
		nxtLargest := smallest - gap - 2 // largest = previous_smallest - gap - 2
		nxtSmallest := nxtLargest - ackRangeLength
		if nxtLargest < 0 || nxtSmallest < 0 {
			return nil, quicErr(frameEncodingError, "negative packet number")
		}
		out = append(out, PktRange{Lo: nxtSmallest, Hi: nxtLargest})
		smallest = nxtSmallest
	}
	return out, nil
}

// GapPacketCount 「gap 里的包数比 Gap 字段的编码值多 1」。
func GapPacketCount(gap int64) int64 { return gap + 1 }

// Frame 一个帧:类型 + 负载(本 demo 只建模无内容的那些)。
type Frame struct {
	Type    int64
	Payload []byte
}

// ParseFrames 把一包负载切成帧。空负载 -> PROTOCOL_VIOLATION。
func ParseFrames(payload []byte) ([]Frame, error) {
	if len(payload) == 0 {
		return nil, quicErr(protocolViolation, "packet payload has no frames")
	}
	frames := []Frame{}
	pos := 0
	for pos < len(payload) {
		ftype, used, err := VarintDecode(payload, pos)
		if err != nil {
			return nil, err
		}
		shortest, err := VarintEncode(ftype, 0)
		if err != nil {
			return nil, err
		}
		if used != len(shortest) {
			return nil, quicErr(frameEncodingError, "frame type not shortest")
		}
		pos += used
		switch ftype {
		case padding, ping:
			frames = append(frames, Frame{Type: ftype, Payload: []byte{}})
		default:
			return nil, quicErr(frameEncodingError, "frame not modelled")
		}
	}
	return frames, nil
}
