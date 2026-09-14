// bpf_insn.go — eBPF 指令编码与汇编小工具(Go 版)
//
// 布局:8 字节定长,小端 = [opcode][src<<4|dst][offset:16][imm:32]
//   ALU/JMP 类:code 占 bit 4-7,source 占 bit 3
//   LD/ST   类:mode 占 bit 5-7,size 占 bit 3-4
// 权威依据:docs.kernel.org/bpf/standardization/instruction-set.html
package main

import "encoding/binary"

// ---------------- 编码常量 ----------------
const (
	LD, LDX, ST, STX, ALU, JMP, JMP32, ALU64 = 0, 1, 2, 3, 4, 5, 6, 7
	BPFAdd, BPFSub, BPFMov                   = 0x0, 0x1, 0xb
	BPFJa, BPFJne, BPFCall, BPFExit          = 0x0, 0x5, 0x8, 0x9
	BPFK, BPFX                               = 0, 1 // 源操作数:立即数 / 寄存器
	BPFMem, BPFDW                            = 3, 3 // 访存 mode / size
	FP                                       = 10 // r10:只读帧指针
	StackSize                                = 512
)

// Ins 是一条 eBPF 基本指令 = 8 字节
type Ins struct {
	Opcode uint8
	Dst    uint8
	Src    uint8
	Offset int16
	Imm    int32
}

func (i Ins) Class() uint8  { return i.Opcode & 0x07 }
func (i Ins) Code() uint8   { return (i.Opcode >> 4) & 0x0f }
func (i Ins) Source() uint8 { return (i.Opcode >> 3) & 1 }

// Size 返回访存宽度(字节);LD/ST 类的 size 字段占 bit 3-4
func (i Ins) Size() int {
	switch (i.Opcode >> 3) & 0x03 {
	case 0: // BPF_W
		return 4
	case 1: // BPF_H
		return 2
	case 2: // BPF_B
		return 1
	default: // BPF_DW
		return 8
	}
}

// Encode 输出小端 8 字节
func (i Ins) Encode() [8]byte {
	var b [8]byte
	b[0] = i.Opcode
	b[1] = i.Src<<4 | (i.Dst & 0x0f)
	binary.LittleEndian.PutUint16(b[2:4], uint16(i.Offset))
	binary.LittleEndian.PutUint32(b[4:8], uint32(i.Imm))
	return b
}

func decode(b [8]byte) Ins {
	return Ins{
		Opcode: b[0],
		Dst:    b[1] & 0x0f,
		Src:    b[1] >> 4,
		Offset: int16(binary.LittleEndian.Uint16(b[2:4])),
		Imm:    int32(binary.LittleEndian.Uint32(b[4:8])),
	}
}

// ---------------- 汇编小工具 ----------------
func aluOp(class, code, source uint8) uint8 { return class | code<<4 | source<<3 }
func memOp(class, mode, size uint8) uint8   { return class | mode<<5 | size<<3 }

func movImm(dst uint8, imm int32) Ins { return Ins{aluOp(ALU64, BPFMov, BPFK), dst, 0, 0, imm} }
func movReg(dst, src uint8) Ins       { return Ins{aluOp(ALU64, BPFMov, BPFX), dst, src, 0, 0} }

func aluImm(code, dst uint8, imm int32) Ins {
	return Ins{aluOp(ALU64, code, BPFK), dst, 0, 0, imm}
}

func stxDW(ptr uint8, off int16, val uint8) Ins {
	return Ins{memOp(STX, BPFMem, BPFDW), ptr, val, off, 0}
}

func ldxDW(dst, ptr uint8, off int16) Ins {
	return Ins{memOp(LDX, BPFMem, BPFDW), dst, ptr, off, 0}
}

func jmpImm(code, dst uint8, off int16, imm int32) Ins {
	return Ins{aluOp(JMP, code, BPFK), dst, 0, off, imm}
}

func ja(off int16) Ins { return Ins{aluOp(JMP, BPFJa, BPFK), 0, 0, off, 0} }

func call(nr int32) Ins { return Ins{aluOp(JMP, BPFCall, BPFK), 0, 0, 0, nr} }

var exitIns = Ins{aluOp(JMP, BPFExit, BPFK), 0, 0, 0, 0}
