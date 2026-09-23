// Package main 复刻 Windows x64 表驱动展开：.pdata / .xdata。
//
// 规范原文：Microsoft Learn《x64 exception handling》
//
//	https://learn.microsoft.com/en-us/cpp/build/exception-handling-x64
//	UNW_FLAG_* 位值取自 mingw-w64 的 mingw-w64-headers/include/winnt.h
package main

import (
	"encoding/binary"
	"errors"
)

// UNW_FLAG_*（winnt.h）
const (
	UnwFlagNHandler  = 0x0
	UnwFlagEHandler  = 0x1
	UnwFlagUHandler  = 0x2
	UnwFlagChainInfo = 0x4
)

// UWOP_* 操作码
const (
	UwopPushNonvol    = 0
	UwopAllocLarge    = 1
	UwopAllocSmall    = 2
	UwopSetFpReg      = 3
	UwopSaveNonvol    = 4
	UwopSaveNonvolFar = 5
	UwopSaveXmm128    = 8
	UwopSaveXmm128Far = 9
	UwopPushMachFrame = 10
)

// RegNames 是操作信息字段里的整数寄存器编号表。
var RegNames = []string{
	"RAX", "RCX", "RDX", "RBX", "RSP", "RBP", "RSI", "RDI",
	"R8", "R9", "R10", "R11", "R12", "R13", "R14", "R15",
}

func regName(n int) string {
	if n >= 0 && n < len(RegNames) {
		return RegNames[n]
	}
	return "reg?"
}

// UnwindCode 是一个 USHORT 槽；作为数据槽时 16 位整体有效。
type UnwindCode struct {
	Offset int
	Op     int
	Info   int
	Word   int // 非空即为数据槽原始值
}

// Data 返回该槽的 16 位值。
func (c UnwindCode) Data() int {
	if c.Word != 0 {
		return c.Word
	}
	return c.Offset | (c.Op << 8) | (c.Info << 12)
}

// UnwindInfo 是 UNWIND_INFO 及其尾部的 handler / chained info。
type UnwindInfo struct {
	Version        int
	Flags          int
	PrologSize     int
	Codes          []UnwindCode
	FrameRegister  int
	FrameOffset    int
	HandlerRva     int
	HandlerDataLen int
	Chained        *RuntimeFunction
}

// ChainedSlot 是 chained info 的槽位：(CountOfCodes + 1) &^ 1。
func (u *UnwindInfo) ChainedSlot() int {
	return (len(u.Codes) + 1) &^ 1
}

// ToBytes 编码 UNWIND_INFO（含补齐与尾部）。
func (u *UnwindInfo) ToBytes() []byte {
	b := make([]byte, 0, 16)
	b = append(b, byte(u.Version&0x7)|byte((u.Flags&0x1F)<<3))
	b = append(b, byte(u.PrologSize))
	b = append(b, byte(len(u.Codes)))
	b = append(b, byte((u.FrameRegister&0xF)<<4)|byte(u.FrameOffset&0xF))
	for _, c := range u.Codes {
		v := make([]byte, 2)
		binary.LittleEndian.PutUint16(v, uint16(c.Data()&0xFFFF))
		b = append(b, v...)
	}
	if len(u.Codes)&1 == 1 {
		b = append(b, 0, 0)
	}
	if u.Flags&UnwFlagChainInfo != 0 && u.Chained != nil {
		b = append(b, u.Chained.ToBytes()...)
	} else if u.Flags&(UnwFlagEHandler|UnwFlagUHandler) != 0 {
		v := make([]byte, 4)
		binary.LittleEndian.PutUint32(v, uint32(u.HandlerRva))
		b = append(b, v...)
		b = append(b, make([]byte, u.HandlerDataLen)...)
	}
	return b
}

// ParseUnwindInfo 解码 UNWIND_INFO。
func ParseUnwindInfo(buf []byte) (*UnwindInfo, error) {
	if len(buf) < 4 {
		return nil, errors.New("UNWIND_INFO 至少 4 字节")
	}
	u := &UnwindInfo{
		Version:       int(buf[0] & 0x7),
		Flags:         int(buf[0]>>3) & 0x1F,
		PrologSize:    int(buf[1]),
		FrameRegister: int(buf[3]>>4) & 0xF,
		FrameOffset:   int(buf[3]) & 0xF,
	}
	n := int(buf[2])
	for i := 0; i < n; i++ {
		w := binary.LittleEndian.Uint16(buf[4+2*i:])
		u.Codes = append(u.Codes, UnwindCode{
			Offset: int(w & 0xFF), Op: int(w>>8) & 0xF, Info: int(w>>12) & 0xF, Word: int(w),
		})
	}
	tail := 4 + 2*u.ChainedSlot()
	if u.Flags&UnwFlagChainInfo != 0 {
		if tail+12 > len(buf) {
			return nil, errors.New("chained info 截断")
		}
		u.Chained = &RuntimeFunction{
			Begin:  binary.LittleEndian.Uint32(buf[tail:]),
			End:    binary.LittleEndian.Uint32(buf[tail+4:]),
			Unwind: binary.LittleEndian.Uint32(buf[tail+8:]),
		}
	} else if u.Flags&(UnwFlagEHandler|UnwFlagUHandler) != 0 {
		if tail+4 > len(buf) {
			return nil, errors.New("handler 截断")
		}
		u.HandlerRva = int(binary.LittleEndian.Uint32(buf[tail:]))
		u.HandlerDataLen = len(buf) - tail - 4
	}
	return u, nil
}

