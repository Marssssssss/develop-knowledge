// pb_wire.go — 字段定义与标量编解码(与 pb_codec.go 同属 package main)
//
// 权威来源(protobuf.dev 官方文档原文):
//   programming-guides/encoding/
//     * wire type 表:VARINT 用于 int32/int64/uint32/uint64/sint32/sint64/bool/enum;
//       I64 用于 fixed64/sfixed64/double;LEN 用于 string/bytes/embedded messages/
//       packed repeated fields;I32 用于 fixed32/sfixed32/float
//     * "double values are encoded in IEEE 754 double-precision format"
//     * "float values are encoded in IEEE 754 single-precision format"
//     * packed:"parsers must be able to parse repeated fields that were compiled as
//       packed as if they were not packed, and vice versa"
package main

import (
	"encoding/binary"
	"fmt"
	"math"
)

// kindToWire 官方 wire type 表:每类字段固定映射到一种线格式。
var kindToWire = map[string]int{
	"int32": WireVarint, "int64": WireVarint, "uint32": WireVarint,
	"uint64": WireVarint, "sint32": WireVarint, "sint64": WireVarint,
	"bool": WireVarint, "enum": WireVarint,
	"fixed64": WireI64, "sfixed64": WireI64, "double": WireI64,
	"string": WireLen, "bytes": WireLen, "message": WireLen,
	"fixed32": WireI32, "sfixed32": WireI32, "float": WireI32,
}

// FieldDef 一个字段的声明。
type FieldDef struct {
	Number   int
	Name     string
	Kind     string
	Repeated bool
	Packed   bool
	MsgDef   *MessageDef
}

// WireType 返回该字段的 wire type。
func (f *FieldDef) WireType() (int, error) {
	if err := ValidateFieldNumber(f.Number); err != nil {
		return 0, err
	}
	w, ok := kindToWire[f.Kind]
	if !ok {
		return 0, fmt.Errorf("unsupported kind %q", f.Kind)
	}
	return w, nil
}

// ScalarWire VARINT / I64 / I32 是自定界标量,可以 packed。
func (f *FieldDef) ScalarWire() bool {
	w, err := f.WireType()
	if err != nil {
		return false
	}
	return w == WireVarint || w == WireI64 || w == WireI32
}

// MessageDef 一张消息定义。
type MessageDef struct {
	Name   string
	Fields []*FieldDef
}

// ByNumber 字段号 → 定义。
func (m *MessageDef) ByNumber() map[int]*FieldDef {
	out := map[int]*FieldDef{}
	for _, f := range m.Fields {
		out[f.Number] = f
	}
	return out
}

// ByName 字段名 → 定义。
func (m *MessageDef) ByName() map[string]*FieldDef {
	out := map[string]*FieldDef{}
	for _, f := range m.Fields {
		out[f.Name] = f
	}
	return out
}

func toInt64(v interface{}) (int64, bool) {
	switch x := v.(type) {
	case int:
		return int64(x), true
	case int32:
		return int64(x), true
	case int64:
		return x, true
	case uint32:
		return int64(x), true
	case uint64:
		return int64(x), true
	case bool:
		if x {
			return 1, true
		}
		return 0, true
	}
	return 0, false
}

func toFloat64(v interface{}) (float64, bool) {
	switch x := v.(type) {
	case float64:
		return x, true
	case float32:
		return float64(x), true
	case int:
		return float64(x), true
	case int32:
		return float64(x), true
	case int64:
		return float64(x), true
	}
	return 0, false
}

func toBytes(v interface{}) ([]byte, bool) {
	switch x := v.(type) {
	case []byte:
		return x, true
	case string:
		return []byte(x), true
	}
	return nil, false
}

