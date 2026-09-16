// Snappy 块格式(Go 版):完整解码器 + 贪心编码器,纯标准库。
// 依据 google/snappy 官方 format_description.txt:preamble 小端 varint
// 未压缩长度;元素 literal(00)/copy1(01)/copy2(10)/copy4(11);
// copy 逐字节回引,RLE 语义允许 len > offset。
package main

import (
	"bytes"
	"encoding/binary"
	"fmt"
	"math/rand"
	"os"
)

// ---------------- preamble 变长整数 ----------------

func appendVarint(out []byte, n uint32) []byte {
	for n >= 0x80 {
		out = append(out, byte(n)|0x80)
		n >>= 7
	}
	return append(out, byte(n))
}

func decodeVarint(b []byte, pos int) (uint32, int, bool) {
	var n uint32
	for shift := uint(0); ; shift += 7 {
		if pos >= len(b) || shift > 28 {
			return 0, 0, false
		}
		c := b[pos]
		pos++
		n |= uint32(c&0x7F) << shift
		if c&0x80 == 0 {
			return n, pos, true
		}
	}
}

// ---------------- 解码器(全格式) ----------------

func decompress(data []byte) ([]byte, error) {
	n, pos, ok := decodeVarint(data, 0)
	if !ok {
		return nil, fmt.Errorf("varint 截断")
	}
	out := make([]byte, 0, n)
	for pos < len(data) {
		tag := data[pos]
		t := tag & 3
		pos++
		var length, offset int
		switch t {
		case 0: // literal:高 6 位 = len-1(<=60),或 60..63 扩展
			h := int(tag >> 2)
			if h < 60 {
				length = h + 1
			} else {
				extra := h - 59 // 60/61/62/63 -> 1~4 字节小端 (len-1)
				if pos+extra > len(data) {
					return nil, fmt.Errorf("literal 长度截断")
				}
				v := uint64(0)
				for i := 0; i < extra; i++ {
					v |= uint64(data[pos+i]) << (8 * i)
				}
				length = 1 + int(v)
				pos += extra
			}
			if pos+length > len(data) {
				return nil, fmt.Errorf("literal 数据截断")
			}
			out = append(out, data[pos:pos+length]...)
			pos += length
			continue
		case 1: // copy1:offset 高 3 位在 tag 内
			length = 4 + int((tag>>2)&7)
			if pos >= len(data) {
				return nil, fmt.Errorf("copy1 offset 截断")
			}
			offset = int(tag>>5)<<8 | int(data[pos])
			pos++
		case 2: // copy2
			length = 1 + int(tag>>2)
			if pos+2 > len(data) {
				return nil, fmt.Errorf("copy2 offset 截断")
			}
			offset = int(binary.LittleEndian.Uint16(data[pos:]))
			pos += 2
		default: // copy4
			length = 1 + int(tag>>2)
			if pos+4 > len(data) {
				return nil, fmt.Errorf("copy4 offset 截断")
			}
			offset = int(binary.LittleEndian.Uint32(data[pos:]))
			pos += 4
		}
		if offset == 0 || offset > len(out) {
			return nil, fmt.Errorf("非法回引 offset=%d(已输出 %d)", offset, len(out))
		}
		for i := 0; i < length; i++ { // 逐字节回引 = RLE
			out = append(out, out[len(out)-offset])
		}
	}
	if uint32(len(out)) != n {
		return nil, fmt.Errorf("声明长度 %d != 实际 %d", n, len(out))
	}
	return out, nil
}

// ---------------- 贪心编码器(教学级) ----------------

func emitLiteral(out []byte, data []byte, start, end int) []byte {
	length := end - start
	if length <= 0 {
		return out
	}
	if length <= 60 {
		out = append(out, byte(length-1)<<2)
	} else {
		for extra, tag := range [4]byte{60, 61, 62, 63} {
			n := length - 1
			if n < 1<<(8*(extra+1)) {
				out = append(out, tag<<2)
				for i := 0; i <= extra; i++ {
					out = append(out, byte(n>>(8*i)))
				}
				break
			}
		}
	}
	return append(out, data[start:end]...)
}

func emitCopy(out []byte, offset, length int) []byte {
	for length > 0 {
		if length >= 4 && length <= 11 && offset <= 2047 { // copy1 最省
			out = append(out, 0x01|byte(length-4)<<2|byte(offset>>8)<<5,
				byte(offset&0xFF))
			return out
		}
		chunk := length
		if chunk > 64 {
			chunk = 64
		}
		if offset <= 0xFFFF {
			out = append(out, 0x02|byte(chunk-1)<<2, byte(offset), byte(offset>>8))
		} else {
			out = append(out, 0x03|byte(chunk-1)<<2,
				byte(offset), byte(offset>>8), byte(offset>>16), byte(offset>>24))
		}
		length -= chunk
	}
	return out
}

