// macho_defs.go —— Mach-O 格式常量、数据结构与合成样本构造
//
// 自 macho_parser.go 拆出（OPTIMIZATION.md §1.1「单源代码文件 ≤ 300 行」）：
// 本文件只放静态定义（常量表 / struct / 合成样本生成器），
// 解析逻辑与 CLI 在 macho_parser.go，两者同属 package main。
//
// 参考：OSX ABI Mach-O File Format Reference (aidansteele)
package main

import (
	"encoding/binary"
	"fmt"
	"strings"
)

const (
	magic64     uint32 = 0xFEEDFACF
	magicCigam  uint32 = 0xCFFAEDFE
	headerSize  = 32
	segCmd64Len = 72
	sect64Len   = 80
)

const (
	lcSegment64  uint32 = 0x19
	lcSymtab     uint32 = 0x02
	lcLoadDylib  uint32 = 0x0C
	lcUUID       uint32 = 0x1B
	cpuTypeX8664 uint32 = 0x01000007
)

var fileTypes = map[uint32]string{
	0x1: "MH_OBJECT", 0x2: "MH_EXECUTE", 0x4: "MH_CORE",
	0x6: "MH_DYLIB", 0x7: "MH_DYLINKER", 0x8: "MH_BUNDLE", 0xA: "MH_DSYM",
}

var sectionTypes = map[uint32]string{
	0x00: "S_REGULAR", 0x01: "S_ZEROFILL", 0x02: "S_CSTRING_LITERALS",
	0x06: "S_NON_LAZY_SYMBOL_POINTERS", 0x07: "S_LAZY_SYMBOL_POINTERS",
	0x08: "S_SYMBOL_STUBS", 0x09: "S_MOD_INIT_FUNC_POINTERS",
}

const sAttrPureInstructions uint32 = 0x80000000

// ------------------------------------------------------------- 数据结构

type Header struct {
	Magic      uint32
	Cputype    uint32
	Cpusubtype uint32
	Filetype   uint32
	Ncmds      uint32
	Sizeofcmds uint32
	Flags      uint32
	Reserved   uint32
}

type Section struct {
	Index    int
	Sectname string
	Segname  string
	Addr     uint64
	Size     uint64
	Offset   uint32
	Align    uint32
	Nreloc   uint32
	Flags    uint32
}

func (s Section) TypeName() string {
	t, ok := sectionTypes[s.Flags&0xFF]
	if !ok {
		return fmt.Sprintf("type(0x%x)", s.Flags&0xFF)
	}
	return t
}

func (s Section) PureInstructions() bool { return s.Flags&sAttrPureInstructions != 0 }

type Segment struct {
	Segname  string
	Vmaddr   uint64
	Vmsize   uint64
	Fileoff  uint64
	Filesize uint64
	Maxprot  int32
	Initprot int32
	Flags    uint32
	Sections []Section
}

func (s Segment) Prot() string {
	one := func(p int32) string {
		var b strings.Builder
		if p&1 != 0 {
			b.WriteByte('R')
		}
		if p&2 != 0 {
			b.WriteByte('W')
		}
		if p&4 != 0 {
			b.WriteByte('X')
		}
		if b.Len() == 0 {
			return "-"
		}
		return b.String()
	}
	return one(s.Initprot) + "/" + one(s.Maxprot)
}

type Dylib struct {
	Ordinal int
	Name    string
	Current string
	Compat  string
}

type MachO struct {
	Header   Header
	Segments []Segment
	Dylibs   []Dylib
	Symtab   []uint32 // symoff, nsyms, stroff, strsize
	Others   []uint32 // cmd 值
}

// --------------------------------------------------------------- 工具

// cstr16：固定 16 字节名 → 字符串；短名才补 '\0'，必须强制作终止
func cstr16(b []byte) string {
	if i := indexByte(b, 0); i >= 0 {
		return string(b[:i])
	}
	return string(b)
}

func indexByte(b []byte, c byte) int {
	for i := range b {
		if b[i] == c {
			return i
		}
	}
	return -1
}

func ver(v uint32) string {
	return fmt.Sprintf("%d.%d.%d", (v>>16)&0xFFFF, (v>>8)&0xFF, v&0xFF)
}

// ------------------------------------------------------- 构造合成样本

