#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macho_defs.py —— Mach-O 格式常量、数据结构与合成样本构造

自 `macho_parser.py` 拆出（OPTIMIZATION.md §1.1「单源代码文件 ≤ 300 行」）：
本模块只放**静态定义**——常量表 / dataclass / 合成样本生成器；
解析逻辑、展示与 CLI 在 `macho_parser.py`。

参考：OSX ABI Mach-O File Format Reference (aidansteele)；Apple `<mach-o/loader.h>`
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# --------------------------------------------------------------- 常量

MH_MAGIC_64 = 0xFEEDFACF      # 64 位、与宿主同字节序
MH_CIGAM_64 = 0xCFFAEDFE      # 64 位、字节序相反

CPU_TYPE_X86_64 = 0x01000007
CPU_TYPE_ARM64 = 0x0100000C

FILETYPE = {
    0x1: "MH_OBJECT", 0x2: "MH_EXECUTE", 0x3: "MH_FVMLIB", 0x4: "MH_CORE",
    0x5: "MH_PRELOAD", 0x6: "MH_DYLIB", 0x7: "MH_DYLINKER", 0x8: "MH_BUNDLE",
    0x9: "MH_DYLIB_STUB", 0xA: "MH_DSYM", 0xB: "MH_KEXT_BUNDLE",
}

LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x02
LC_LOAD_DYLIB = 0x0C
LC_ID_DYLIB = 0x0D
LC_UUID = 0x1B
LC_UNIXTHREAD = 0x05
LC_MAIN = 0x80000028        # 新式入口声明：本项目仅识别名称，不解析字段

LC_NAME = {
    LC_SEGMENT_64: "LC_SEGMENT_64", LC_SYMTAB: "LC_SYMTAB",
    LC_LOAD_DYLIB: "LC_LOAD_DYLIB", LC_ID_DYLIB: "LC_ID_DYLIB",
    LC_UUID: "LC_UUID", LC_UNIXTHREAD: "LC_UNIXTHREAD", LC_MAIN: "LC_MAIN",
}

VM_PROT = {0x1: "R", 0x2: "W", 0x4: "X"}

SECTION_TYPE = {
    0x00: "S_REGULAR", 0x01: "S_ZEROFILL", 0x02: "S_CSTRING_LITERALS",
    0x03: "S_4BYTE_LITERALS", 0x04: "S_8BYTE_LITERALS",
    0x05: "S_LITERAL_POINTERS", 0x06: "S_NON_LAZY_SYMBOL_POINTERS",
    0x07: "S_LAZY_SYMBOL_POINTERS", 0x08: "S_SYMBOL_STUBS",
    0x09: "S_MOD_INIT_FUNC_POINTERS", 0x0A: "S_MOD_TERM_FUNC_POINTERS",
    0x0B: "S_COALESCED", 0x0C: "S_GB_ZEROFILL",
}
S_ATTR_PURE_INSTRUCTIONS = 0x80000000
S_ATTR_SOME_INSTRUCTIONS = 0x00000400

MAGIC_SIZE = 32               # mach_header_64 固定 32 字节
SEG_CMD_64_SIZE = 72          # segment_command_64 固定 72 字节
SECTION_64_SIZE = 80
SYMTAB_CMD_SIZE = 24
DYLIB_CMD_SIZE = 24           # cmd+cmdsize + name/timestamp/cur/compat


class MachOError(Exception):
    """解析失败（格式非法 / 字段自相矛盾）"""


# ------------------------------------------------------------- 数据结构

@dataclass
class MachHeader:
    magic: int
    cputype: int
    cpusubtype: int
    filetype: int
    ncmds: int
    sizeofcmds: int
    flags: int
    reserved: int

    @property
    def cpuname(self) -> str:
        return {CPU_TYPE_X86_64: "x86_64", CPU_TYPE_ARM64: "arm64"}.get(
            self.cputype, f"cpu(0x{self.cputype:x})")

    @property
    def filetypename(self) -> str:
        return FILETYPE.get(self.filetype, f"type(0x{self.filetype:x})")


@dataclass
class Section:
    index: int
    sectname: str
    segname: str
    addr: int
    size: int
    offset: int
    align: int
    flags: int

    @property
    def sectype(self) -> str:
        return SECTION_TYPE.get(self.flags & 0xFF, f"type(0x{self.flags & 0xFF:x})")

    @property
    def pure_instructions(self) -> bool:
        return bool(self.flags & S_ATTR_PURE_INSTRUCTIONS)