// RuntimeFunction 是 .pdata 里的表项（三个 ULONG，image relative）。
type RuntimeFunction struct {
	Begin  uint32
	End    uint32
	Unwind uint32
}

// ToBytes 编码 RUNTIME_FUNCTION。
func (r *RuntimeFunction) ToBytes() []byte {
	b := make([]byte, 12)
	binary.LittleEndian.PutUint32(b, r.Begin)
	binary.LittleEndian.PutUint32(b[4:], r.End)
	binary.LittleEndian.PutUint32(b[8:], r.Unwind)
	return b
}

// NodeSize 返回该操作码连同数据槽占用的 USHORT 数。
func NodeSize(c UnwindCode) int {
	switch c.Op {
	case UwopAllocLarge:
		if c.Info == 1 {
			return 3
		}
		return 2
	case UwopSaveNonvol, UwopSaveXmm128:
		return 2
	case UwopSaveNonvolFar, UwopSaveXmm128Far:
		return 3
	}
	return 1
}

// Nodes 按操作码节点遍历，产出槽下标（跳过数据槽）。
func Nodes(u *UnwindInfo) []int {
	out := []int{}
	for i := 0; i < len(u.Codes); {
		out = append(out, i)
		i += NodeSize(u.Codes[i])
	}
	return out
}

// Machine 是展开用的寄存器 + 栈模型。
type Machine struct {
	Regs  map[string]int
	Rip   int
	Stack map[int]int
}

// NewMachine 构造一台机器。
func NewMachine(rsp int, stack map[int]int) *Machine {
	m := &Machine{Regs: map[string]int{}, Stack: map[int]int{}}
	for _, n := range RegNames {
		m.Regs[n] = 0
	}
	m.Regs["RSP"] = rsp
	for k, v := range stack {
		m.Stack[k] = v
	}
	return m
}

func (m *Machine) load(a int) int { return m.Stack[a] }

// FrameBase 是 SAVE_* 的偏移基准：无 FP 时是 RSP，否则是 FP-16*scaled。
func FrameBase(m *Machine, u *UnwindInfo) int {
	if u.FrameRegister == 0 {
		return m.Regs["RSP"]
	}
	return m.Regs[regName(u.FrameRegister)] - 16*u.FrameOffset
}

// ApplyCode 撤销第 idx 个节点的效果，返回消耗的槽数。
func ApplyCode(m *Machine, u *UnwindInfo, idx int) int {
	c := u.Codes[idx]
	switch c.Op {
	case UwopPushNonvol:
		m.Regs[regName(c.Info)] = m.load(m.Regs["RSP"])
		m.Regs["RSP"] += 8
		return 1
	case UwopAllocLarge:
		if c.Info == 0 {
			m.Regs["RSP"] += u.Codes[idx+1].Data() * 8
			return 2
		}
		m.Regs["RSP"] += u.Codes[idx+2].Data()<<16 | u.Codes[idx+1].Data()
		return 3
	case UwopAllocSmall:
		m.Regs["RSP"] += c.Info*8 + 8
		return 1
	case UwopSetFpReg:
		m.Regs["RSP"] = m.Regs[regName(u.FrameRegister)] - 16*u.FrameOffset
		return 1
	case UwopSaveNonvol:
		m.Regs[regName(c.Info)] = m.load(FrameBase(m, u) + u.Codes[idx+1].Data()*8)
		return 2
	case UwopSaveNonvolFar:
		off := u.Codes[idx+2].Data()<<16 | u.Codes[idx+1].Data()
		m.Regs[regName(c.Info)] = m.load(FrameBase(m, u) + off)
		return 3
	case UwopSaveXmm128:
		m.Regs["xmm"] = m.load(FrameBase(m, u) + u.Codes[idx+1].Data()*16)
		return 2
	case UwopSaveXmm128Far:
		off := u.Codes[idx+2].Data()<<16 | u.Codes[idx+1].Data()
		m.Regs["xmm"] = m.load(FrameBase(m, u) + off)
		return 3
	case UwopPushMachFrame:
		m.Rip = m.load(m.Regs["RSP"])
		if c.Info == 0 {
			m.Regs["RSP"] += 40
		} else {
			m.Regs["RSP"] += 48
		}
		return 1
	}
	return 1
}

// UnwindProlog 是 Case b)：只撤销 offset <= ripOffset 的节点。
func UnwindProlog(m *Machine, u *UnwindInfo, ripOffset int) []int {
	applied := []int{}
	started := false
	for _, i := range Nodes(u) {
		if !started && u.Codes[i].Offset > ripOffset {
			continue
		}
		started = true
		ApplyCode(m, u, i)
		applied = append(applied, i)
	}
	return applied
}

// UnwindFull 是 Case c)：整段 code 数组全部撤销。
func UnwindFull(m *Machine, u *UnwindInfo) []int {
	applied := []int{}
	for _, i := range Nodes(u) {
		ApplyCode(m, u, i)
		applied = append(applied, i)
	}
	return applied
}

// UnwindLeaf 是查不到 RUNTIME_FUNCTION 时的叶函数回退。
func UnwindLeaf(m *Machine) {
	m.Rip = m.load(m.Regs["RSP"])
	m.Regs["RSP"] += 8
}
