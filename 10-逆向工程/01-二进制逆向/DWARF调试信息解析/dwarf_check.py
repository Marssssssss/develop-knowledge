#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DWARF5 解析自检：LEB128 / CU 头 / 缩写表 / DIE / 行号程序状态机。

运行：python dwarf_check.py
构造输入全部是**按 DWARF5 规范手搓的合成字节**，不依赖任何真实编译器产物，
因此每一条断言都是对「解析是否符合规范」的直接检验。
"""

import struct
import sys

from dwarf_parse import (Reader, DW_UT, DW_TAG, DW_AT, DW_FORM,
                         parse_cu_header, parse_abbrev_table, parse_dies,
                         uleb128_encode, uleb128_decode, sleb128_encode,
                         sleb128_decode, read_initial_length, DWARF64_MARKER)
from dwarf_line import (LineState, _advance, LineProgram, parse_line_header,
                        run_line_program_bytes, encode_special_opcode, lookup,
                        DW_LNS_advance_pc, DW_LNS_copy, DW_LNS_advance_line,
                        DW_LNS_negate_stmt, DW_LNS_const_add_pc,
                        DW_LNS_fixed_advance_pc, DW_LNE_set_address,
                        DW_LNE_end_sequence)

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def raises(fn, exc, label):
    try:
        fn()
    except exc:
        return True
    except Exception as e:
        FAIL.append("%s:应抛 %s,实抛 %r" % (label, exc.__name__, e))
        return False
    FAIL.append("%s:应抛 %s 却没有" % (label, exc.__name__))
    return False


# ------------------------------------------------------------- 合成数据构造

def build_header(version=5, address_size=8, seg_sel=0, min_inst_len=1, max_ops=1,
                 default_is_stmt=1, line_base=-5, line_range=12, opcode_base=13,
                 program=b""):
    """按 §6.2.4 的字段顺序拼一个行号单元。

    第 12 项之后还有 DWARF5 的 directory/file 描述符；本合成样例两者都取 0 项,
    于是只追加 4 个零字节。header_length 为「header_length 字段之后到程序首字节」。
    """
    tail = struct.pack("<BBBbBB", min_inst_len, max_ops, default_is_stmt,
                       line_base, line_range, opcode_base)
    tail += b"\x00" * (opcode_base - 1)   # standard_opcode_lengths
    tail += b"\x00\x00\x00\x00"           # 目录表 0 项 + 文件表 0 项
    header_length = len(tail)
    unit_length = 2 + 1 + 1 + 4 + header_length + len(program)
    head = (struct.pack("<I", unit_length) + struct.pack("<H", version)
            + struct.pack("<BB", address_size, seg_sel)
            + struct.pack("<I", header_length) + tail)
    return head + program, unit_length, header_length


def build_cu(abbrev_data, dies_data, offset_size=4, version=5, unit_type=0x01,
             address_size=8, abbrev_offset=0):
    fmt = "<Q" if offset_size == 8 else "<I"
    after_len = struct.pack("<H", version) + struct.pack("<BB", unit_type, address_size)
    after_len += struct.pack(fmt, abbrev_offset) + dies_data
    length = len(after_len)
    if offset_size == 4:
        head = struct.pack("<I", length) + after_len
    else:
        head = struct.pack("<I", DWARF64_MARKER) + struct.pack("<Q", length) + after_len
    return head, length


def main():
    print("== 1. ULEB128 / SLEB128 / initial length ==")
    for v in (0, 1, 63, 127, 128, 0x3FFF, 625485, 0xFFFFFFFF):
        blob = uleb128_encode(v)
        got, n = uleb128_decode(blob, 0)
        check(got == v and n == len(blob), "ULEB round-trip %d -> %r" % (v, blob))
    check(uleb128_encode(128) == b"\x80\x01", "128 = 0x80 0x01(continuation bit)")
    check(uleb128_encode(0) == b"\x00", "0 仍占一个字节")
    for v in (0, -1, 1, -63, 64, -64, -8193, 8192):
        blob = sleb128_encode(v)
        got, n = sleb128_decode(blob, 0)
        check(got == v and n == len(blob), "SLEB round-trip %d -> %r" % (v, blob))
    check(sleb128_encode(-1) == b"\x7f", "-1 只需一个字节(最高位之外的第 6 位作符号)")
    raises(lambda: uleb128_encode(-1), ValueError, "ULEB 拒绝负数")
    # 32/64 位 DWARF
    check(read_initial_length(struct.pack("<I", 0x120) + b"\x00" * 8, 0) == (0x120, 4, 4),
          "32 位 initial length")
    b64 = struct.pack("<I", DWARF64_MARKER) + struct.pack("<Q", 0x1234) + b"\x00" * 4
    check(read_initial_length(b64, 0) == (0x1234, 8, 12), "64 位 initial length")

    print("== 2. DWARF5 CU 头(§7.5.1.1 字段顺序) ==")
    dies_data = b"\x00"  # 只有一个 null DIE
    blob, length = build_cu(b"", dies_data)
    cu = parse_cu_header(blob, 0)
    check(cu["version"] == 5, "version = 5")
    check(cu["unit_type"] == 0x01 and cu["unit_type_name"] == "DW_UT_compile",
          "unit_type = DW_UT_compile")
    check(cu["address_size"] == 8, "address_size")
    check(cu["offset_size"] == 4, "32 位 DWARF")
    check(cu["debug_abbrev_offset"] == 0, "debug_abbrev_offset")
    check(cu["header_size"] == 4 + 2 + 1 + 1 + 4, "CU 头共 12 字节(32 位)")
    check(cu["unit_end"] == len(blob), "unit_length 指向本单元末尾")
    check(cu["dies_offset"] == cu["header_size"], "DIE 紧随其后")
    check(DW_UT[0x05] == "DW_UT_split_compile" and DW_UT[0x06] == "DW_UT_split_type",
          "Table 7.2 取值")

    print("== 3. 缩写表(§7.5.3)与 implicit_const ==")
    # code 1: DW_TAG_compile_unit, has_children=1, (name, string), (language, data2)
    # code 2: DW_TAG_base_type, has_children=0, (byte_size, implicit_const 8)
    abbrev = b""
    abbrev += b"\x01" + b"\x11" + b"\x01"
    abbrev += b"\x03" + b"\x08"      # DW_AT_name / DW_FORM_string
    abbrev += b"\x13" + b"\x06"      # DW_AT_language / DW_FORM_data4(故意写 data4 测 & 改 data2)
    abbrev += b"\x00" + b"\x00"
    abbrev += b"\x02" + b"\x24" + b"\x00"
    abbrev += b"\x0b" + b"\x21" + b"\x08"   # DW_AT_byte_size / DW_FORM_implicit_const = 8
    abbrev += b"\x00" + b"\x00"
    abbrev += b"\x00"
    table, end = parse_abbrev_table(abbrev, 0)
    check(end == len(abbrev), "缩写表消费到结尾")
    check(set(table) == {1, 2}, "两条缩写")
    check(table[1]["tag"] == 0x11 and table[1]["has_children"] == 1, "code 1 有子节点")
    check(table[2]["has_children"] == 0, "code 2 无子节点")
    check(table[2]["attrs"][0][2] == 8, "implicit_const 常量存在缩写表里")
    check(DW_TAG[0x2E] == "DW_TAG_subprogram" and DW_AT[0x25] == "DW_AT_producer",
          "DW_TAG / DW_AT 编号")

    print("== 4. DIE:编码 -> 解码 round-trip ==")
    # 手工写一条 CU DIE(name="demo.c", language=0x21=DW_LANG_C11? 用 0x0c=ANSI C 更稳)
    # 这里不引用语言编号的官方含义,只做数值往返。
    dies = b"\x01" + b"demo.c\x00" + struct.pack("<I", 0x0C)
    dies += b"\x02"                      # code 2:全部属性走 implicit_const,不占字节
    dies += b"\x00"                      # null DIE
    blob, _ = build_cu(abbrev, dies)
    cu = parse_cu_header(blob, 0)
    _tbl, _ = parse_abbrev_table(abbrev, 0)
    died_list, consumed = parse_dies(blob, cu, _tbl)
    top = [d for d in died_list if d["tag"] is not None]
    check(len(top) == 2, "两个实 DIE + 一个 null")
    check(top[0]["tag_name"] == "DW_TAG_compile_unit", "第一个是 CU")
    check(top[0]["attrs"]["DW_AT_name"] == "demo.c", "string 型属性读出字符串")
    check(top[0]["attrs"]["DW_AT_language"] == 0x0C, "data4 型属性读出整数")
    check(top[1]["attrs"]["DW_AT_byte_size"] == 8, "implicit_const 不读字节却给值")
    check(died_list[-1]["tag"] is None, "最后一个是 null entry")
    check(consumed == cu["unit_end"], "DIE 流正好消费到单元末尾")

    print("== 5. 行号程序头部(§6.2.4) ==")
    blob, unit_len, hdr_len = build_header(program=b"\x00")
    h, prog_start, prog_end = parse_line_header(blob, 0)
    check(h["version"] == 5, "行号版本独立取值")
    check(h["minimum_instruction_length"] == 1, "min_inst_len")
    check(h["maximum_operations_per_instruction"] == 1, "max_ops")
    check(h["line_base"] == -5, "line_base 是有符号字节")
    check(h["line_range"] == 12, "line_range")
    check(h["opcode_base"] == 13, "opcode_base")
    check(len(h["standard_opcode_lengths"]) == h["opcode_base"] - 1,
          "standard_opcode_lengths 有 %d 项" % (h["opcode_base"] - 1))
    lp = LineProgram(h, b"")

    print("== 6. 特殊操作码(§6.2.5.1 公式) ==")
    # 编码侧:line_increment=+1, operation_advance=1
    op = encode_special_opcode(lp, 1, 1)
    check(op == (1 - (-5)) + 12 * 1 + 13, "encode =(Δline-line_base)+line_range*opadv+base")
    check(op == 31, "具体值 31")
    check(encode_special_opcode(lp, 100, 1) == 130,
          "Δline=100/opadv=1 仍装得下:105+12+13=130")
    check(encode_special_opcode(lp, 100, 100) is None, "超过 255 必须改用标准操作码")
    check(encode_special_opcode(lp, 1, 30) is None, "30*12+13 超 255:溢出被捕获")
    # 解码侧
    st = LineState(is_stmt=True)
    rows = []
    adj = op - lp.opcode_base
    check(adj // lp.line_range == 1, "解码 operation advance = 1")
    check(lp.line_base + (adj % lp.line_range) == 1, "解码 line increment = 1")

    print("== 7. 完整行号程序执行 ==")
    addr = 0x401000
    prog = b""
    prog += b"\x00\x09" + bytes([DW_LNE_set_address]) + struct.pack("<Q", addr)
    prog += bytes([op])                       # line 1 -> 2, addr += 1
    prog += bytes([DW_LNS_advance_pc]) + b"\x04"   # addr += 4
    prog += bytes([DW_LNS_copy])              # 追加一行
    prog += b"\x00\x01" + bytes([DW_LNE_end_sequence])
    blob, _, _ = build_header(program=prog)
    h, start, end = parse_line_header(blob, 0)
    lp = LineProgram(h, prog)
    rows = run_line_program_bytes(blob, lp, start, end)
    check(len(rows) == 3, "三行:两个实 row + 一个 end_sequence,实为 %d" % len(rows))
    check(rows[0]["address"] == addr + 1 and rows[0]["line"] == 2,
          "第一行:地址 +1、行号 +1")
    check(rows[1]["address"] == addr + 5 and rows[1]["line"] == 2,
          "advance_pc 4 后再 copy")
    check(rows[2]["end_sequence"] is True and rows[2]["address"] == addr + 5,
          "end_sequence 行标记序列终止")
    check(all(not r["basic_block"] for r in rows), "special opcode 会清 basic_block")
    # end_sequence 之后寄存器必须复位
    check(rows[2]["line"] == 2 and rows[2]["file"] == 1, "end_sequence 行沿用当前值")

    print("== 8. 崩溃地址反查源码行 ==")
    got = lookup(rows, addr + 1)
    check(got is not None and got["line"] == 2, "addr+1 命中第一行")
    got2 = lookup(rows, addr + 3)
    check(got2["address"] == addr + 1, "addr+3 落在两行之间,取前一行")
    got3 = lookup(rows, addr + 999)
    check(got3["address"] == addr + 5, "超出时也取最后一行(含 end_sequence)")

    print("== 9. const_add_pc / fixed_advance_pc / negate_stmt ==")
    prog2 = b"\x00\x09" + bytes([DW_LNE_set_address]) + struct.pack("<Q", addr)
    prog2 += bytes([DW_LNS_const_add_pc]) + bytes([DW_LNS_copy])
    blob2, _, _ = build_header(program=prog2)
    h2, s2, e2 = parse_line_header(blob2, 0)
    lp2 = LineProgram(h2, prog2)
    rows2 = run_line_program_bytes(blob2, lp2, s2, e2)
    check(rows2[0]["address"] == addr + (255 - h2["opcode_base"]) // h2["line_range"],
          "const_add_pc = 特殊操作码 255 的推进量:%d" %
          ((255 - h2["opcode_base"]) // h2["line_range"]))
    prog3 = b"\x00\x09" + bytes([DW_LNE_set_address]) + struct.pack("<Q", addr)
    prog3 += bytes([DW_LNS_fixed_advance_pc]) + struct.pack("<H", 0x100)
    prog3 += bytes([DW_LNS_negate_stmt]) + bytes([DW_LNS_copy])
    blob3, _, _ = build_header(program=prog3)
    h3, s3, e3 = parse_line_header(blob3, 0)
    rows3 = run_line_program_bytes(blob3, LineProgram(h3, prog3), s3, e3)
    check(rows3[0]["address"] == addr + 0x100,
          "fixed_advance_pc 不乘 min_inst_len,直接加 uhalf")
    check(rows3[0]["is_stmt"] is False, "negate_stmt 翻转 is_stmt")

    print("== 10. VLIW:op_index 参与进位(max_ops > 1) ==")
    prog4 = b"\x00\x09" + bytes([DW_LNE_set_address]) + struct.pack("<Q", addr)
    prog4 += bytes([DW_LNS_advance_pc]) + b"\x03"
    blob4, _, _ = build_header(program=prog4, min_inst_len=4, max_ops=2)
    h4, s4, e4 = parse_line_header(blob4, 0)
    check(h4["maximum_operations_per_instruction"] == 2, "头部读出 max_ops=2")
    rows4 = []
    # 注意:状态机的 address 初值是 0,progressing 由 DW_LNE_set_address 才拉到实际加载地址
    st4 = LineState()
    st4.address = addr
    _advance(LineProgram(h4, prog4), st4, 3)
    check(st4.address == addr + 4 * (3 // 2), "operation advance 3 / max_ops 2 -> 地址 +4")
    check(st4.op_index == 3 % 2, "op_index 取余")
    st5 = LineState()
    st5.address = addr
    _advance(LineProgram(h4, prog4), st5, 2)
    check(st5.address == addr + 4 and st5.op_index == 0,
          "advance 正好等于 max_ops 时不留余")

    print("\n结果: %d 项失败" % len(FAIL))
    for x in FAIL:
        print("  FAIL: %s" % x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
