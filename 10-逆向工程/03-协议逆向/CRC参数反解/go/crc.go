// Package main —— CRC 参数化模型与参数反解（RevEng / Rocksoft 记法）。
//
// 与 python/ 同题：六参数模型计算 CRC，再用「等长报文的残值之差」消掉
// init/xorout 后单独搜 poly，最后暴力搜 init。
//
// 语言差异：Python 的 int 任意精度，这里显式用 uint64 并全程 mask；
// reflect 按位循环实现（注意 Go 没有内建位反转）。
//
// 运行：go run .
package main

import "fmt"

// Model 六参数 CRC 模型。
type Model struct {
	Name                                       string
	Width                                      int
	Poly, Init, Xorout                         uint64
	RefIn, RefOut                              bool
}

func (m Model) mask() uint64 {
	if m.Width >= 64 {
		return ^uint64(0)
	}
	return (uint64(1) << uint(m.Width)) - 1
}

// Reflect 按 width 位反转比特序。
func Reflect(v uint64, width int) uint64 {
	var r uint64
	for i := 0; i < width; i++ {
		if (v>>uint(i))&1 == 1 {
			r |= 1 << uint(width-1-i)
		}
	}
	return r
}

// CRC 位级实现：寄存器永远左移；refin 反射输入字节（与 init），refout 反射输出。
func CRC(m Model, data []byte) uint64 {
	mask := m.mask()
	top := uint64(1) << uint(m.Width-1)
	poly := m.Poly & mask
	reg := m.Init & mask
	if m.RefIn {
		reg = Reflect(reg, m.Width)
	}
	for _, b := range data {
		by := uint64(b)
		if m.RefIn {
			by = Reflect(by, 8)
		}
		for i := 7; i >= 0; i-- {
			bit := (by >> uint(i)) & 1
			out := ((reg >> uint(m.Width-1)) & 1) ^ bit
			reg = (reg << 1) & mask
			if out == 1 {
				reg ^= poly
			}
		}
	}
	if m.RefOut {
		reg = Reflect(reg, m.Width)
	}
	return (reg ^ m.Xorout) & mask
}

// CRC0Poly init=0 / xorout=0 / 不反射 的残值——poly 搜索的内层循环。
// 刻意不建 256 项查表：建表 2048 次移位，而一次短报文只要 8 次。
func CRC0Poly(width int, poly uint64, data []byte) uint64 {
	mask := (uint64(1) << uint(width)) - 1
	top := uint64(1) << uint(width-1)
	poly &= mask
	var reg uint64
	for _, b := range data {
		reg ^= uint64(b) << uint(width-8)
		for i := 0; i < 8; i++ {
			if reg&top != 0 {
				reg = ((reg << 1) ^ poly) & mask
			} else {
				reg = (reg << 1) & mask
			}
		}
	}
	return reg
}

// SearchPolys 用等长报文对的残值之差筛 poly（init/xorout 在等长时抵消）。
func SearchPolys(pairs [][2][]byte, vals []uint64, width int, refin, refout bool) []uint64 {
	byLen := map[int][]int{}
	for i, p := range pairs {
		byLen[len(p[0])] = append(byLen[len(p[0])], i)
	}
	bestLen := 1 << 30
	for ln, ix := range byLen {
		if len(ix) >= 2 && ln < bestLen {
			bestLen = ln
		}
	}
	if bestLen == 1<<30 {
		return nil
	}
	var idxs []int
	for _, ix := range byLen {
		if len(ix) >= 2 && len(pairs[ix[0]][0]) == bestLen {
			idxs = ix
			break
		}
	}
	a, b := idxs[0], idxs[1]
	ma, mb := pairs[a][0], pairs[b][0]
	if refin {
		ra := make([]byte, len(ma))
		rb := make([]byte, len(mb))
		for i := range ma {
			ra[i] = byte(Reflect(uint64(ma[i]), 8))
		}
		for i := range mb {
			rb[i] = byte(Reflect(uint64(mb[i]), 8))
		}
		ma, mb = ra, rb
	}
	target := vals[a] ^ vals[b]
	if refout {
		target = Reflect(target, width)
	}
	out := []uint64{}
	for p := uint64(0); p < uint64(1)<<uint(width); p++ {
		if CRC0Poly(width, p, ma)^CRC0Poly(width, p, mb) == target {
			out = append(out, p)
		}
	}
	return out
}

// SolveInitXorout poly 已定，搜 init；xorout 由首条反解，第二条剪枝。
func SolveInitXorout(poly uint64, width int, refin, refout bool,
	pairs [][2][]byte, vals []uint64) []Model {
	out := []Model{}
	probe := Model{Width: width, Poly: poly, RefIn: refin, RefOut: refout}
	for init := uint64(0); init < uint64(1)<<uint(width); init++ {
		probe.Init = init
		xorout := CRC(probe, pairs[0][0]) ^ vals[0]
		if len(pairs) > 1 && CRC(probe, pairs[1][0])^xorout != vals[1] {
			continue
		}
		cand := Model{Width: width, Poly: poly, Init: init, Xorout: xorout,
			RefIn: refin, RefOut: refout}
		good := true
		for i := range pairs {
			if CRC(cand, pairs[i][0]) != vals[i] {
				good = false
				break
			}
		}
		if good {
			out = append(out, cand)
		}
	}
	return out
}

func main() {
	// CRC-8/SMBUS：width=8 poly=0x07 init=0 refin=false refout=false xorout=0
	truth := Model{Name: "CRC-8/SMBUS", Width: 8, Poly: 0x07}
	msgs := [][]byte{
		{0x01, 0x02, 0x03}, {0xaa, 0xbb, 0xcc}, {0x10, 0x20},
		{0xff, 0x00, 0x7f}, {0x12, 0x34, 0x56},
	}
	vals := make([]uint64, len(msgs))
	pairs := make([][2][]byte, len(msgs))
	for i, m := range msgs {
		vals[i] = CRC(truth, m)
		pairs[i] = [2][]byte{m, nil}
	}
	fmt.Printf("CRC-8/SMBUS check(\"123456789\") = 0x%02x (官方 0xf4)\n",
		CRC(truth, []byte("123456789")))

	found := 0
	for _, refin := range []bool{false, true} {
		for _, refout := range []bool{false, true} {
			for _, p := range SearchPolys(pairs, vals, 8, refin, refout) {
				sols := SolveInitXorout(p, 8, refin, refout, pairs, vals)
				for _, s := range sols {
					found++
					fmt.Printf("  解: poly=0x%02x init=0x%02x refin=%v refout=%v xorout=0x%02x\n",
						s.Poly, s.Init, s.RefIn, s.RefOut, s.Xorout)
				}
			}
		}
	}
	fmt.Println("共解出参数组:", found)

	// CRC-32/ISO-HDLC 与已知真值对拍
	c32 := Model{Name: "CRC-32/ISO-HDLC", Width: 32, Poly: 0x04C11DB7,
		Init: 0xFFFFFFFF, RefIn: true, RefOut: true, Xorout: 0xFFFFFFFF}
	fmt.Printf("CRC-32(\"123456789\") = 0x%08x (官方 0xcbf43926)\n",
		CRC(c32, []byte("123456789")))
}
