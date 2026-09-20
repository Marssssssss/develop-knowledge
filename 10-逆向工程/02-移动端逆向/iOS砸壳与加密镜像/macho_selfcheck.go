// macho_selfcheck.go — 与 selfcheck_dump.py 同判据的 Go 自检入口。
package main

import (
	"encoding/binary"
	"fmt"
)

var passed int

func check(cond bool, label string) {
	if !cond {
		panic("FAILED: " + label)
	}
	passed++
}

func buildSample() []byte {
	buf := make([]byte, 0x8000)
	textOff := uint64(0x4000)
	cryptOff := uint32(textOff + 0x1000)
	for i := uint64(0); i < 0x2000; i++ {
		if i < 0x1000 {
			buf[textOff+i] = 0x41
		} else {
			buf[textOff+i] = 0xee
		}
	}
	binary.LittleEndian.PutUint32(buf[0:4], mhMagic64)
	binary.LittleEndian.PutUint32(buf[12:16], mhExecute)
	binary.LittleEndian.PutUint32(buf[16:20], 2) // ncmds
	binary.LittleEndian.PutUint32(buf[20:24], segment64Size+encryptionInfo64Size)
	binary.LittleEndian.PutUint32(buf[24:28], mhPIE)

	off := header64Size
	binary.LittleEndian.PutUint32(buf[off:off+4], lcSegment64)
	binary.LittleEndian.PutUint32(buf[off+4:off+8], segment64Size)
	copy(buf[off+8:off+24], []byte("__TEXT"))
	binary.LittleEndian.PutUint64(buf[off+24:off+32], 0x100000000)
	binary.LittleEndian.PutUint64(buf[off+32:off+40], 0x2000) // vmsize
	binary.LittleEndian.PutUint64(buf[off+40:off+48], textOff)
	binary.LittleEndian.PutUint64(buf[off+48:off+56], 0x2000) // filesize
	off += segment64Size

	binary.LittleEndian.PutUint32(buf[off:off+4], lcEncryptionInfo64)
	binary.LittleEndian.PutUint32(buf[off+4:off+8], encryptionInfo64Size)
	binary.LittleEndian.PutUint32(buf[off+8:off+12], cryptOff)
	binary.LittleEndian.PutUint32(buf[off+12:off+16], 0x1000)
	binary.LittleEndian.PutUint32(buf[off+16:off+20], 1) // cryptid
	_ = off
	return buf
}

func selfcheck() {
	// 常量
	check(mhMagic64 == 0xfeedfacf, "MH_MAGIC_64")
	check(mhCigam64 == 0xcffaedfe, "MH_CIGAM_64")
	check(lcSegment64 == 0x19 && lcUUID == 0x1b, "LC_SEGMENT_64 / LC_UUID")
	check(lcEncryptionInfo == 0x21 && lcEncryptionInfo64 == 0x2c, "两个加密命令")
	check(mhExecute == 0x2 && mhPIE == 0x200000, "MH_EXECUTE / MH_PIE")
	check(mhDylibInCache == 0x80000000, "MH_DYLIB_IN_CACHE")

	// 结构体大小
	check(header64Size == 32, "mach_header_64 = 32")
	check(segment64Size == 72, "segment_command_64 = 72")
	check(encryptionInfoSize == 20 && encryptionInfo64Size == 24, "两个加密命令的大小")
	check(encryptionInfo64Size%8 == 0, "pad 让大小成为 8 的倍数")
	check(cryptIDOffset == 16, "cryptid 在命令内偏移 16")

	// 解析样本
	buf := buildSample()
	h, err := parseHeader64(buf)
	check(err == nil, "header 解析无错")
	check(h.filetype == mhExecute && h.ncmds == 2, "filetype 与 ncmds")
	check(h.sizeofcmds == segment64Size+encryptionInfo64Size, "sizeofcmds")
	check(h.flags&mhPIE != 0, "MH_PIE 置位")
	segs, crypt, err := parseCommands(buf, h)
	check(err == nil, "load commands 解析无错")
	check(len(segs) == 1 && segs[0].name == "__TEXT", "解析出 __TEXT")
	check(isEncrypted(crypt), "cryptid 非 0 判定为已加密")

	// dump 计划：文件偏移 -> 虚拟地址
	plan, err := makeDumpPlan(segs, crypt)
	check(err == nil, "dump 计划生成")
	check(plan.segment == "__TEXT", "加密区间落在 __TEXT")
	check(plan.vmaddr == 0x100001000, "vmaddr 按段内偏移换算")

	// 执行砸壳
	memory := make([]byte, 0x3000)
	for i := 0x1000; i < 0x2000; i++ {
		memory[i] = 0x42
	}
	check(buf[crypt.cryptoff] == 0xee, "砸壳前是密文标记")
	applyDump(buf, memory, segs[0].vmaddr, plan)
	check(buf[crypt.cryptoff] == 0x42, "砸壳后变成内存里的明文")
	check(buf[0x4000] == 0x41, "未加密区保持原样")

	patchCryptID(buf, crypt)
	check(crypt.cryptid == 0 && !isEncrypted(crypt), "patch 后 cryptid 为 0")
	// 重新解析确认改动落到字节流
	h2, _ := parseHeader64(buf)
	_, crypt2, _ := parseCommands(buf, h2)
	check(!isEncrypted(crypt2), "重新解析后仍是未加密")
	check(crypt2.cryptoff == crypt.cryptoff, "cryptoff 未被改动")

	// 页对齐
	s, n := pageAlignedRange(0x5000, 0x1000, 0x1000)
	check(s == 0x5000 && n == 0x1000, "整页对齐")
	s, n = pageAlignedRange(0x5000, 0x800, 0x1000)
	check(s == 0x5000 && n == 0x1000, "不足一页时向上取整")
	s, n = pageAlignedRange(0x5800, 0x100, 0x1000)
	check(s == 0x5000 && n == 0x1000, "跨页时向前扩到页首")

	// 异常路径
	bad := make([]byte, 64)
	if _, err := parseHeader64(bad); err == nil {
		panic("FAILED: magic 不对应报错")
	}
	passed++

	fmt.Printf("PASS %d 项断言全部通过\n", passed)
}
