package main

import "fmt"

// Vindex 是 Primary Vindex: 列值 -> keyspace id。
// Cost、Sequential 直接取自 vitessio/vitess 的 vindexes 源码。
type Vindex struct {
	Name       string
	Cost       int
	Sequential bool
}

// HashBinary 对应 binary.go 的 Hash: return id.ToBytes()。
func HashBinary(v []byte) []byte {
	out := make([]byte, len(v))
	copy(out, v)
	return out
}

// HashNumeric 对应 numeric.go 的 Hash: binary.BigEndian.PutUint64(num)。
func HashNumeric(v uint64) []byte {
	out := make([]byte, ksidLen)
	for i := 0; i < ksidLen; i++ {
		out[ksidLen-1-i] = byte(v >> (8 * i))
	}
	return out
}

// UnhashNumeric 对应 numeric.go 的 ReverseMap(要求长度恰为 8)。
func UnhashNumeric(k []byte) (uint64, error) {
	if len(k) != ksidLen {
		return 0, fmt.Errorf("numeric: keyspace id 长度必须为 8: %d", len(k))
	}
	var v uint64
	for i := 0; i < ksidLen; i++ {
		v |= uint64(k[ksidLen-1-i]) << (8 * i)
	}
	return v, nil
}

// Reverse64 等价于 bits.Reverse64。
func Reverse64(x uint64) uint64 {
	var out uint64
	for i := 0; i < 64; i++ {
		if (x>>i)&1 == 1 {
			out |= 1 << (63 - uint(i))
		}
	}
	return out
}

// HashReverseBits 对应 reverse_bits.go 的 Hash: BigEndian(bits.Reverse64(num))。
func HashReverseBits(v uint64) []byte {
	return HashNumeric(Reverse64(v))
}

// UnhashReverseBits 对应 unreverse。
func UnhashReverseBits(k []byte) (uint64, error) {
	if len(k) != ksidLen {
		return 0, fmt.Errorf("reverse_bits: keyspace id 长度必须为 8: %d", len(k))
	}
	n, err := UnhashNumeric(k)
	if err != nil {
		return 0, err
	}
	return Reverse64(n), nil
}

var registry = map[string]Vindex{
	"binary":       {Name: "binary", Cost: 0, Sequential: true},
	"numeric":      {Name: "numeric", Cost: 0, Sequential: true},
	"reverse_bits": {Name: "reverse_bits", Cost: 1, Sequential: false},
}

func lookupVindex(name string) (Vindex, error) {
	v, ok := registry[name]
	if !ok {
		return Vindex{}, fmt.Errorf("未实装的 vindex: %s", name)
	}
	return v, nil
}

// hashID 按 vindex 名把 uint64 列值映射成 keyspace id。
func hashID(name string, id uint64) ([]byte, error) {
	switch name {
	case "numeric":
		return HashNumeric(id), nil
	case "reverse_bits":
		return HashReverseBits(id), nil
	case "binary":
		return HashNumeric(id), nil // binary 走字节, 这里退化成同一编码便于对齐
	}
	return nil, fmt.Errorf("未实装的 vindex: %s", name)
}
