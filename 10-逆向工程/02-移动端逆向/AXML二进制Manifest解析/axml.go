// axml.go — 与 axml.py 同语义的 Go 复刻(静态审查用)。
package main

import (
	"encoding/binary"
	"fmt"
	"unicode/utf16"
)

const (
	resStringPoolType = 0x0001
	resXMLType        = 0x0003
	resXMLStartElement = 0x0102
	utf8Flag          = 1 << 8
	typeString        = 0x03
)

func u16(b []byte, o int) uint16  { return binary.LittleEndian.Uint16(b[o:]) }
func u32(b []byte, o int) uint32  { return binary.LittleEndian.Uint32(b[o:]) }

// LoadPool:索引数组值是相对 stringsStart 的偏移。
func LoadPool(b []byte, off int) []string {
	count := int(u32(b, off+8))
	flags := u32(b, off+16)
	stringsStart := int(u32(b, off+20))
	out := make([]string, 0, count)
	for i := 0; i < count; i++ {
		idx := int(u32(b, off+28+i*4))
		d := off + stringsStart + idx
		if flags&utf8Flag != 0 {
			blen := int(b[d+2])
			out = append(out, string(b[d+3:d+3+blen]))
		} else {
			n := int(u16(b, d))
			if n&0x8000 != 0 {
				n = (n&0x7FFF)<<16 | int(u16(b, d+2))
				d += 2
			}
			u16s := make([]uint16, n)
			for j := 0; j < n; j++ {
				u16s[j] = u16(b, d+2+j*2)
			}
			out = append(out, string(utf16.Decode(u16s)))
		}
	}
	return out
}

// Parse:按 chunk 的 size 逐个跳;START_ELEMENT 读 attrExt(o+16 起)与 20 字节属性。
func Parse(b []byte, pool []string) []string {
	var out []string
	total := int(u32(b, 4))
	o := 8
	for o < total {
		ctype, hsize, size := u16(b, o), u16(b, o+2), u32(b, o+4)
		switch ctype {
		case resStringPoolType:
			pool = LoadPool(b, o)
		case resXMLStartElement:
			name := pool[u32(b, o+20)]
			count := int(u16(b, o+28))
			for i := 0; i < count; i++ {
				a := o + int(hsize) + i*20
				dtype := b[a+15]
				val := u32(b, a+16)
				s := fmt.Sprintf("%d", val)
				if dtype == typeString {
					s = pool[val]
				}
				out = append(out, fmt.Sprintf("%s/%s=%s", name, pool[u32(b, a+4)], s))
			}
		}
		o += int(size)
	}
	return out
}

func main() {
	fmt.Println("consts:", resStringPoolType, resXMLType, resXMLStartElement, utf8Flag)
}
