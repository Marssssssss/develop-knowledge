// pb_group.go — group 解析与未知字段跳过(与 pb_codec.go 同属 package main)
//
// 权威来源(protobuf.dev programming-guides/encoding/):
//   * "The wire type tells the parser how big the payload after it is. This allows
//      old parsers to skip over new fields they don't understand."
//   * "Group field numbers need to match up. If we encounter 7:EGROUP where we
//      expect 8:EGROUP, the message is mal-formed."
package main

import "fmt"

// SkipField 按 wire type 跳过一条未知记录 —— 旧解析器能读新消息的机制。
func SkipField(data []byte, pos, wire int) (int, error) {
	switch wire {
	case WireVarint:
		_, next, err := DecodeVarint(data, pos)
		return next, err
	case WireI64:
		return pos + 8, nil
	case WireI32:
		return pos + 4, nil
	case WireLen:
		n, next, err := DecodeVarint(data, pos)
		if err != nil {
			return pos, err
		}
		return next + int(n), nil
	case WireSGroup:
		return pos, nil
	}
	return pos, fmt.Errorf("EGROUP without SGROUP at %d", pos)
}

// ParseGroup 官方:SGROUP/EGROUP 的字段号必须配对,否则消息 mal-formed。
func ParseGroup(data []byte, pos, fieldNumber int) ([]byte, int, error) {
	start := pos
	for pos < len(data) {
		number, wire, next, err := DecodeTag(data, pos)
		if err != nil {
			return nil, pos, err
		}
		if wire == WireEGroup {
			if number != fieldNumber {
				return nil, pos, fmt.Errorf("mal-formed group: expected %d:EGROUP, got %d:EGROUP",
					fieldNumber, number)
			}
			return data[start:pos], next, nil
		}
		if pos, err = SkipField(data, next, wire); err != nil {
			return nil, pos, err
		}
	}
	return nil, pos, fmt.Errorf("group %d:EGROUP missing", fieldNumber)
}