func buildSample() []byte {
	// 段构造：section 用 (sectname, segname, addr, size, off, align, flags)
	type sec = struct {
		name, seg         string
		addr, size        uint64
		off, align, flags uint32
	}

	segCmd := func(name string, vmaddr, vmsize, fileoff, filesize uint64,
		maxprot, initprot int32, secs []sec) []byte {
		cmdsize := segCmd64Len + len(secs)*sect64Len
		out := make([]byte, cmdsize)
		binary.LittleEndian.PutUint32(out[0:], lcSegment64)
		binary.LittleEndian.PutUint32(out[4:], uint32(cmdsize))
		copy(out[8:24], name)
		binary.LittleEndian.PutUint64(out[24:], vmaddr)
		binary.LittleEndian.PutUint64(out[32:], vmsize)
		binary.LittleEndian.PutUint64(out[40:], fileoff)
		binary.LittleEndian.PutUint64(out[48:], filesize)
		binary.LittleEndian.PutUint32(out[56:], uint32(maxprot))
		binary.LittleEndian.PutUint32(out[60:], uint32(initprot))
		binary.LittleEndian.PutUint32(out[64:], uint32(len(secs)))
		p := segCmd64Len
		for _, s := range secs {
			copy(out[p:p+16], s.name)
			copy(out[p+16:p+32], s.seg)
			binary.LittleEndian.PutUint64(out[p+32:], s.addr)
			binary.LittleEndian.PutUint64(out[p+40:], s.size)
			binary.LittleEndian.PutUint32(out[p+48:], s.off)
			binary.LittleEndian.PutUint32(out[p+52:], s.align)
			binary.LittleEndian.PutUint32(out[p+64:], s.flags)
			p += sect64Len
		}
		return out
	}

	dylibCmd := func(name string) []byte {
		body := 24 + len(name) + 1
		pad := (8 - body%8) % 8
		out := make([]byte, body+pad)
		binary.LittleEndian.PutUint32(out[0:], lcLoadDylib)
		binary.LittleEndian.PutUint32(out[4:], uint32(len(out)))
		binary.LittleEndian.PutUint32(out[8:], 24) // lc_str 偏移
		binary.LittleEndian.PutUint32(out[16:], 0x00010E00)
		binary.LittleEndian.PutUint32(out[20:], 0x00010000)
		copy(out[24:], name)
		return out
	}

	cmds := [][]byte{
		segCmd("__PAGEZERO", 0, 0x100000000, 0, 0, 0, 0, nil),
		segCmd("__TEXT", 0x100000000, 0x1000, 0, 0x1000, 7, 5, []sec{
			{"__text", "__TEXT", 0x100000F00, 0x30, 0xF00, 4, sAttrPureInstructions},
			{"__cstring", "__TEXT", 0x100000F30, 0x0C, 0xF30, 0, 0x02},
		}),
		segCmd("__DATA", 0x100001000, 0x1000, 0x1000, 0x1000, 7, 3, []sec{
			{"__data", "__DATA", 0x100001000, 0x10, 0x1000, 3, 0x00},
			{"__bss", "__DATA", 0x100001010, 0x20, 0x0, 3, 0x01},
		}),
		segCmd("__LINKEDIT", 0x100002000, 0x1000, 0x2000, 0x200, 7, 1, nil),
		func() []byte {
			b := make([]byte, 24)
			binary.LittleEndian.PutUint32(b[0:], lcSymtab)
			binary.LittleEndian.PutUint32(b[4:], 24)
			binary.LittleEndian.PutUint32(b[8:], 0x2000)
			binary.LittleEndian.PutUint32(b[12:], 2)
			binary.LittleEndian.PutUint32(b[16:], 0x2018)
			binary.LittleEndian.PutUint32(b[20:], 0x40)
			return b
		}(),
		dylibCmd("/usr/lib/libSystem.B.dylib"),
		func() []byte {
			b := make([]byte, 24)
			binary.LittleEndian.PutUint32(b[0:], lcUUID)
			binary.LittleEndian.PutUint32(b[4:], 24)
			return b
		}(),
	}

	total := 0
	for _, c := range cmds {
		total += len(c)
	}

	blob := make([]byte, headerSize, 0x2200)
	binary.LittleEndian.PutUint32(blob[0:], magic64)
	binary.LittleEndian.PutUint32(blob[4:], cpuTypeX8664)
	binary.LittleEndian.PutUint32(blob[8:], 0x80000003)
	binary.LittleEndian.PutUint32(blob[12:], 0x2) // MH_EXECUTE
	binary.LittleEndian.PutUint32(blob[16:], uint32(len(cmds)))
	binary.LittleEndian.PutUint32(blob[20:], uint32(total))
	binary.LittleEndian.PutUint32(blob[24:], 0x00200085)
	for _, c := range cmds {
		blob = append(blob, c...)
	}
	for len(blob) < 0x2200 {
		blob = append(blob, 0)
	}
	return blob
}
