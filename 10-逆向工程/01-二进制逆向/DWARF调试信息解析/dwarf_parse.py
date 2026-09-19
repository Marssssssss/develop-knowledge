#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DWARF5 调试信息骨架解析：initial length / CU 头 / 缩写表 / DIE。

数据来源（本轮实测下载并提取正文）：
  * DWARF Debugging Information Format Version 5 (February 13, 2017)
    - §7.4  Initial Length Object Representation（32/64 位 DWARF）
    - §7.5.1.1 Full and Partial Compilation Unit Headers
    - §7.5.3 Abbreviations Tables
    - Table 7.2 Unit header unit type encodings / Table 7.3 Tag encodings
    https://dwarfstd.org/doc/DWARF5.pdf
  * binutils include/dwarf2.def（DW_TAG / DW_AT / DW_FORM 的编号取值）
    https://sourceware.org/git/?p=binutils-gdb.git;a=blob_plain;f=include/dwarf2.def

刻意不含的东西：属性 Class 表上的地址重定位（需要重定位表配合）、
loclist / rnglist 表达式求值，以及各家 vendor 扩展。
本文件只做「把字节流还原成结构化条目」这一步。
"""

import struct

from dwarf_const import DW_TAG, DW_AT, DW_FORM, DW_CHILDREN_NO, DW_CHILDREN_YES

# ------------------------------------------------------------ LEB128 (§7.6)

# ULEB:低 7 位放数据,最高位作 continuation bit,字节序是 little-endian。
# SLEB:最后那个字节的第 6 位（值 0x40）是符号位。

def uleb128_encode(value):
    if value < 0:
        raise ValueError("ULEB128 不能编码负数: %d" % value)
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)

def uleb128_decode(data, off):
    result = 0
    shift = 0
    while True:
        byte = data[off]
        off += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, off
        shift += 7

def sleb128_encode(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7  # Python 的 >> 是算术右移,天然做符号扩展
        done = (value == 0 and not (byte & 0x40)) or (value == -1 and (byte & 0x40))
        out.append(byte | (0x80 if not done else 0x00))
        if done:
            return bytes(out)

def sleb128_decode(data, off):
    result = 0
    shift = 0
    while True:
        byte = data[off]
        off += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if not (byte & 0x80):
            # 最后一个字节的第 6 位是符号位 -> 补足到 Python 的无限宽度整数
            if byte & 0x40:
                result -= 1 << shift
            return result, off

# --------------------------------------------------- initial length (§7.4)

DWARF64_MARKER = 0xFFFFFFFF

def read_initial_length(data, off):
    """§7.4：先读 4 字节；等于 0xffffffff 则是 64 位 DWARF,真长度在后 8 字节。

    返回 (length, offset_size, new_off)。offset_size 决定所有 form-based
    偏移量（debug_abbrev_offset 等）是 4 还是 8 字节。
    """
    first = struct.unpack_from("<I", data, off)[0]
    if first != DWARF64_MARKER:
        return first, 4, off + 4
    length = struct.unpack_from("<Q", data, off + 4)[0]
    return length, 8, off + 12

# ------------------------------------------------------ unit type (Table 7.2)

DW_UT = {
    0x01: "DW_UT_compile",
    0x02: "DW_UT_type",
    0x03: "DW_UT_partial",
    0x04: "DW_UT_skeleton",
    0x05: "DW_UT_split_compile",
    0x06: "DW_UT_split_type",
    0x80: "DW_UT_lo_user",
    0xFF: "DW_UT_hi_user",
}

# --------------------------------------------------- CU header (§7.5.1.1)

def parse_cu_header(data, off=0):
    """解析一个 DWARF5 编译单元头。

    字段顺序（§7.5.1.1）：unit_length / version(uhalf) / unit_type(ubyte) /
    address_size(ubyte) / debug_abbrev_offset(4 或 8 字节)。
    unit_type 与 version 是 DWARF5 新增的版本辨认点——version 必须为 5。
    """
    length, offset_size, pos = read_initial_length(data, off)
    fmt = "<I" if offset_size == 4 else "<Q"
    version = struct.unpack_from("<H", data, pos)[0]
    pos += 2
    unit_type = data[pos]
    pos += 1
    address_size = data[pos]
    pos += 1
    abbrev_off = struct.unpack_from(fmt, data, pos)[0]
    pos += offset_size
    return {
        "unit_length": length,
        "offset_size": offset_size,
        "version": version,
        "unit_type": unit_type,
        "unit_type_name": DW_UT.get(unit_type, "reserved"),
        "address_size": address_size,
        "debug_abbrev_offset": abbrev_off,
        "header_size": pos - off,
        "dies_offset": pos,
        "unit_end": off + (4 if offset_size == 4 else 12) + length,
    }

# ------------------------------------------------------ abbreviations (§7.5.3)

# DW_TAG 取值取自 DWARF5 Table 7.3（也可在 binutils dwarf2.def 逐条核对）

class Reader(object):
    """带 position 的字节流读取器。"""

    def __init__(self, data, off=0):
        self.data = data
        self.off = off

    def u8(self):
        v = self.data[self.off]
        self.off += 1
        return v

    def u16(self):
        v = struct.unpack_from("<H", self.data, self.off)[0]
        self.off += 2
        return v

    def u32(self):
        v = struct.unpack_from("<I", self.data, self.off)[0]
        self.off += 4
        return v

    def u64(self):
        v = struct.unpack_from("<Q", self.data, self.off)[0]
        self.off += 8
        return v

    def bytes(self, n):
        v = self.data[self.off:self.off + n]
        self.off += n
        return bytes(v)

    def uleb(self):
        v, self.off = uleb128_decode(self.data, self.off)
        return v

    def sleb(self):
        v, self.off = sleb128_decode(self.data, self.off)
        return v

    def cstr(self):
        end = self.data.index(b"\x00", self.off)
        v = self.data[self.off:end].decode("utf-8", "replace")
        self.off = end + 1
        return v

def parse_abbrev_table(data, off=0):
    """§7.5.3：一串缩写声明,code=0 结束整表。

    每条声明 = ULEB(code) ULEB(tag) ubyte(DW_children) [(ULEB at, ULEB form)],
    属性对以 (0, 0) 收尾。注意 DW_FORM_implicit_const 在缩写表里**多带一个
    SLEB 常量**——它不在 DIE 数据里,而是写在缩写表侧。
    """
    rd = Reader(data, off)
    table = {}
    while True:
        code = rd.uleb()
        if code == 0:
            break
        tag = rd.uleb()
        has_children = rd.u8()
        attrs = []
        while True:
            at = rd.uleb()
            form = rd.uleb()
            implicit = rd.sleb() if form == 0x21 else None
            if at == 0 and form == 0:
                break
            attrs.append((at, form, implicit))
        table[code] = {"tag": tag, "has_children": has_children, "attrs": attrs}
    return table, rd.off

# -------------------------------------------------------------- DIE 取值

def read_form(rd, form, cu, str_sec=None):
    """按 form 读一个属性值。cu 提供 offset_size / address_size。"""
    name = DW_FORM.get(form)
    if name is None:
        raise ValueError("未知 DW_FORM 0x%x" % form)
    if name == "DW_FORM_addr":
        return rd.u64() if cu["address_size"] == 8 else rd.u32()
    if name in ("DW_FORM_strp", "DW_FORM_line_strp", "DW_FORM_sec_offset"):
        raw = rd.u64() if cu["offset_size"] == 8 else rd.u32()
        if name != "DW_FORM_sec_offset" and str_sec is not None:
            end = str_sec.index(b"\x00", raw)
            return str_sec[raw:end].decode("utf-8", "replace")
        return raw
    if name == "DW_FORM_string":
        return rd.cstr()
    if name == "DW_FORM_data1":
        return rd.u8()
    if name == "DW_FORM_data2":
        return rd.u16()
    if name == "DW_FORM_data4":
        return rd.u32()
    if name == "DW_FORM_data8":
        return rd.u64()
    if name == "DW_FORM_ref4":
        return rd.u32()
    if name == "DW_FORM_udata":
        return rd.uleb()
    if name == "DW_FORM_sdata":
        return rd.sleb()
    if name == "DW_FORM_strx1":
        return rd.u8()
    if name == "DW_FORM_addrx1":
        return rd.u8()
    # flag_present 不占字节,值恒为 1
    if name == "DW_FORM_flag_present":
        return 1
    raise NotImplementedError("本 demo 未实现 form %s" % name)

def parse_die(rd, abbrevs, cu, str_sec=None, offset_size=None):
    """读一个 DIE。abbrev code 为 0 表示 null entry（兄弟链表的终止形式）。

    返回 dict；code==0 时返回 {"tag": None, ...}。
    """
    code = rd.uleb()
    if code == 0:
        return {"abbrev_code": 0, "tag": None, "attrs": {}}
    if code not in abbrevs:
        raise ValueError("缩写表缺少 code %d" % code)
    spec = abbrevs[code]
    attrs = {}
    for at, form, implicit in spec["attrs"]:
        if form == 0x21:  # implicit_const：值写在缩写表里,DIE 侧一个字节都不占
            attrs[DW_AT.get(at, "DW_AT_0x%x" % at)] = implicit
            continue
        attrs[DW_AT.get(at, "DW_AT_0x%x" % at)] = read_form(rd, form, cu, str_sec)
    return {
        "abbrev_code": code,
        "tag": spec["tag"],
        "tag_name": DW_TAG.get(spec["tag"], "DW_TAG_0x%x" % spec["tag"]),
        "has_children": spec["has_children"],
        "attrs": attrs,
    }

def parse_dies(data, cu, abbrevs, str_sec=None, limit=64):
    """线性扫出一个 CU 的全部顶层 DIE（不做 children 递归）。"""
    rd = Reader(data, cu["dies_offset"])
    out = []
    while True:
        die = parse_die(rd, abbrevs, cu, str_sec)
        out.append(die)
        if die["tag"] is None or len(out) >= limit:
            break
    return out, rd.off
