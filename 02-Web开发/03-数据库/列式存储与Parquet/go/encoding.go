package main

// ULEB128 是官方 varint-encode() 用的 ULEB-128。
func ULEB128(v int) []byte {
	var out []byte
	for {
		b := byte(v & 0x7F)
		v >>= 7
		if v != 0 {
			out = append(out, b|0x80)
		} else {
			out = append(out, b)
			return out
		}
	}
}

// PackLSBFirst 官方：值从每个字节的**最低位**往高位打包。
func PackLSBFirst(values []int, width int) []byte {
	var out []byte
	cur, bits := 0, 0
	mask := (1 << uint(width)) - 1
	for _, v := range values {
		cur |= (v & mask) << uint(bits)
		bits += width
		for bits >= 8 {
			out = append(out, byte(cur&0xFF))
			cur >>= 8
			bits -= 8
		}
	}
	if bits > 0 {
		out = append(out, byte(cur&0xFF))
	}
	return out
}

// UnpackLSBFirst 是 PackLSBFirst 的逆运算。
func UnpackLSBFirst(data []byte, width, count int) []int {
	out := make([]int, 0, count)
	cur, bits, pos := 0, 0, 0
	mask := (1 << uint(width)) - 1
	for len(out) < count && pos < len(data) {
		for bits < width && pos < len(data) {
			cur |= int(data[pos]) << uint(bits)
			bits += 8
			pos++
		}
		out = append(out, cur&mask)
		cur >>= uint(width)
		bits -= width
	}
	return out
}

// RLEEncode 是极简 RLE/Bit-Packing 混合编码器：长游程走 rle-run，其余按 8 个一组。
func RLEEncode(levels []int, width int) []byte {
	var out []byte
	widthBytes := (width + 7) / 8
	if widthBytes == 0 {
		widthBytes = 1
	}
	for i := 0; i < len(levels); {
		j := i
		for j < len(levels) && levels[j] == levels[i] {
			j++
		}
		if j-i >= 8 {
			out = append(out, ULEB128((j-i)<<1)...)
			out = append(out, PackLSBFirst([]int{levels[i]}, widthBytes*8)...)
			i = j
			continue
		}
		group := make([]int, 0, 8)
		for x := i; x < i+8; x++ {
			if x < len(levels) {
				group = append(group, levels[x])
			} else {
				group = append(group, 0) // 官方：总是按 8 的倍数打包
			}
		}
		out = append(out, ULEB128((len(group)/8)<<1|1)...)
		out = append(out, PackLSBFirst(group, width)...)
		i += 8
	}
	return out
}

// DataPage 官方：rep → def → values 三段背靠背，无填充。
func DataPage(rep, def []int, values []byte, repWidth, defWidth int) []byte {
	page := []byte{}
	if rep != nil {
		page = append(page, 'R')
		page = append(page, RLEEncode(rep, repWidth)...)
	}
	if def != nil {
		page = append(page, 'D')
		page = append(page, RLEEncode(def, defWidth)...)
	}
	page = append(page, 'V')
	page = append(page, values...)
	return page
}

func keysOf(m map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
