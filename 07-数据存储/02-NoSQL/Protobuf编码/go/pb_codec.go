// pb_codec.go — 消息编解码:TLV 记录、未知字段跳过、合并语义、group(与 pb_wire.go 同属 package main)
//
// 权威来源(protobuf.dev 官方文档原文):
//   programming-guides/encoding/
//     * "each key-value pair is turned into a record consisting of the field number,
//        a wire type and a payload" —— 即 TLV
//     * "The wire type tells the parser how big the payload after it is. This allows
//        old parsers to skip over new fields they don't understand."
//     * LEN "has a dynamic length, specified by a varint immediately after the tag"
//     * Field Order:"there is no guaranteed order for how its known or unknown fields
//        will be written ... parsers must be able to parse fields in any order"
//     * Last One Wins:"if the same field appears multiple times, the parser accepts the
//        last value it sees. For embedded message fields, the parser merges multiple
//        instances of the same field"
//     * SGROUP/EGROUP "records have empty payloads";"Group field numbers need to match
//        up. If we encounter 7:EGROUP where we expect 8:EGROUP, the message is mal-formed."
package main

import "fmt"

// Record 一条已定界的 TLV 记录。
type Record struct {
	Number  int
	Wire    int
	Payload []byte
}

// EncodeRecord 拼一条记录。LEN 会自动补 varint 长度前缀。
func EncodeRecord(number, wire int, payload []byte) ([]byte, error) {
	tag, err := EncodeTag(number, wire)
	if err != nil {
		return nil, err
	}
	switch wire {
	case WireVarint, WireI64, WireI32:
		return append(tag, payload...), nil
	case WireLen:
		return append(append(tag, EncodeVarint(uint64(len(payload)))...), payload...), nil
	case WireSGroup, WireEGroup:
		return tag, nil // 官方:group 记录 payload 为空
	}
	return nil, fmt.Errorf("cannot build record for wire type %d", wire)
}

// Records 逐条解出记录。未知字段也能定位 —— 这是前向兼容的基础。
func Records(data []byte) ([]Record, error) {
	out := []Record{}
	pos := 0
	for pos < len(data) {
		number, wire, next, err := DecodeTag(data, pos)
		if err != nil {
			return nil, err
		}
		pos = next
		var payload []byte
		switch wire {
		case WireVarint:
			start := pos
			if _, pos, err = DecodeVarint(data, pos); err != nil {
				return nil, err
			}
			payload = data[start:pos]
		case WireI64:
			if pos+8 > len(data) {
				return nil, fmt.Errorf("truncated I64 for field %d", number)
			}
			payload, pos = data[pos:pos+8], pos+8
		case WireI32:
			if pos+4 > len(data) {
				return nil, fmt.Errorf("truncated I32 for field %d", number)
			}
			payload, pos = data[pos:pos+4], pos+4
		case WireLen:
			var n uint64
			if n, pos, err = DecodeVarint(data, pos); err != nil {
				return nil, err
			}
			if pos+int(n) > len(data) {
				return nil, fmt.Errorf("truncated LEN for field %d", number)
			}
			payload, pos = data[pos:pos+int(n)], pos+int(n)
		default:
			return nil, fmt.Errorf("group record for field %d", number)
		}
		out = append(out, Record{Number: number, Wire: wire, Payload: payload})
	}
	return out, nil
}

// EncodeMessage 按 fieldOrder(为空则按声明顺序)编码。顺序只改字节,不改语义。
func EncodeMessage(defn *MessageDef, values map[string]interface{},
	fieldOrder []string) ([]byte, error) {
	named := defn.ByName()
	order := fieldOrder
	if order == nil {
		order = []string{}
		for _, f := range defn.Fields {
			order = append(order, f.Name)
		}
	}
	out := []byte{}
	for _, name := range order {
		fd := named[name]
		if fd == nil {
			return nil, fmt.Errorf("unknown field %q", name)
		}
		v, present := values[name]
		if !present || v == nil {
			continue
		}
		wire, err := fd.WireType()
		if err != nil {
			return nil, err
		}
		if fd.Repeated {
			items, ok := v.([]interface{})
			if !ok {
				return nil, fmt.Errorf("field %s: need []interface{}", name)
			}
			if fd.Packed && fd.ScalarWire() {
				payload := []byte{}
				for _, it := range items {
					b, err := EncodeScalar(fd, it)
					if err != nil {
						return nil, err
					}
					payload = append(payload, b...)
				}
				rec, err := EncodeRecord(fd.Number, WireLen, payload)
				if err != nil {
					return nil, err
				}
				out = append(out, rec...)
				continue
			}
			for _, it := range items {
				b, err := EncodeScalar(fd, it)
				if err != nil {
					return nil, err
				}
				rec, err := EncodeRecord(fd.Number, wire, b)
				if err != nil {
					return nil, err
				}
				out = append(out, rec...)
			}
			continue
		}
		b, err := EncodeScalar(fd, v)
		if err != nil {
			return nil, err
		}
		rec, err := EncodeRecord(fd.Number, wire, b)
		if err != nil {
			return nil, err
		}
		out = append(out, rec...)
	}
	return out, nil
}

// DecodeMessage 解析消息:未知字段按 wire type 跳过;重复标量取最后一个值。
func DecodeMessage(defn *MessageDef, data []byte) (map[string]interface{}, error) {
	byNum := defn.ByNumber()
	out := map[string]interface{}{}
	pos := 0
	for pos < len(data) {
		number, wire, next, err := DecodeTag(data, pos)
		if err != nil {
			return nil, err
		}
		pos = next
		fd, known := byNum[number]
		if !known {
			if pos, err = SkipField(data, pos, wire); err != nil {
				return nil, err
			}
			continue
		}
		var payload []byte
		switch wire {
		case WireVarint:
			start := pos
			if _, pos, err = DecodeVarint(data, pos); err != nil {
				return nil, err
			}
			payload = data[start:pos]
		case WireI64:
			if pos+8 > len(data) {
				return nil, fmt.Errorf("truncated I64 for field %d", number)
			}
			payload, pos = data[pos:pos+8], pos+8
		case WireI32:
			if pos+4 > len(data) {
				return nil, fmt.Errorf("truncated I32 for field %d", number)
			}
			payload, pos = data[pos:pos+4], pos+4
		case WireLen:
			var n uint64
			if n, pos, err = DecodeVarint(data, pos); err != nil {
				return nil, err
			}
			if pos+int(n) > len(data) {
				return nil, fmt.Errorf("truncated LEN for field %d", number)
			}
			payload, pos = data[pos:pos+int(n)], pos+int(n)
		default:
			return nil, fmt.Errorf("group records must be handled by ParseGroup")
		}
		// 官方:packed 与非 packed 形式解析器都必须接受
		if fd.Repeated && fd.ScalarWire() && wire == WireLen {
			items, err := decodePacked(fd, payload)
			if err != nil {
				return nil, err
			}
			out[fd.Name] = append(toSlice(out[fd.Name]), items...)
			continue
		}
		val, err := DecodeScalar(fd, payload)
		if err != nil {
			return nil, err
		}
		switch {
		case fd.Repeated:
			out[fd.Name] = append(toSlice(out[fd.Name]), val)
		case fd.Kind == "message":
			cur, _ := out[fd.Name].(map[string]interface{})
			if cur == nil {
				cur = map[string]interface{}{}
			}
			out[fd.Name] = MergeMessage(cur, val.(map[string]interface{}), fd.MsgDef)
		default:
			out[fd.Name] = val
		}
	}
	return out, nil
}
