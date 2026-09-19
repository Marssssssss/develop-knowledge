#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""x86-64 CFI 展开：.eh_frame / .eh_frame_hdr 结构 + DW_CFA 程序 + 栈回溯。

数据来源（本轮实测下载并提取正文）：
  * Linux Standard Base Core Specification, Generic Part 5.0.0
    §10.6 Exception Frames —— CIE/FDE 字段清单、augmentation 串四个字符、
    .eh_frame_hdr 布局
    https://refspecs.linuxbase.org/LSB_5.0.0/LSB-Core-generic/LSB-Core-generic/ehframechpt.html
  * System V AMD64 psABI Draft 0.99.6
    §3.6.2 Figure 3.36 DWARF Register Number Mapping（%rsp=7, %rbp=6, RA=16）
    §3.6.2 Figure 3.37 Pointer Encoding Specification Byte（掩码 0x1..0x40）
    §3.7 Stack Unwind Algorithm（GNU_ARGS_SIZE 0x2e 的用途）
    https://refspecs.linuxfoundation.org/elf/x86_64-abi-0.99.pdf
  * binutils include/dwarf2.def —— DW_CFA_* 操作码取值
"""

import struct

# ------------------------------------------- psABI Figure 3.36 寄存器编号

REG_RAX, REG_RDX, REG_RCX, REG_RBX = 0, 1, 2, 3
REG_RSI, REG_RDI, REG_RBP, REG_RSP = 4, 5, 6, 7
REG_RA = 16  # Return Address —— psABI 脚注 29 特别说明：它不是物理寄存器,
             # 实际存在 0(%rsp),但 DWARF 仍然给它分配了编号。

X86_64_REG_NAMES = ["%rax", "%rdx", "%rcx", "%rbx", "%rsi", "%rdi", "%rbp", "%rsp"]
X86_64_REG_NAMES += ["%r" + str(i) for i in range(8, 16)]
X86_64_REG_NAMES.append("RA")


def reg_name(num):
    if num < len(X86_64_REG_NAMES):
        return X86_64_REG_NAMES[num]
    return "reg%d" % num


# --------------------------------------------------------- psABI Figure 3.37

class PtrEnc(object):
    """8 位的指针编码说明字节。低 4 位是「怎么存」,其余是位标志。

    Figure 3.37 原文（mask / meaning）：
      0x1 uleb128 / sleb128（视 0x8 而定）  0x2 udata2 / sdata2
      0x3 udata4 / sdata4                   0x4 udata8 / sdata8
      0x8 signed   0x10 PC relative   0x20 text relative   0x40 function relative
    0x30 是 0x20|0x10,即 data section relative。
    默认值 0 = “direct 4-byte absolute pointers”。
    """

    def __init__(self, byte):
        self.byte = byte
        self.format = byte & 0x0F
        self.signed = bool(byte & 0x08)
        self.pc_rel = bool(byte & 0x10)
        self.text_rel = bool(byte & 0x20)
        self.func_rel = bool(byte & 0x40)

    @property
    def data_rel(self):
        return self.text_rel and self.pc_rel

    def __repr__(self):
        return "PtrEnc(0x%02x)" % self.byte


class Reader(object):
    """UEB/SLEB 与定长字段的最小读取器。"""

    def __init__(self, data, off=0):
        self.data = data
        self.off = off

    def raw(self, n):
        v = self.data[self.off:self.off + n]
        self.off += n
        return bytes(v)

    def u8(self):
        return struct.unpack_from("<B", self.raw(1))[0]

    def u32(self):
        return struct.unpack_from("<I", self.raw(4))[0]

    def u64(self):
        return struct.unpack_from("<Q", self.raw(8))[0]

    def uleb(self):
        result = shift = 0
        while True:
            b = self.u8()
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7

    def sleb(self):
        result = shift = 0
        while True:
            b = self.u8()
            result |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                if b & 0x40:
                    result -= 1 << shift
                return result

    def cstr(self):
        end = self.data.index(b"\x00", self.off)
        v = self.data[self.off:end].decode("ascii")
        self.off = end + 1
        return v

    def encoded_ptr(self, enc, place=0, text=0, data_sec=0, func=0):
        """按 Figure 3.37 解一个编码指针；本 demo 只实现 absptr / udata 三类。"""
        e = enc if isinstance(enc, PtrEnc) else PtrEnc(enc)
        fmt = e.format & 0x07   # 0x08 是 signed 标志,不参与「存法」分类
        if fmt == 0x00:  # absptr：与地址同宽,本 demo 用 8 字节
            v = self.u64()
        elif fmt == 0x03:
            v = self.u32()
        elif fmt == 0x02:
            v = struct.unpack_from("<H", self.raw(2))[0]
        elif fmt == 0x01:
            v = self.sleb() if e.signed else self.uleb()
        else:
            raise ValueError("未实现的指针编码 0x%02x" % e.byte)
        if e.signed and fmt in (0x02, 0x03):
            bits = 16 if e.format == 0x02 else 32
            if v >= (1 << (bits - 1)):
                v -= 1 << bits
        if e.pc_rel:
            v += place
        elif e.text_rel and not e.pc_rel:
            v += text
        elif e.data_rel:
            v += data_sec
        elif e.func_rel:
            v += func
        return v


# ------------------------------------------------------- DW_CFA 操作码取值

DW_CFA_advance_loc = 0x40
DW_CFA_offset = 0x80
DW_CFA_restore = 0xC0
DW_CFA_nop = 0x00
DW_CFA_set_loc = 0x01
DW_CFA_advance_loc1 = 0x02
DW_CFA_advance_loc2 = 0x03
DW_CFA_advance_loc4 = 0x04
DW_CFA_offset_extended = 0x05
DW_CFA_restore_extended = 0x06
DW_CFA_undefined = 0x07
DW_CFA_same_value = 0x08
DW_CFA_register = 0x09
DW_CFA_remember_state = 0x0A
DW_CFA_restore_state = 0x0B
DW_CFA_def_cfa = 0x0C
DW_CFA_def_cfa_register = 0x0D
DW_CFA_def_cfa_offset = 0x0E
DW_CFA_def_cfa_sf = 0x12
DW_CFA_def_cfa_offset_sf = 0x13
DW_CFA_val_offset = 0x14
DW_CFA_GNU_args_size = 0x2E


# ---------------------------------------------------------- CIE / FDE 结构

class CIE(object):
    """LSB §10.6.1.1 Common Information Entry。

    字段序：Length / [Extended Length] / CIE ID(=0) / Version(=1) /
            Augmentation String / Code Alignment Factor(ULEB) /
            Data Alignment Factor(SLEB) / Return Address Register /
            [Augmentation Data Length] / [Augmentation Data] /
            Initial Instructions / Padding
    Length == 0              -> 该条是 terminator，处理到此结束
    Length == 0xffffffff     -> 真长度在接下来的 8 字节 Extended Length 里
    """

    def __init__(self, offset=0, length=0, aug="", code_align=1, data_align=-8,
                 ra_reg=REG_RA, instructions=b"", version=1, cie_id=0):
        self.offset = offset
        self.length = length
        self.version = version
        self.cie_id = cie_id
        self.aug = aug
        self.code_align = code_align
        self.data_align = data_align
        self.ra_reg = ra_reg
        self.instructions = instructions

    @property
    def has_z(self):
        return self.aug.startswith("z")


class FDE(object):
    """LSB §10.6.1.2 Frame Description Entry。

    CIE Pointer 的口径是：用「本 FDE 里 CIE Pointer 字段自身的偏移」减去它，
    得到关联 CIE 的起始偏移 —— 它是一个"往回走多远"，不是绝对地址。
    """

    def __init__(self, offset=0, cie=None, pc_begin=0, pc_range=0,
                 instructions=b"", aug_data=b""):
        self.offset = offset
        self.cie = cie
        self.pc_begin = pc_begin
        self.pc_range = pc_range
        self.instructions = instructions
        self.aug_data = aug_data

    def covers(self, pc):
        return self.pc_begin <= pc < self.pc_begin + self.pc_range


def _read_length(rd):
    length = rd.u32()
    if length == 0:
        return 0, 4
    if length == 0xFFFFFFFF:
        return rd.u64(), 8
    return length, 4


def parse_eh_frame(data, base=0x400000):
    """线性扫一条 .eh_frame，返回 (CIE 列表, FDE 列表)。

    augmentation 串本 demo 只支持 ""（无 'z'），因此不存在 Augmentation Data
    Length / Data 两个字段；PC Begin / PC Range 按 Figure 3.37 的默认编码
    （值为 0 = direct 4-byte absolute pointers）处理成绝对值。
    """
    cies, fdes = {}, []
    rd = Reader(data, 0)
    while rd.off < len(data):
        start = rd.off
        length, enc = _read_length(rd)
        if length == 0:
            break                       # terminator
        body_end = start + (4 if enc == 4 else 12) + length
        ident = rd.u32()
        if ident == 0:                  # CIE：CIE ID shall always be 0
            cie = CIE(offset=start, length=length)
            cie.version = rd.u8()
            cie.aug = rd.cstr()
            cie.code_align = rd.uleb()
            cie.data_align = rd.sleb()
            cie.ra_reg = rd.uleb()
            if cie.has_z:
                aug_len = rd.uleb()
                cie.instructions = rd.raw(aug_len)
            else:
                cie.instructions = rd.raw(body_end - rd.off)
            cies[start] = cie
            rd.off = body_end
        else:
            cie_ptr_field_off = rd.off - 4
            cie_start = cie_ptr_field_off - ident
            cie = cies[cie_start]
            pc_begin = rd.u64()
            pc_range = rd.u64()
            instr = rd.raw(body_end - rd.off)
            fdes.append(FDE(start, cie, pc_begin, pc_range, instr))
            rd.off = body_end
    return cies, fdes


def find_fde(fdes, pc):
    hits = [f for f in fdes if f.covers(pc)]
    if not hits:
        return None
    return hits[0]
