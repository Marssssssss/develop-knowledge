// macho_dump.go — Mach-O 加密段与砸壳的 Go 实现(与 macho_dump.py 同构)。
// 结构体与常量取自 xnu 源码 EXTERNAL_HEADERS/mach-o/loader.h; 本机无 go 工具链:
// 人工审查 + 括号配平校验。
package main

import (
	"encoding/binary"
	"fmt"
)

// loader.h 常量
const (
	mhMagic64           = 0xfeedfacf
	mhCigam64           = 0xcffaedfe
	lcSegment64         = 0x19
	lcUUID              = 0x1b
	lcEncryptionInfo    = 0x21
	lcEncryptionInfo64  = 0x2c
	mhExecute           = 0x2
	mhPIE               = 0x200000
	mhDylibInCache      = 0x80000000
)

// 结构体大小
const (
	header64Size          = 32 // 8 个 uint32
	segment64Size         = 72 // 4+4+16+8*4+4*4
	encryptionInfoSize    = 20 // 5 个 uint32
	encryptionInfo64Size  = 24 // 补 pad 到 8 的倍数
	cryptIDOffset         = 16 // cmd + cmdsize + cryptoff + cryptsize
)

type machHeader64 struct {
	magic      uint32
	cputype    uint32
	cpusubtype uint32
	filetype   uint32
	ncmds      uint32
	sizeofcmds uint32
	flags      uint32
	reserved   uint32
}

type segment64 struct {
	name     string
	vmaddr   uint64
	vmsize   uint64
	fileoff  uint64
	filesize uint64
}

type cryptInfo struct {
	cryptoff  uint32
	cryptsize uint32
	cryptid   uint32
	cmdOff    int
}

func parseHeader64(buf []byte) (*machHeader64, error) {
	if len(buf) < header64Size {
		return nil, fmt.Errorf("文件短于 mach_header_64 的 32 字节")
	}
	h := &machHeader64{}
	h.magic = binary.LittleEndian.Uint32(buf[0:4])
	if h.magic != mhMagic64 && h.magic != mhCigam64 {
		return nil, fmt.Errorf("magic %#x 不是 64 位 Mach-O", h.magic)
	}
	h.cputype = binary.LittleEndian.Uint32(buf[4:8])
	h.filetype = binary.LittleEndian.Uint32(buf[12:16])
	h.ncmds = binary.LittleEndian.Uint32(buf[16:20])
	h.sizeofcmds = binary.LittleEndian.Uint32(buf[20:24])
	h.flags = binary.LittleEndian.Uint32(buf[24:28])
	return h, nil
}

// parseCommands 扫 load commands, 抽出段表与加密信息
func parseCommands(buf []byte, h *machHeader64) ([]segment64, *cryptInfo, error) {
	segs := []segment64{}
	var crypt *cryptInfo
	off := header64Size
	for i := uint32(0); i < h.ncmds; i++ {
		if off+8 > len(buf) {
			return nil, nil, fmt.Errorf("load command 越界")
		}
		cmd := binary.LittleEndian.Uint32(buf[off : off+4])
		cmdsize := binary.LittleEndian.Uint32(buf[off+4 : off+8])
		if cmdsize < 8 || off+int(cmdsize) > len(buf) {
			return nil, nil, fmt.Errorf("cmdsize 非法")
		}
		switch cmd {
		case lcSegment64:
			if cmdsize < uint32(segment64Size) {
				return nil, nil, fmt.Errorf("LC_SEGMENT_64 的 cmdsize 不足 72")
			}
			name := string(buf[off+8 : off+24])
			for i := 0; i < len(name); i++ {
				if name[i] == 0 {
					name = name[:i]
					break
				}
			}
			segs = append(segs, segment64{
				name:     name,
				vmaddr:   binary.LittleEndian.Uint64(buf[off+24 : off+32]),
				vmsize:   binary.LittleEndian.Uint64(buf[off+32 : off+40]),
				fileoff:  binary.LittleEndian.Uint64(buf[off+40 : off+48]),
				filesize: binary.LittleEndian.Uint64(buf[off+48 : off+56]),
			})
		case lcEncryptionInfo, lcEncryptionInfo64:
			crypt = &cryptInfo{
				cryptoff:  binary.LittleEndian.Uint32(buf[off+8 : off+12]),
				cryptsize: binary.LittleEndian.Uint32(buf[off+12 : off+16]),
				cryptid:   binary.LittleEndian.Uint32(buf[off+16 : off+20]),
				cmdOff:    off,
			}
		}
		off += int(cmdsize)
	}
	return segs, crypt, nil
}

// isEncrypted: loader.h 原文 —— cryptid 为 0 表示尚未加密
func isEncrypted(c *cryptInfo) bool { return c != nil && c.cryptid != 0 }

// dumpPlan: cryptoff 是**文件偏移**, 内存地址要用所在段换算
type dumpPlan struct {
	segment    string
	fileOffset uint32
	size       uint32
	vmaddr     uint64
}

func makeDumpPlan(segs []segment64, c *cryptInfo) (*dumpPlan, error) {
	if c == nil {
		return nil, fmt.Errorf("没有 LC_ENCRYPTION_INFO(_64)")
	}
	for _, s := range segs {
		if uint64(c.cryptoff) >= s.fileoff && uint64(c.cryptoff) < s.fileoff+s.filesize {
			return &dumpPlan{
				segment:    s.name,
				fileOffset: c.cryptoff,
				size:       c.cryptsize,
				vmaddr:     s.vmaddr + (uint64(c.cryptoff) - s.fileoff),
			}, nil
		}
	}
	return nil, fmt.Errorf("cryptoff %#x 不落在任何段内", c.cryptoff)
}

// applyDump 把进程内存里已解密的字节写回文件区间
func applyDump(file []byte, memory []byte, imageBase uint64, p *dumpPlan) {
	startMem := p.vmaddr - imageBase
	for i := uint32(0); i < p.size; i++ {
		file[int(p.fileOffset)+int(i)] = memory[int(startMem)+int(i)]
	}
}

// patchCryptID 把 cryptid 置 0
func patchCryptID(file []byte, c *cryptInfo) {
	binary.LittleEndian.PutUint32(file[c.cmdOff+cryptIDOffset:], 0)
	c.cryptid = 0
}

// pageAlignedRange: 加密区间落在若干页内, dump 必须覆盖整个页。
// 页大小由调用方给出(不同平台不同), 这里不假定具体数值。
func pageAlignedRange(cryptoff, cryptsize, pageSize uint64) (uint64, uint64) {
	start := cryptoff - cryptoff%pageSize
	end := cryptoff + cryptsize
	if end%pageSize != 0 {
		end += pageSize - end%pageSize
	}
	return start, end - start
}

func main() { selfcheck() }