func compress(data []byte) []byte {
	out := appendVarint(nil, uint32(len(data)))
	table := make(map[[4]byte]int)
	i, litStart, n := 0, 0, len(data)
	for i+4 <= n {
		var key [4]byte
		copy(key[:], data[i:i+4])
		cand, hit := table[key]
		table[key] = i
		if hit {
			offset := i - cand
			m := 4
			for i+m < n && data[cand+m] == data[i+m] && m < 64 {
				m++
			}
			out = emitLiteral(out, data, litStart, i)
			out = emitCopy(out, offset, m)
			i += m
			litStart = i
			continue
		}
		i++
	}
	return emitLiteral(out, data, litStart, n)
}

// ---------------- 断言 ----------------

var failures int

func check(label string, cond bool) {
	if !cond {
		failures++
		fmt.Printf("[FAIL] %s\n", label)
		return
	}
	fmt.Printf("[ok] %s\n", label)
}

func lcg(n int) []byte {
	x, out := uint32(1), make([]byte, 0, n)
	for i := 0; i < n; i++ {
		x = x*1103515245 + 12345
		out = append(out, byte(x))
	}
	return out
}

func main() {
	// ---- 1. preamble varint(规范原文数值) ----
	check("varint(64) == 0x40", bytes.Equal(appendVarint(nil, 64), []byte{0x40}))
	check("varint(2097150) == FE FF 7F",
		bytes.Equal(appendVarint(nil, 2097150), []byte{0xFE, 0xFF, 0x7F}))

	// ---- 2. 规范原文示例:'xababab' = literal 'xab' + copy(offset=2,len=4) ----
	enc := compress([]byte("xababab"))
	check("规范示例字节串逐字节复现",
		bytes.Equal(enc, []byte{0x07, 0x08, 'x', 'a', 'b', 0x01, 0x02}))
	dec, err := decompress(enc)
	check("规范示例解压还原", err == nil && string(dec) == "xababab")

	// ---- 3. RLE:len > offset ----
	raw := bytes.Repeat([]byte{'a'}, 100)
	dec, err = decompress(compress(raw))
	check("RLE 游程往返", err == nil && bytes.Equal(dec, raw))
	check("手工 copy2(len=1) 解压 'aa'", func() bool {
		d, e := decompress([]byte{0x02, 0x00, 'a', 0x02, 0x01, 0x00})
		return e == nil && string(d) == "aa"
	}())

	// ---- 4. copy4 长回引(70000 字节距离) ----
	a := lcg(70000)
	data := append(append([]byte{}, a...), a[:200]...)
	dec, err = decompress(compress(data))
	check("70000 长回引往返", err == nil && bytes.Equal(dec, data))

	// ---- 5. 混合数据往返 ----
	rng := rand.New(rand.NewSource(42))
	sample := append(bytes.Repeat([]byte("hp=100 mp=50 pos=1,2,3 "), 40), rng.Bytes(64)...)
	sample = append(sample, bytes.Repeat([]byte{0}, 128)...)
	for _, n := range []int{0, 1, 4, 60, 61, 64, 100, 1000} {
		blob := sample
		if n > 0 {
			if n > len(blob) {
				blob = bytes.Repeat(blob, n/len(blob)+1)
			}
			blob = blob[:n]
		} else {
			blob = nil
		}
		d, e := decompress(compress(blob))
		check(fmt.Sprintf("往返 n=%d", n), e == nil && bytes.Equal(d, blob))
	}

	// ---- 6. 解码器守卫 ----
	for _, bad := range [][]byte{
		{0x05, 0x08, 'a', 'b'},                 // 声明长度不符
		{0x04, 0x00, 'a', 0x01, 0x02},          // 回引越界
		{0x04, 0x00, 'a', 0x01, 0x00},          // offset=0
		{0x05, 0x0b, 'a', 'b'},                 // literal 数据截断
	} {
		if _, e := decompress(bad); e == nil {
			check(fmt.Sprintf("守卫 %x 应报错", bad), false)
		}
	}
	check("解码器守卫全部报错", true)

	if failures > 0 {
		fmt.Printf("\n%d 项断言失败\n", failures)
		os.Exit(1)
	}
	fmt.Println("\n全部断言通过")
}
