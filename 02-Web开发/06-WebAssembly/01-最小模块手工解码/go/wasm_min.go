// wasm_min.go — 与 python/wasm_min.py 同语义的 Go 复刻(静态审查用)。
package main

import (
	"bytes"
	"fmt"
)

func ulebEncode(n uint32) []byte {
	var out []byte
	for {
		b := byte(n & 0x7F)
		n >>= 7
		if n != 0 {
			b |= 0x80
		}
		out = append(out, b)
		if n == 0 {
			return out
		}
	}
}

func section(id byte, payload []byte) []byte {
	out := []byte{id}
	out = append(out, ulebEncode(uint32(len(payload)))...)
	return append(out, payload...)
}

const i32 = 0x7F

// BuildAddModule:带导出 add 的最小模块(type/function/export/code 四段)。
func BuildAddModule() []byte {
	types := section(1, append([]byte{1, 0x60, 2, i32, i32, 1, i32}, []byte{}...))
	funcs := section(3, []byte{1, 0})
	name := []byte("add")
	exports := section(7, append([]byte{1, 3}, append(name, 0x00, 0)...))
	body := []byte{0, 0x20, 0, 0x20, 1, 0x6A, 0x0B}
	code := section(10, append([]byte{1, byte(len(body))}, body...))
	mod := []byte{0x00, 0x61, 0x73, 0x6D, 0x01, 0x00, 0x00, 0x00}
	for _, s := range [][]byte{types, funcs, exports, code} {
		mod = append(mod, s...)
	}
	return mod
}

// ParseSections:magic/version + 段遍历(size 只数内容)。
func ParseSections(mod []byte) ([]byte, []int) {
	if !bytes.Equal(mod[:8], []byte{0x00, 0x61, 0x73, 0x6D, 0x01, 0x00, 0x00, 0x00}) {
		panic("bad magic/version")
	}
	var ids []int
	i := 8
	for i < len(mod) {
		id := int(mod[i])
		size, n := 0, 0
		for {
			b := mod[i+1+n]
			n++
			size |= int(b&0x7F) << (7 * (n - 1))
			if b&0x80 == 0 {
				break
			}
		}
		ids = append(ids, id)
		i += 1 + n + size
	}
	return mod[:8], ids
}

func main() {
	mod := BuildAddModule()
	_, ids := ParseSections(mod)
	fmt.Println("sections:", ids) // [1 3 7 10]
	fmt.Printf("magic ok, total %d bytes\n", len(mod))
}
