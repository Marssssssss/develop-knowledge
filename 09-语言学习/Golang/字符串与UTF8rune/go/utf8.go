// Package main 用 Go 复刻 unicode/utf8 的编解码（src/unicode/utf8/utf8.go）。
//
// 官方实现里这些函数是内联 + 边界检查消除优化过的；这里保留**查表与位运算的原始逻辑**，
// 便于观察「哪一步拦住了哪一类非法序列」。
package main

// 官方常量。
const (
	runeError = 0xFFFD // '\uFFFD'
	runeSelf  = 0x80
	maxRune   = 0x10FFFF
	utfMax    = 4

	surrogateMin = 0xD800
	surrogateMax = 0xDFFF

	tx = 0b10000000
	t2 = 0b11000000
	t3 = 0b11100000

	maskx = 0b00111111
	mask2 = 0b00011111
	mask3 = 0b00001111
	mask4 = 0b00000111

	rune1Max = 1<<7 - 1
	rune2Max = 1<<11 - 1
	rune3Max = 1<<16 - 1

	locb = 0b10000000
	hicb = 0b10111111
)

// first 表的记号。
const (
	xx = 0xF1 // 非法：size 1
	as = 0xF0 // ASCII：size 1
	s1 = 0x02 // accept 0, size 2
	s2 = 0x13 // accept 1, size 3
	s3 = 0x03 // accept 0, size 3
	s4 = 0x23 // accept 2, size 3
	s5 = 0x34 // accept 3, size 4
	s6 = 0x04 // accept 0, size 4
	s7 = 0x44 // accept 4, size 4
)

// first 对应官方的 first [256]uint8。
var first = [256]uint8{
	as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, // 0x00-0x0F
	as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, // 0x10-0x1F
	as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, // 0x20-0x2F
	as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, // 0x30-0x3F
	as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, // 0x40-0x4F
	as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, // 0x50-0x5F
	as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, // 0x60-0x6F
	as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, as, // 0x70-0x7F
	xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, // 0x80-0x8F
	xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, // 0x90-0x9F
	xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, // 0xA0-0xAF
	xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, // 0xB0-0xBF
	xx, xx, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, // 0xC0-0xCF
	s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, s1, // 0xD0-0xDF
	s2, s3, s3, s3, s3, s3, s3, s3, s3, s3, s3, s3, s3, s4, s3, s3, // 0xE0-0xEF
	s5, s6, s6, s6, s7, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, xx, // 0xF0-0xFF
}

type acceptRange struct{ lo, hi uint8 }

// acceptRanges 第二个字节的合法区间，16 项里只用到 0..4。
var acceptRanges = [16]acceptRange{
	{locb, hicb},
	{0xA0, hicb}, // E0：挡 overlong
	{locb, 0x9F}, // ED：挡代理区
	{0x90, hicb}, // F0：挡 overlong
	{locb, 0x8F}, // F4：挡 > U+10FFFF
}

// Rune 是解码结果：码点 + 消耗字节数。
type Rune struct {
	R    rune
	Size int
}

// decodeRune 对应官方 DecodeRune（ASCII 快路径 + decodeRuneSlow）。
func decodeRune(p []byte) Rune {
	if len(p) == 0 {
		return Rune{runeError, 0}
	}
	if p[0] < runeSelf {
		return Rune{rune(p[0]), 1}
	}
	return decodeRuneSlow(p)
}

func decodeRuneSlow(p []byte) Rune {
	n := len(p)
	if n < 1 {
		return Rune{runeError, 0}
	}
	p0 := p[0]
	x := first[p0]
	if x >= as {
		// 官方用 mask-and-or 同时处理 ASCII 与非法，省一次分支
		mask := rune(x) << 31 >> 31 // 0 或 -1
		return Rune{rune(p[0])&^mask | runeError&mask, 1}
	}
	sz := int(x & 7)
	accept := acceptRanges[x>>4]
	if n < sz {
		return Rune{runeError, 1}
	}
	b1 := p[1]
	if b1 < accept.lo || accept.hi < b1 {
		return Rune{runeError, 1}
	}
	if sz <= 2 {
		return Rune{rune(p0&mask2)<<6 | rune(b1&maskx), 2}
	}
	b2 := p[2]
	if b2 < locb || hicb < b2 {
		return Rune{runeError, 1}
	}
	if sz <= 3 {
		return Rune{rune(p0&mask3)<<12 | rune(b1&maskx)<<6 | rune(b2&maskx), 3}
	}
	b3 := p[3]
	if b3 < locb || hicb < b3 {
		return Rune{runeError, 1}
	}
	return Rune{rune(p0&mask4)<<18 | rune(b1&maskx)<<12 | rune(b2&maskx)<<6 | rune(b3&maskx), 4}
}

// runeLen 对应官方 RuneLen：代理区判断夹在 rune2Max 与 rune3Max 之间。
func runeLen(r rune) int {
	switch {
	case r < 0:
		return -1
	case r <= rune1Max:
		return 1
	case r <= rune2Max:
		return 2
	case surrogateMin <= r && r <= surrogateMax:
		return -1
	case r <= rune3Max:
		return 3
	case r <= maxRune:
		return 4
	}
	return -1
}

// encodeRune 对应官方 EncodeRune：越界/代理区写 runeError 的编码。
func encodeRune(r rune) []byte {
	if r < 0 || surrogateMin <= r && r <= surrogateMax || r > maxRune {
		return []byte{t3 | byte(runeError>>12), tx | byte(runeError>>6)&maskx, tx | byte(runeError)&maskx}
	}
	switch {
	case r <= rune1Max:
		return []byte{byte(r)}
	case r <= rune2Max:
		return []byte{t2 | byte(r>>6), tx | byte(r)&maskx}
	case r <= rune3Max:
		return []byte{t3 | byte(r>>12), tx | byte(r>>6)&maskx, tx | byte(r)&maskx}
	default:
		return []byte{0xF0 | byte(r>>18), tx | byte(r>>12)&maskx, tx | byte(r>>6)&maskx, tx | byte(r)&maskx}
	}
}

// runeCountInString 对应官方 RuneCountInString（实现就是 for range）。
func runeCountInString(s []byte) int {
	n := 0
	for i := 0; i < len(s); {
		d := decodeRune(s[i:])
		if d.Size == 0 {
			break
		}
		n++
		i += d.Size
	}
	return n
}

// runeStart 对应官方 RuneStart：续字节的高两位恒为 10。
func runeStart(b byte) bool { return b&0xC0 != 0x80 }

// valid 对应官方 Valid。
func valid(s []byte) bool {
	for i := 0; i < len(s); {
		d := decodeRune(s[i:])
		if d.R == runeError && d.Size == 1 && s[i] >= runeSelf {
			return false
		}
		i += d.Size
	}
	return true
}