// EncodeScalar 把一个标量编成 payload(不含 tag 与长度前缀)。
func EncodeScalar(fd *FieldDef, v interface{}) ([]byte, error) {
	switch fd.Kind {
	case "int32", "int64", "uint32", "uint64", "enum":
		n, ok := toInt64(v)
		if !ok {
			return nil, fmt.Errorf("field %s: need integer, got %T", fd.Name, v)
		}
		return EncodeVarint(uint64(n)), nil
	case "bool":
		n, ok := toInt64(v)
		if !ok {
			return nil, fmt.Errorf("field %s: need bool", fd.Name)
		}
		return EncodeVarint(uint64(n)), nil
	case "sint32":
		n, _ := toInt64(v)
		return EncodeVarint(ZigZagEncode(n, 32)), nil
	case "sint64":
		n, _ := toInt64(v)
		return EncodeVarint(ZigZagEncode(n, 64)), nil
	case "fixed64":
		n, _ := toInt64(v)
		out := make([]byte, 8)
		binary.LittleEndian.PutUint64(out, uint64(n))
		return out, nil
	case "sfixed64":
		n, _ := toInt64(v)
		out := make([]byte, 8)
		binary.LittleEndian.PutUint64(out, uint64(n))
		return out, nil
	case "double":
		f, _ := toFloat64(v)
		out := make([]byte, 8)
		binary.LittleEndian.PutUint64(out, math.Float64bits(f))
		return out, nil
	case "fixed32":
		n, _ := toInt64(v)
		out := make([]byte, 4)
		binary.LittleEndian.PutUint32(out, uint32(n))
		return out, nil
	case "sfixed32":
		n, _ := toInt64(v)
		out := make([]byte, 4)
		binary.LittleEndian.PutUint32(out, uint32(n))
		return out, nil
	case "float":
		f, _ := toFloat64(v)
		out := make([]byte, 4)
		binary.LittleEndian.PutUint32(out, math.Float32bits(float32(f)))
		return out, nil
	case "string":
		b, ok := toBytes(v)
		if !ok {
			return nil, fmt.Errorf("field %s: need string", fd.Name)
		}
		return b, nil
	case "bytes":
		b, ok := toBytes(v)
		if !ok {
			return nil, fmt.Errorf("field %s: need bytes", fd.Name)
		}
		return b, nil
	case "message":
		m, ok := v.(map[string]interface{})
		if !ok {
			return nil, fmt.Errorf("field %s: need map for embedded message", fd.Name)
		}
		return EncodeMessage(fd.MsgDef, m, nil)
	}
	return nil, fmt.Errorf("unsupported kind %q", fd.Kind)
}

// DecodeScalar 把 payload 解回 Go 值。整数类返回 int64,定长类返回 uint64/float。
func DecodeScalar(fd *FieldDef, payload []byte) (interface{}, error) {
	switch fd.Kind {
	case "int32", "int64", "uint32", "uint64", "bool", "enum":
		v, _, err := DecodeVarint(payload, 0)
		if err != nil {
			return nil, err
		}
		return int64(v), nil
	case "sint32", "sint64":
		v, _, err := DecodeVarint(payload, 0)
		if err != nil {
			return nil, err
		}
		return ZigZagDecode(v), nil
	case "fixed64", "sfixed64":
		return int64(binary.LittleEndian.Uint64(payload)), nil
	case "double":
		return math.Float64frombits(binary.LittleEndian.Uint64(payload)), nil
	case "fixed32", "sfixed32":
		return int64(binary.LittleEndian.Uint32(payload)), nil
	case "float":
		return float64(math.Float32frombits(binary.LittleEndian.Uint32(payload))), nil
	case "string":
		return string(payload), nil
	case "bytes":
		return payload, nil
	case "message":
		// 注意:不能写成 `return DecodeMessage(...)` —— 多值返回要求签名完全一致,
		// map[string]interface{} != interface{}。
		inner, err := DecodeMessage(fd.MsgDef, payload)
		if err != nil {
			return nil, err
		}
		return inner, nil
	}
	return nil, fmt.Errorf("unsupported kind %q", fd.Kind)
}
