// DWARF5 相关的最小 Go 侧实现:LEB128、initial length、行号特殊操作码换算。
//
// 数据来源(本轮实测下载并提取正文):
//
//	DWARF Debugging Information Format Version 5 (February 13, 2017)
//	§7.4 Initial Length Object Representation
//	§6.2.5.1 Special Opcodes
//	https://dwarfstd.org/doc/DWARF5.pdf
//
// 与 dwarf_parse.py / dwarf_line.py 是同一份模型的另一种写法。Go 侧只保留
// 「不依赖任何反射与运行时」的那部分纯解码逻辑,便于在没有 Python 的环境里核对。
package main

import (
	"encoding/binary"
	"fmt"
)

// ---- LEB128 (§7.6) ----

// Uleb128Decode 返回 (值, 消耗的字节数)。ULEB 每条字节低 7 位有效,
// 最高位为 continuation bit;字节序是 little-endian。
func Uleb128Decode(data []byte, off int) (uint64, int) {
	var result uint64
	var shift uint
	for {
		b := data[off]
		off++
		result |= uint64(b&0x7f) << shift
		if b&0x80 == 0 {
			return result, off
		}
		shift += 7
	}
}

// Sleb128Decode 与 ULEB 的差别只在最后一位:
// 终止字节的第 6 位(0x40)为 1 表示负数,要把高位补足。
func Sleb128Decode(data []byte, off int) (int64, int) {
	var result int64
	var shift uint
	for {
		b := data[off]
		off++
		result |= int64(b&0x7f) << shift
		shift += 7
		if b&0x80 == 0 {
			if b&0x40 != 0 {
				result -= int64(1) << shift
			}
			return result, off
		}
	}
}

// Uleb128Encode 是 Uleb128Decode 的逆操作。
func Uleb128Encode(value uint64) []byte {
	var out []byte
	for {
		b := byte(value & 0x7f)
		value >>= 7
		if value != 0 {
			out = append(out, b|0x80)
		} else {
			out = append(out, b)
			return out
		}
	}
}

// ---- initial length (§7.4) ----

const dwarf64Marker = 0xffffffff

// ReadInitialLength 返回 (长度, 偏移字段宽度, 新偏移)。
// 32 位 DWARF 只占 4 字节;等于 0xffffffff 时是 64 位 DWARF,真长度在后 8 字节。
func ReadInitialLength(data []byte, off int) (uint64, int, int) {
	first := binary.LittleEndian.Uint32(data[off:])
	if uint64(first) != dwarf64Marker {
		return uint64(first), 4, off + 4
	}
	length := binary.LittleEndian.Uint64(data[off+4:])
	return length, 8, off + 12
}

// ---- line number program header ----

// LineHeader 是 §6.2.4 里与特殊操作码换算相关的那几个字段。
type LineHeader struct {
	MinInstructionLength uint8
	MaxOpsPerInstruction uint8
	DefaultIsStmt        uint8
	LineBase             int8 // sbyte
	LineRange            uint8
	OpcodeBase           uint8
}

// DecodeSpecial 按 §6.2.5.1 的公式拆一个特殊操作码:
//
//	adjusted opcode    = opcode - opcode_base
//	operation advance  = adjusted opcode / line_range
//	line increment     = line_base + (adjusted opcode % line_range)
func (h LineHeader) DecodeSpecial(opcode uint8) (uint64, int64) {
	adjusted := uint64(opcode) - uint64(h.OpcodeBase)
	opAdvance := adjusted / uint64(h.LineRange)
	lineIncrement := int64(h.LineBase) + int64(adjusted%uint64(h.LineRange))
	return opAdvance, lineIncrement
}

// EncodeSpecial 是 DecodeSpecial 的逆;over 为 true 表示该用量必须用标准操作码。
func (h LineHeader) EncodeSpecial(lineIncrement int64, opAdvance uint64) (uint8, bool) {
	v := (lineIncrement - int64(h.LineBase)) +
		int64(uint64(h.LineRange)*opAdvance) + int64(h.OpcodeBase)
	if v < int64(h.OpcodeBase) || v > 255 {
		return 0, true
	}
	return uint8(v), false
}

