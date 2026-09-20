// dalvik_isa.go — Dalvik 指令格式的 Go 实现(与 dalvik_isa.py / dalvik_encode.py 同构)。
// 依据 AOSP《Dalvik 指令格式》与《Dalvik 字节码》; 本机无 go 工具链: 人工审查 + 括号配平校验。
package main

import "fmt"

// 类型代码字母的位宽(官方「类型代码字母的完整列表」)
var typeLetterBits = map[byte]int{
	'b': 8, 'c': 16, 'f': 16, 'h': 16, 'i': 32, 'l': 64,
	'm': 16, 'n': 4, 's': 16, 't': 8, 'x': 0,
}

// 格式 ID -> 代码单元数
var formatUnits = map[string]int{
	"10x": 1, "12x": 1, "11n": 1, "11x": 1, "10t": 1,
	"20t": 2, "22x": 2, "21t": 2, "21s": 2, "21h": 2, "21c": 2, "23x": 2, "22b": 2,
	"22t": 2, "22s": 2, "22c": 2,
	"30t": 3, "32x": 3, "31i": 3, "31t": 3, "31c": 3, "35c": 3, "3rc": 3,
	"45cc": 4, "4rcc": 4, "51l": 5,
}

// opcode -> (助记符, 格式)
type opEntry struct {
	name string
	fmt  string
}

var opcodes = map[uint8]opEntry{
	0x00: {"nop", "10x"},
	0x01: {"move", "12x"},
	0x0A: {"move-result", "11x"},
	0x0E: {"return-void", "10x"},
	0x0F: {"return", "11x"},
	0x12: {"const/4", "11n"},
	0x13: {"const/16", "21s"},
	0x14: {"const", "31i"},
	0x15: {"const/high16", "21h"},
	0x1A: {"const-string", "21c"},
	0x1B: {"const-string/jumbo", "31c"},
	0x1C: {"const-class", "21c"},
	0x28: {"goto", "10t"},
	0x29: {"goto/16", "20t"},
	0x2A: {"goto/32", "30t"},
	0x32: {"if-eq", "22t"},
	0x33: {"if-ne", "22t"},
	0x34: {"if-lt", "22t"},
	0x35: {"if-ge", "22t"},
	0x36: {"if-gt", "22t"},
	0x37: {"if-le", "22t"},
	0x38: {"if-eqz", "21t"},
	0x39: {"if-nez", "21t"},
	0x3A: {"if-ltz", "21t"},
	0x3B: {"if-gez", "21t"},
	0x3C: {"if-gtz", "21t"},
	0x3D: {"if-lez", "21t"},
	0x6E: {"invoke-virtual", "35c"},
	0x6F: {"invoke-super", "35c"},
	0x70: {"invoke-direct", "35c"},
	0x71: {"invoke-static", "35c"},
	0x72: {"invoke-interface", "35c"},
	0x74: {"invoke-virtual/range", "3rc"},
	0x75: {"invoke-super/range", "3rc"},
	0x76: {"invoke-direct/range", "3rc"},
	0x77: {"invoke-static/range", "3rc"},
	0x78: {"invoke-interface/range", "3rc"},
	0xFA: {"invoke-polymorphic", "45cc"},
	0xFB: {"invoke-polymorphic/range", "4rcc"},
	0xFC: {"invoke-custom", "35c"},
	0xFD: {"invoke-custom/range", "3rc"},
	0xFE: {"const-method-handle", "21c"},
	0xFF: {"const-method-type", "21c"},
}

// payload 伪运算码识别值
const (
	identPackedSwitch    = 0x0100
	identSparseSwitch    = 0x0200
	identFillArrayData   = 0x0300
)

// parseFormatID: '35c' -> (3, '5', 'c'); '3rc' -> (3, 'r', 'c')
func parseFormatID(fid string) (int, byte, byte) {
	body := fid
	if len(fid) > 3 && (fid[len(fid)-1] == 's' || fid[len(fid)-1] == 'i') {
		body = fid[:len(fid)-1]
	}
	return int(body[0] - '0'), body[1], body[2]
}

func s8(v uint16) int { 
	if v&0x80 != 0 {
		return int(v) - 0x100
	}
	return int(v)
}

func s16(v uint16) int {
	if v&0x8000 != 0 {
		return int(v) - 0x10000
	}
	return int(v)
}

// decode35c: 'A|G|op BBBB F|E|D|C'
func decode35c(u []uint16) (a, g, b int, regs []int) {
	a = int((u[0] >> 12) & 0x0f)
	g = int((u[0] >> 8) & 0x0f)
	b = int(u[1])
	nibble := []int{int(u[2] & 0x0f), int((u[2] >> 4) & 0x0f), int((u[2] >> 8) & 0x0f), int((u[2] >> 12) & 0x0f)}
	// 官方 [A=N] 变体: A 决定实际使用几个寄存器, 第五个放在 G 位
	for i := 0; i < a && i < 4; i++ {
		regs = append(regs, nibble[i])
	}
	if a == 5 {
		regs = append(regs, g)
	}
	return
}

// decode3rc: 'AA|op BBBB CCCC', 官方 NNNN = CCCC + AA - 1
func decode3rc(u []uint16) (a, b, c int, regs []int) {
	a = int((u[0] >> 8) & 0xff)
	b = int(u[1])
	c = int(u[2])
	for i := 0; i < a; i++ {
		regs = append(regs, c+i)
	}
	return
}

// encode35c / encode3rc 是与 decode 互逆的装回
func encode35c(op uint8, a, g, b int, c, d, e, f int) []uint16 {
	u0 := uint16(a&0x0f)<<12 | uint16(g&0x0f)<<8 | uint16(op)
	u2 := uint16(f&0x0f)<<12 | uint16(e&0x0f)<<8 | uint16(d&0x0f)<<4 | uint16(c&0x0f)
	return []uint16{u0, uint16(b & 0xffff), u2}
}

func encode3rc(op uint8, a, b, c int) []uint16 {
	return []uint16{uint16(a&0xff)<<8 | uint16(op), uint16(b & 0xffff), uint16(c & 0xffff)}
}

// payload 代码单元数(官方三个小节各自给出的公式)
func packedSwitchUnits(size int) int              { return size*2 + 4 }
func sparseSwitchUnits(size int) int              { return size*4 + 2 }
func fillArrayDataUnits(size, width int) int      { return (size*width+1)/2 + 4 }

// branchOffsetOK: 官方注释 —— 分支偏移量不得为 0
func branchOffsetOK(offset int) bool { return offset != 0 }

func main() { selfcheck() }