@dataclass
class Segment:
    segname: str
    vmaddr: int
    vmsize: int
    fileoff: int
    filesize: int
    maxprot: int
    initprot: int
    flags: int
    sections: list[Section] = field(default_factory=list)

    @property
    def prot_str(self) -> str:
        def one(p: int) -> str:
            return "".join(v for k, v in VM_PROT.items() if p & k) or "-"
        return f"{one(self.initprot)}/{one(self.maxprot)}"


@dataclass
class Dylib:
    ordinal: int
    name: str
    current_version: str
    compat_version: str


@dataclass
class MachO:
    header: MachHeader
    segments: list[Segment]
    dylibs: list[Dylib]
    symtab: tuple[int, int, int, int] | None   # (symoff, nsyms, stroff, strsize)
    other_commands: list[tuple[int, int]]      # (cmd, cmdsize)


# ------------------------------------------------------- 构造合成样本

def build_sample() -> bytes:
    """构造一个结构与真实 MH_EXECUTE 等价的合成 Mach-O"""

    def segment(name: bytes, vmaddr: int, vmsize: int, fileoff: int, filesize: int,
                maxprot: int, initprot: int, sections: list[tuple]) -> bytes:
        nsects = len(sections)
        cmdsize = SEG_CMD_64_SIZE + nsects * SECTION_64_SIZE
        out = struct.pack("<II16sQQQQiiII", LC_SEGMENT_64, cmdsize, name,
                          vmaddr, vmsize, fileoff, filesize, maxprot, initprot,
                          nsects, 0)
        for (sname, sseg, addr, size, off, align, sflags) in sections:
            out += struct.pack("<16s16sQQIIIIIIII", sname, sseg, addr, size, off,
                               align, 0, 0, sflags, 0, 0, 0)
        return out

    def dylib_cmd(name: bytes) -> bytes:
        body_len = DYLIB_CMD_SIZE + len(name) + 1
        pad = (-body_len) % 8                      # cmdsize 必须 8 字节对齐
        cmdsize = body_len + pad
        return struct.pack("<IIIIII", LC_LOAD_DYLIB, cmdsize,
                           DYLIB_CMD_SIZE, 0, 0x0001_0E00, 0x0001_0000) \
            + name + b"\0" + b"\0" * pad

    text_sections = [
        (b"__text", b"__TEXT", 0x100000F00, 0x30, 0xF00, 4,
         S_ATTR_PURE_INSTRUCTIONS),
        (b"__cstring", b"__TEXT", 0x100000F30, 0x0C, 0xF30, 0, 0x02),
    ]
    data_sections = [
        (b"__data", b"__DATA", 0x100001000, 0x10, 0x1000, 3, 0x00),
        (b"__bss", b"__DATA", 0x100001010, 0x20, 0x0000, 3, 0x01),
    ]

    cmds = [
        # __PAGEZERO：vmsize=4GB 但 filesize=0，让解引用 NULL 立刻崩
        segment(b"__PAGEZERO", 0, 0x1_0000_0000, 0, 0, 0, 0, []),
        segment(b"__TEXT", 0x100000000, 0x1000, 0, 0x1000, 0x7, 0x5, text_sections),
        segment(b"__DATA", 0x100001000, 0x1000, 0x1000, 0x1000, 0x7, 0x3, data_sections),
        segment(b"__LINKEDIT", 0x100002000, 0x1000, 0x2000, 0x200, 0x7, 0x1, []),
        struct.pack("<IIIIII", LC_SYMTAB, SYMTAB_CMD_SIZE, 0x2000, 2, 0x2018, 0x40),
        dylib_cmd(b"/usr/lib/libSystem.B.dylib"),
        struct.pack("<II", LC_UUID, 24) + bytes(range(16)),
    ]

    cmds_total = sum(len(c) for c in cmds)
    header = struct.pack("<IIIIIIII", MH_MAGIC_64, CPU_TYPE_X86_64, 0x80000003, 0x2,
                         len(cmds), cmds_total, 0x00200085, 0)
    blob = header + b"".join(cmds)
    return blob.ljust(0x2200, b"\0")     # 填到 __LINKEDIT 结束
