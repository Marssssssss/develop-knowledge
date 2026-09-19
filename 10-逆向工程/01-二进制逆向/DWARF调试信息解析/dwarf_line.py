#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DWARF5 行号程序：一个有限状态机把「程序」还原成「地址 → 源码行列」的表。

为什么不用一张直表：每条机器指令一行的话表会大到不可接受，所以 DWARF
把它编成一台状态机的指令序列，由消费端跑一遍还原出完整矩阵。

数据来源（本轮实测下载并提取正文）：
  * DWARF5 §6.2.4 The Line Number Program Header —— 头部 13 个字段的顺序与语义
  * DWARF5 §6.2.5.1 Special Opcodes —— opcode/enhanced 换算公式原文
  * DWARF5 §6.2.5.2 Standard Opcodes —— 12 个标准操作码
  * DWARF5 §6.2.5.3 Extended Opcodes —— DW_LNE_end_sequence / set_address / set_discriminator
  * DWARF5 §7.22 Table 7.25 / 7.26 —— 操作码取值表
  https://dwarfstd.org/doc/DWARF5.pdf
"""

import struct

from dwarf_parse import Reader, read_initial_length

# Table 7.25:Line number standard opcode encodings
DW_LNS_copy = 0x01
DW_LNS_advance_pc = 0x02
DW_LNS_advance_line = 0x03
DW_LNS_set_file = 0x04
DW_LNS_set_column = 0x05
DW_LNS_negate_stmt = 0x06
DW_LNS_set_basic_block = 0x07
DW_LNS_const_add_pc = 0x08
DW_LNS_fixed_advance_pc = 0x09
DW_LNS_set_prologue_end = 0x0A
DW_LNS_set_epilogue_begin = 0x0B
DW_LNS_set_isa = 0x0C

# Table 7.26:Line number extended opcode encodings
DW_LNE_end_sequence = 0x01
DW_LNE_set_address = 0x02
DW_LNE_set_discriminator = 0x04


class LineState(object):
    """§6.2.2 的状态机寄存器。初始值见 §6.2.2 的约定。"""

    KEYS = ("address", "op_index", "file", "line", "column", "is_stmt",
            "basic_block", "end_sequence", "prologue_end", "epilogue_begin",
            "isa", "discriminator")

    def __init__(self, is_stmt=True):
        self.address = 0
        self.op_index = 0
        self.file = 1
        self.line = 1
        self.column = 0
        self.is_stmt = bool(is_stmt)
        self.basic_block = False
        self.end_sequence = False
        self.prologue_end = False
        self.epilogue_begin = False
        self.isa = 0
        self.discriminator = 0

    def row(self):
        return dict((k, getattr(self, k)) for k in self.KEYS)

    def reset_after_sequence(self, is_stmt):
        """end_sequence 之后必须把寄存器复位到「行序列开头」的初值。"""
        self.__init__(is_stmt=is_stmt)


class LineProgram(object):
    """一个 .debug_line 单元：头部 + 程序。"""

    def __init__(self, header, program):
        self.header = header
        self.program = program

    @property
    def opcode_base(self):
        return self.header["opcode_base"]

    @property
    def line_range(self):
        return self.header["line_range"]

    @property
    def line_base(self):
        return self.header["line_base"]

    @property
    def min_inst_len(self):
        return self.header["minimum_instruction_length"]

    @property
    def max_ops(self):
        return self.header["maximum_operations_per_instruction"]


def parse_line_header(data, off=0):
    """§6.2.4：按下列顺序读头部。注意 version 是**行号信息的**版本号，
    文档明确说它「is independent of the DWARF version number」。"""
    unit_length, offset_size, pos = read_initial_length(data, off)
    rd = Reader(data, pos)
    h = {
        "unit_length": unit_length,
        "offset_size": offset_size,
        "version": rd.u16(),
        "address_size": rd.u8(),
        "segment_selector_size": rd.u8(),
    }
    h["header_length"] = rd.u64() if offset_size == 8 else rd.u32()
    # §6.2.4:「the number of bytes following the header_length field to the
    # beginning of the first byte of the line number program」—— 起点是
    # header_length 字段之后,不是「读完最后一个已知字段」的当前位置。
    after_len_field = rd.off
    h["minimum_instruction_length"] = rd.u8()
    h["maximum_operations_per_instruction"] = rd.u8()
    h["default_is_stmt"] = rd.u8()
    h["line_base"] = struct.unpack_from("<b", bytes([rd.u8()]))[0]  # sbyte
    h["line_range"] = rd.u8()
    h["opcode_base"] = rd.u8()
    h["standard_opcode_lengths"] = [rd.u8() for _ in range(h["opcode_base"] - 1)]
    prog_start = after_len_field + h["header_length"]
    unit_end = pos + unit_length  # unit_length 不含 initial length 字段本身
    return h, prog_start, unit_end


def _advance(prog, st, operation_advance):
    """§6.2.5.1 的 new address / new op_index 两组公式。"""
    h = prog.header
    total = st.op_index + operation_advance
    st.address += h["minimum_instruction_length"] * (
        total // h["maximum_operations_per_instruction"])
    st.op_index = total % h["maximum_operations_per_instruction"]


def _apply_special(prog, st, rows, opcode):
    """特化 §6.2.5.1 开头的七步：加行 / 推进 op / 追加一行 / 复位四个寄存器。"""
    adjusted = opcode - prog.opcode_base
    operation_advance = adjusted // prog.line_range
    line_increment = prog.line_base + (adjusted % prog.line_range)
    st.line += line_increment
    _advance(prog, st, operation_advance)
    rows.append(st.row())
    st.basic_block = False
    st.prologue_end = False
    st.epilogue_begin = False
    st.discriminator = 0


def run_line_program_bytes(data, prog, start, end):
    rd = Reader(data, start)
    rows = []
    st = LineState(is_stmt=bool(prog.header["default_is_stmt"]))
    while rd.off < end:
        opcode = rd.u8()
        if opcode >= prog.opcode_base:
            _apply_special(prog, st, rows, opcode)
            continue
        if opcode == 0:  # extended opcode:0x00 ULEB(长度) sub-opcode ...
            length = rd.uleb()  # 长度**含** sub-opcode 那一个字节
            after_len = rd.off
            sub = rd.u8()
            if sub == DW_LNE_end_sequence:
                st.end_sequence = True
                rows.append(st.row())
                st = LineState(is_stmt=bool(prog.header["default_is_stmt"]))
            elif sub == DW_LNE_set_address:
                st.address = rd.u64() if prog.header["address_size"] == 8 else rd.u32()
                st.op_index = 0
            elif sub == DW_LNE_set_discriminator:
                st.discriminator = rd.uleb()
            else:
                rd.off = after_len + length  # 未知扩展操作码:整块跳过 payload
            continue
        if opcode == DW_LNS_copy:
            rows.append(st.row())
            st.discriminator = 0
            st.basic_block = False
            st.prologue_end = False
            st.epilogue_begin = False
        elif opcode == DW_LNS_advance_pc:
            _advance(prog, st, rd.uleb())
        elif opcode == DW_LNS_advance_line:
            st.line += rd.sleb()
        elif opcode == DW_LNS_set_file:
            st.file = rd.uleb()
        elif opcode == DW_LNS_set_column:
            st.column = rd.uleb()
        elif opcode == DW_LNS_negate_stmt:
            st.is_stmt = not st.is_stmt
        elif opcode == DW_LNS_set_basic_block:
            st.basic_block = True
        elif opcode == DW_LNS_const_add_pc:
            _advance(prog, st, (255 - prog.opcode_base) // prog.line_range)
        elif opcode == DW_LNS_fixed_advance_pc:
            # 唯一一个操作数不是变长数的标准操作码,且不乘 minimum_instruction_length
            st.address += rd.u16()
            st.op_index = 0
        elif opcode == DW_LNS_set_prologue_end:
            st.prologue_end = True
        elif opcode == DW_LNS_set_epilogue_begin:
            st.epilogue_begin = True
        elif opcode == DW_LNS_set_isa:
            st.isa = rd.uleb()
        else:
            raise NotImplementedError("未实现的 DW_LNS 0x%02x" % opcode)
    return rows


def encode_special_opcode(prog, line_increment, operation_advance):
    """§6.2.5.1 里给 emit 侧用的公式：

        opcode = (desired line increment - line_base)
                 + (line_range * operation advance) + opcode_base
    """
    opcode = ((line_increment - prog.line_base)
              + prog.line_range * operation_advance + prog.opcode_base)
    if not (prog.opcode_base <= opcode <= 255):
        return None  # 只能用标准操作码
    return opcode


def lookup(rows, address):
    """给定虚拟地址找最近的一行（§6.2.4 提到的两种用途之一：崩溃点反查源码）。

    取「address 不超过 addr 的最后一行」；位于 end_sequence 行之后则无。
    """
    best = None
    for r in rows:
        if r["address"] <= address:
            if best is None or r["address"] > best["address"]:
                best = r
    return best