// AdvanceOp 按 §6.2.5.1 推进 address / op_index。
// 当 MaxOpsPerInstruction == 1 时 opIndex 恒为 0,公式退化到 DWARF v3 及以前。
func (h LineHeader) AdvanceOp(address, opIndex, opAdvance uint64) (uint64, uint64) {
	total := opIndex + opAdvance
	address += uint64(h.MinInstructionLength) *
		(total / uint64(h.MaxOpsPerInstruction))
	opIndex = total % uint64(h.MaxOpsPerInstruction)
	return address, opIndex
}

func main() {
	fmt.Println("-- ULEB128 / SLEB128 --")
	for _, v := range []uint64{0, 1, 127, 128, 0x3fff, 625485} {
		blob := Uleb128Encode(v)
		got, n := Uleb128Decode(blob, 0)
		fmt.Printf("  ULEB %-7d -> % X (消耗 %d 字节, round-trip=%v)\n",
			v, blob, n, got == v)
	}
	for _, v := range []int64{0, -1, 1, -64, 64, -8193} {
		var blob []byte
		// 直接用 Python 侧同款编码规则:SLEB 的终止条件是「再右移一位后
		// 全为符号位且已输出字节的第 6 位与符号一致」
		value := v
		for {
			b := byte(value & 0x7f)
			value >>= 7
			done := (value == 0 && b&0x40 == 0) || (value == -1 && b&0x40 != 0)
			if !done {
				b |= 0x80
			}
			blob = append(blob, b)
			if done {
				break
			}
		}
		got, n := Sleb128Decode(blob, 0)
		fmt.Printf("  SLEB %-7d -> % X (消耗 %d 字节, round-trip=%v)\n",
			v, blob, n, got == v)
	}

	fmt.Println("-- initial length --")
	buf := make([]byte, 16)
	binary.LittleEndian.PutUint32(buf[0:], 0x120)
	length, width, off := ReadInitialLength(buf, 0)
	fmt.Printf("  32 位:len=%#x offset_size=%d new_off=%d\n", length, width, off)
	binary.LittleEndian.PutUint32(buf[0:], dwarf64Marker)
	binary.LittleEndian.PutUint64(buf[4:], 0x1234)
	length, width, off = ReadInitialLength(buf, 0)
	fmt.Printf("  64 位:len=%#x offset_size=%d new_off=%d\n", length, width, off)

	fmt.Println("-- 特殊操作码换算(line_base=-5, line_range=12, opcode_base=13) --")
	h := LineHeader{
		MinInstructionLength: 1, MaxOpsPerInstruction: 1,
		DefaultIsStmt: 1, LineBase: -5, LineRange: 12, OpcodeBase: 13,
	}
	for _, op := range []uint8{13, 31, 100, 255} {
		opAdv, lineInc := h.DecodeSpecial(op)
		back, over := h.EncodeSpecial(lineInc, opAdv)
		fmt.Printf("  opcode=%-4d -> opAdv=%-3d Δline=%-3d 逆编码=%d(溢出=%v, 可逆=%v)\n",
			op, opAdv, lineInc, back, over, !over && back == op)
	}
	// 超出 255 的用量必须改用标准操作码
	_, over := h.EncodeSpecial(100, 100)
	fmt.Println("  Δline=100/opAdv=100 是否溢出(须改用标准操作码):", over)

	fmt.Println("-- op_index 进位(max_ops=2, min_inst_len=4) --")
	h2 := h
	h2.MinInstructionLength, h2.MaxOpsPerInstruction = 4, 2
	a, oi := h2.AdvanceOp(0, 0, 3)
	fmt.Printf("  advance 3 -> address=%d op_index=%d\n", a, oi)
}
