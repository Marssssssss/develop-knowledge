// macho_parser.go —— Mach-O（macOS / iOS）格式解析器
//
// 与 macho_parser.py 同题异构：同样的字段解析，用 Go 的 struct/二进制读取
// 重写一遍，突出"按 cmdsize 步进时必须校验 8 字节对齐"这一关键约束。
// 常量表、数据结构与合成样本构造在 macho_defs.go（同 package main）。
//
// 用法：
//   go run macho_parser.go macho_defs.go             # 解析内置合成样本
//   go run macho_parser.go macho_defs.go /usr/bin/ls # 解析真实 Mach-O（需 macOS）
//
// 参考：OSX ABI Mach-O File Format Reference (aidansteele)
package main

import (
	"encoding/binary"
	"errors"
	"fmt"
	"os"
)

// --------------------------------------------------------------- 解析

func parseHeader(d []byte) (Header, error) {
	var h Header
	if len(d) < headerSize {
		return h, errors.New("文件太小，装不下 mach_header_64")
	}
	h.Magic = binary.LittleEndian.Uint32(d[0:])
	if h.Magic == magicCigam {
		return h, errors.New("检测到大端 Mach-O（MH_CIGAM_64），本 demo 不做字节序翻转")
	}
	if h.Magic != magic64 {
		return h, fmt.Errorf("不是 64 位 Mach-O：magic=0x%08x", h.Magic)
	}
	h.Cputype = binary.LittleEndian.Uint32(d[4:])
	h.Cpusubtype = binary.LittleEndian.Uint32(d[8:])
	h.Filetype = binary.LittleEndian.Uint32(d[12:])
	h.Ncmds = binary.LittleEndian.Uint32(d[16:])
	h.Sizeofcmds = binary.LittleEndian.Uint32(d[20:])
	h.Flags = binary.LittleEndian.Uint32(d[24:])
	h.Reserved = binary.LittleEndian.Uint32(d[28:])
	return h, nil
}

func parseSegment(body []byte) (Segment, error) {
	var s Segment
	s.Segname = cstr16(body[8:24])
	s.Vmaddr = binary.LittleEndian.Uint64(body[24:])
	s.Vmsize = binary.LittleEndian.Uint64(body[32:])
	s.Fileoff = binary.LittleEndian.Uint64(body[40:])
	s.Filesize = binary.LittleEndian.Uint64(body[48:])
	s.Maxprot = int32(binary.LittleEndian.Uint32(body[56:]))
	s.Initprot = int32(binary.LittleEndian.Uint32(body[60:]))
	nsects := binary.LittleEndian.Uint32(body[64:])
	s.Flags = binary.LittleEndian.Uint32(body[68:])

	expect := segCmd64Len + int(nsects)*sect64Len
	if len(body) != expect {
		return s, fmt.Errorf("%s: cmdsize 与 nsects 不符（期望 %d，实得 %d）",
			s.Segname, expect, len(body))
	}

	off := segCmd64Len
	for i := 0; i < int(nsects); i++ {
		b := body[off : off+sect64Len]
		var sec Section
		sec.Sectname = cstr16(b[0:16])
		sec.Segname = cstr16(b[16:32])
		sec.Addr = binary.LittleEndian.Uint64(b[32:])
		sec.Size = binary.LittleEndian.Uint64(b[40:])
		sec.Offset = binary.LittleEndian.Uint32(b[48:])
		sec.Align = binary.LittleEndian.Uint32(b[52:])
		sec.Nreloc = binary.LittleEndian.Uint32(b[60:])
		sec.Flags = binary.LittleEndian.Uint32(b[64:])
		s.Sections = append(s.Sections, sec)
		off += sect64Len
	}
	return s, nil
}

func parse(d []byte) (MachO, error) {
	var m MachO
	h, err := parseHeader(d)
	if err != nil {
		return m, err
	}
	m.Header = h

	off := headerSize
	secCounter := 0
	for i := 0; i < int(h.Ncmds); i++ {
		if off+8 > len(d) {
			return m, fmt.Errorf("第 %d 条 load command 越界", i)
		}
		cmd := binary.LittleEndian.Uint32(d[off:])
		cmdsize := binary.LittleEndian.Uint32(d[off+4:])
		// 64 位 Mach-O 要求 cmdsize 是 8 的倍数（README 坑 1）
		if cmdsize < 8 || cmdsize%8 != 0 {
			return m, fmt.Errorf("第 %d 条 cmdsize 非法（需 8 字节对齐）: 0x%x", i, cmdsize)
		}
		if off+int(cmdsize) > len(d) {
			return m, fmt.Errorf("第 %d 条 load command 超出文件末尾", i)
		}
		body := d[off : off+int(cmdsize)]

		switch cmd {
		case lcSegment64:
			seg, err := parseSegment(body)
			if err != nil {
				return m, err
			}
			for j := range seg.Sections { // 节编号从 1 开始，跨段连续
				secCounter++
				seg.Sections[j].Index = secCounter
			}
			m.Segments = append(m.Segments, seg)

		case lcSymtab:
			if len(body) < 24 {
				return m, errors.New("LC_SYMTAB 长度不足")
			}
			m.Symtab = []uint32{
				binary.LittleEndian.Uint32(body[8:]),
				binary.LittleEndian.Uint32(body[12:]),
				binary.LittleEndian.Uint32(body[16:]),
				binary.LittleEndian.Uint32(body[20:]),
			}

		case lcLoadDylib:
			if len(body) < 24 {
				return m, errors.New("LC_LOAD_DYLIB 长度不足")
			}
			nameOff := binary.LittleEndian.Uint32(body[8:])
			// union lc_str 存的是相对本命令起点的偏移（README 坑 6）
			name := "<非法 lc_str 偏移>"
			if int(nameOff) < len(body) {
				name = cstr16(body[nameOff:])
			}
			m.Dylibs = append(m.Dylibs, Dylib{
				Ordinal: len(m.Dylibs) + 1,
				Name:    name,
				Current: ver(binary.LittleEndian.Uint32(body[16:])),
				Compat:  ver(binary.LittleEndian.Uint32(body[20:])),
			})

		default:
			m.Others = append(m.Others, cmd)
		}

		off += int(cmdsize)
	}

	if off-headerSize != int(h.Sizeofcmds) {
		return m, fmt.Errorf("sizeofcmds 与实际不符：声明 %d，实测 %d",
			h.Sizeofcmds, off-headerSize)
	}
	return m, nil
}

