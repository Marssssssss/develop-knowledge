// pb_merge.go — 合并语义与 packed 解码(与 pb_codec.go 同属 package main)
//
// 权威来源(protobuf.dev programming-guides/encoding/):
//   * packed:"parsers must be able to parse repeated fields that were compiled as
//      packed as if they were not packed, and vice versa"
//   * Last One Wins:"if the same field appears multiple times, the parser accepts
//      the last value it sees. For embedded message fields, the parser merges
//      multiple instances of the same field"
package main

import "fmt"

func toSlice(v interface{}) []interface{} {
	if v == nil {
		return []interface{}{}
	}
	if s, ok := v.([]interface{}); ok {
		return s
	}
	return []interface{}{v}
}

// MergeMessage 官方 Last One Wins:标量后者覆盖;嵌入消息递归合并;repeated 拼接。
func MergeMessage(dst, src map[string]interface{}, defn *MessageDef) map[string]interface{} {
	named := defn.ByName()
	for k, v := range src {
		fd := named[k]
		switch {
		case fd != nil && fd.Repeated:
			dst[k] = append(toSlice(dst[k]), toSlice(v)...)
		case fd != nil && fd.Kind == "message":
			cur, _ := dst[k].(map[string]interface{})
			if cur == nil {
				cur = map[string]interface{}{}
			}
			dst[k] = MergeMessage(cur, v.(map[string]interface{}), fd.MsgDef)
		default:
			dst[k] = v
		}
	}
	return dst
}

func decodePacked(fd *FieldDef, payload []byte) ([]interface{}, error) {
	out := []interface{}{}
	fixed := 0
	switch fd.Kind {
	case "fixed64", "sfixed64", "double":
		fixed = 8
	case "fixed32", "sfixed32", "float":
		fixed = 4
	}
	p := 0
	for p < len(payload) {
		if fixed > 0 {
			if p+fixed > len(payload) {
				return nil, fmt.Errorf("truncated packed payload")
			}
			v, err := DecodeScalar(fd, payload[p:p+fixed])
			if err != nil {
				return nil, err
			}
			out = append(out, v)
			p += fixed
			continue
		}
		_, q, err := DecodeVarint(payload, p)
		if err != nil {
			return nil, err
		}
		v, err := DecodeScalar(fd, payload[p:q])
		if err != nil {
			return nil, err
		}
		out = append(out, v)
		p = q
	}
	return out, nil
}