// ----------------------------------------------------------------- 展示

func dump(m MachO) {
	h := m.Header
	fmt.Println("=== mach_header_64 ===")
	fmt.Printf("  magic      = 0x%08x  (MH_MAGIC_64)\n", h.Magic)
	fmt.Printf("  cputype    = 0x%x   cpusubtype = 0x%x\n", h.Cputype, h.Cpusubtype)
	ft := fileTypes[h.Filetype]
	if ft == "" {
		ft = fmt.Sprintf("type(0x%x)", h.Filetype)
	}
	fmt.Printf("  filetype   = %s (%d)\n", ft, h.Filetype)
	fmt.Printf("  ncmds      = %d   sizeofcmds = %d 字节\n", h.Ncmds, h.Sizeofcmds)
	fmt.Printf("  flags      = 0x%08x\n", h.Flags)

	fmt.Println("\n=== 段（LC_SEGMENT_64）===")
	fmt.Printf("  %-12s %12s %10s %9s %10s  %-12s %s\n",
		"segname", "vmaddr", "vmsize", "fileoff", "filesize", "prot(i/m)", "sections")
	for _, s := range m.Segments {
		fmt.Printf("  %-12s 0x%09x 0x%08x 0x%07x 0x%08x  %-12s %d\n",
			s.Segname, s.Vmaddr, s.Vmsize, s.Fileoff, s.Filesize, s.Prot(), len(s.Sections))
	}

	fmt.Println("\n=== 节（section_64）===")
	fmt.Printf("  %2s %-18s %-10s %12s %7s %8s %7s  %s\n",
		"#", "sectname", "segname", "addr", "size", "offset", "align", "类型/属性")
	for _, s := range m.Segments {
		for _, sec := range s.Sections {
			attr := ""
			if sec.PureInstructions() {
				attr = " | PURE_INSTRUCTIONS"
			}
			fmt.Printf("  %2d %-18s %-10s 0x%09x 0x%05x 0x%06x %6dB  %s%s\n",
				sec.Index, sec.Sectname, sec.Segname, sec.Addr, sec.Size,
				sec.Offset, 1<<sec.Align, sec.TypeName(), attr)
		}
	}

	fmt.Println("\n=== 依赖库（LC_LOAD_DYLIB，顺序即库序号）===")
	for _, d := range m.Dylibs {
		fmt.Printf("  [%d] %s  cur=%s compat=%s\n", d.Ordinal, d.Name, d.Current, d.Compat)
	}

	fmt.Println("\n=== 符号表（LC_SYMTAB）===")
	if len(m.Symtab) == 4 {
		fmt.Printf("  symoff=0x%x nsyms=%d stroff=0x%x strsize=0x%x\n",
			m.Symtab[0], m.Symtab[1], m.Symtab[2], m.Symtab[3])
		fmt.Println("  → nlist_64 数组，每项 16 字节")
	}

	fmt.Println("\n=== 解析自检 ===")
	nsec := 0
	for _, s := range m.Segments {
		nsec += len(s.Sections)
	}
	fmt.Printf("  段数=%d 节数=%d 依赖库=%d\n", len(m.Segments), nsec, len(m.Dylibs))
	for _, s := range m.Segments {
		if s.Segname == "__PAGEZERO" {
			fmt.Printf("  __PAGEZERO: vmsize=0x%x 而 filesize=%d → 虚拟占位不占磁盘\n",
				s.Vmsize, s.Filesize)
		}
	}
	fmt.Println("  结论：Mach-O 用「自描述 load_command 数组」描述布局，")
	fmt.Println("        64 位下 cmdsize 必须 8 字节对齐，节编号从 1 起跨段连续。")
}

func main() {
	var (
		data []byte
		err  error
	)
	if len(os.Args) > 1 {
		data, err = os.ReadFile(os.Args[1])
		if err != nil {
			fmt.Fprintf(os.Stderr, "读取失败: %v\n", err)
			os.Exit(1)
		}
		fmt.Printf("解析文件：%s（%d 字节）\n\n", os.Args[1], len(data))
	} else {
		data = buildSample()
		fmt.Printf("解析内置合成样本（%d 字节，模拟 MH_EXECUTE 布局）\n\n", len(data))
	}

	m, err := parse(data)
	if err != nil {
		fmt.Fprintf(os.Stderr, "解析失败: %v\n", err)
		os.Exit(1)
	}
	dump(m)
}
