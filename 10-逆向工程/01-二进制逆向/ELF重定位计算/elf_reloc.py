#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""x86-64 ELF 重定位：类型表、计算公式与字节落地。

数据来源（本轮实测下载并提取正文）：
  System V AMD64 psABI Draft 0.99.6 (July 2, 2012)
  §4.4 Relocation / §4.4.1 Relocation Types — Table 4.10「Relocation Types」
  https://refspecs.linuxfoundation.org/elf/x86_64-abi-0.99.pdf

关键点（均取自该文档原文，不是猜测）：
  * Table 4.10 每行三列的口径是「Name / Value / Field / Calculation」，
    本文件把 Calculation 逐条落成可执行的函数，Value 与 Field 逐条落成常量表。
  * 文档在表前的记号定义（第 69-70 页）：
      A   Represents the addend used to compute the value of the relocatable field.
      B   Represents the base address at which a shared object has been loaded into memory.
      G   offset into the GOT at which the relocation entry's symbol will reside.
      GOT the address of the global offset table.
      L   the place (address) of the PLT entry for a symbol.
      P   the place (address) of the storage unit being relocated (r_offset).
      S   the value of the symbol whose index resides in the relocation entry.
      Z   the size of the symbol whose index resides in the relocation entry.
  * 「The AMD64 ABI architectures uses only Elf64_Rela relocation entries with explicit
     addends.」—— 64 位 x86 只用 RELA，没有 REL，加数永远来自 r_addend 字段。
"""

import struct
import sys

# ---------------------------------------------------------------- 字段宽度

# Figure 4.1「Relocatable Fields」: word8/16/32/64 分别占 1/2/4/8 字节,
# 且 Table 4.10 里 R_X86_64_TLSDESC 写成 word64×2(两连 nos 的 word64)。
FIELD_SIZE = {
    "none": 0,
    "word8": 1,
    "word16": 2,
    "word32": 4,
    "word64": 8,
    "word64x2": 16,
}

FIELD_MASK = {
    "word8": 0xFF,
    "word16": 0xFFFF,
    "word32": 0xFFFFFFFF,
    "word64": 0xFFFFFFFFFFFFFFFF,
}


class RelocContext(object):
    """psABI §4.4.1 的八个记号,重命名成好写的形式。

    命名对照: sym=S, addend=A, place=P, base=B, plt=L, got_off=G,
              got=GOT, sym_size=Z。故意保持一一对应以便与文档对照阅读。
    """

    def __init__(self, sym=0, addend=0, place=0, base=0, plt=0,
                 got_off=0, got=0, sym_size=0):
        self.sym = sym
        self.addend = addend
        self.place = place
        self.base = base
        self.plt = plt
        self.got_off = got_off
        self.got = got
        self.sym_size = sym_size


# ---------------------------------------------------------------- Table 4.10

# (Value, Field, calc) —— calc 是 psABI 原文写的公式字符串,便于反向核对;
# 真正的实现在同名的 lambda / 函数里。
def f_pc(rel):
    return rel.sym + rel.addend - rel.place


def f_pc64(rel):
    return rel.sym + rel.addend - rel.place


RELOC_TABLE = {
    "R_X86_64_NONE": (0, "none", "none", lambda r: 0),
    "R_X86_64_64": (1, "word64", "S + A", lambda r: r.sym + r.addend),
    "R_X86_64_PC32": (2, "word32", "S + A - P", f_pc),
    "R_X86_64_GOT32": (3, "word32", "G + A", lambda r: r.got_off + r.addend),
    "R_X86_64_PLT32": (4, "word32", "L + A - P", lambda r: r.plt + r.addend - r.place),
    "R_X86_64_COPY": (5, "none", "none", lambda r: None),
    "R_X86_64_GLOB_DAT": (6, "word64", "S", lambda r: r.sym),
    "R_X86_64_JUMP_SLOT": (7, "word64", "S", lambda r: r.sym),
    "R_X86_64_RELATIVE": (8, "word64", "B + A", lambda r: r.base + r.addend),
    "R_X86_64_GOTPCREL": (9, "word32", "G + GOT + A - P",
                          lambda r: r.got_off + r.got + r.addend - r.place),
    "R_X86_64_32": (10, "word32", "S + A", lambda r: r.sym + r.addend),
    "R_X86_64_32S": (11, "word32", "S + A", lambda r: r.sym + r.addend),
    "R_X86_64_16": (12, "word16", "S + A", lambda r: r.sym + r.addend),
    "R_X86_64_PC16": (13, "word16", "S + A - P", f_pc),
    "R_X86_64_8": (14, "word8", "S + A", lambda r: r.sym + r.addend),
    "R_X86_64_PC8": (15, "word8", "S + A - P", f_pc),
    # 16..23 是 TLS 系,psABI 的 Calculation 列留空(由运行时(__tls_get_addr)
    # 或动态链接器按 §4.4.1 后半段的专项说明处理),故这里用 calc=None 表示
    # 「不在 ESPABI 通用公式表内」,而不是「恒等于 0」。
    "R_X86_64_DTPMOD64": (16, "word64", "", None),
    "R_X86_64_DTPOFF64": (17, "word64", "", None),
    "R_X86_64_TPOFF64": (18, "word64", "", None),
    "R_X86_64_TLSGD": (19, "word32", "", None),
    "R_X86_64_TLSLD": (20, "word32", "", None),
    "R_X86_64_DTPOFF32": (21, "word32", "", None),
    "R_X86_64_GOTTPOFF": (22, "word32", "", None),
    "R_X86_64_TPOFF32": (23, "word32", "", None),
    "R_X86_64_PC64": (24, "word64", "S + A - P", f_pc64),
    "R_X86_64_GOTOFF64": (25, "word64", "S + A - GOT", lambda r: r.sym + r.addend - r.got),
    "R_X86_64_GOTPC32": (26, "word32", "GOT + A - P", lambda r: r.got + r.addend - r.place),
    "R_X86_64_SIZE32": (32, "word32", "Z + A", lambda r: r.sym_size + r.addend),
    "R_X86_64_SIZE64": (33, "word64", "Z + A", lambda r: r.sym_size + r.addend),
    "R_X86_64_GOTPC32_TLSDESC": (34, "word32", "", None),
    "R_X86_64_TLSDESC_CALL": (35, "none", "", None),
    "R_X86_64_TLSDESC": (36, "word64x2", "", None),
    "R_X86_64_IRELATIVE": (37, "word64", "indirect (B + A)", lambda r: r.base + r.addend),
}

# 大代码模型(Table 4.11)
LARGE_MODEL_TABLE = {
    "R_X86_64_GOT64": (27, "word64", "G + A", lambda r: r.got_off + r.addend),
    "R_X86_64_GOTPCREL64": (28, "word64", "G + GOT - P + A",
                            lambda r: r.got_off + r.got - r.place + r.addend),
    "R_X86_64_GOTPC64": (29, "word64", "GOT - P + A",
                         lambda r: r.got - r.place + r.addend),
    "R_X86_64_GOTPLT64": (30, "word64", "G + A", lambda r: r.got_off + r.addend),
    "R_X86_64_PLTOFF64": (31, "word64", "L - GOT + A",
                          lambda r: r.plt - r.got + r.addend),
}


def reloc_value(name, rel):
    """按 Table 4.10/4.11 的 Calculation 列算出「要写进重定位位置的值」。

    对 Calculation 留空的类型返回 None —— 用 None 而不是 0,是因为 0 是一个
    合法的计算结果,两者不可混为一谈(见 §7 注意事项)。
    """
    table = RELOC_TABLE if name in RELOC_TABLE else LARGE_MODEL_TABLE
    if name not in table:
        raise KeyError("unknown relocation: %s" % name)
    calc = table[name][3]
    if calc is None:
        return None
    return calc(rel)


def field_of(name):
    table = RELOC_TABLE if name in RELOC_TABLE else LARGE_MODEL_TABLE
    return table[name][1]


def value_of(name):
    table = RELOC_TABLE if name in RELOC_TABLE else LARGE_MODEL_TABLE
    return table[name][0]


# ------------------------------------------------------- Elf64_Rela 记录

# Elf64_Rela: r_offset(8) r_info(8) r_addend(8) —— 共 24 字节。
# r_info 的高 32 位是符号表索引,低 32 位是重定位类型。
RELA_STRUCT = struct.Struct("<QQq")  # addend 是有符号的
RELA_SIZE = RELA_STRUCT.size


def rela_info(sym_index, rtype):
    return ((sym_index & 0xFFFFFFFF) << 32) | (rtype & 0xFFFFFFFF)


def rela_sym(info):
    return (info >> 32) & 0xFFFFFFFF


def rela_type(info):
    return info & 0xFFFFFFFF


def encode_rela(r_offset, sym_index, rtype, addend):
    return RELA_STRUCT.pack(r_offset, rela_info(sym_index, rtype), addend)


def decode_rela(blob, index=0):
    off, info, addend = RELA_STRUCT.unpack_from(blob, index * RELA_SIZE)
    return {"r_offset": off, "sym": rela_sym(info), "type": rela_type(info),
            "addend": addend}


# ------------------------------------------------------------- 写回字节

_STRUCT_FMT = {"word8": "<B", "word16": "<H", "word32": "<I", "word64": "<Q"}


def fits(value, field):
    """判断 value 能否塞进 field 指定的位宽(先按无符号掩码归一化)。

    注意 psABI 里 32 位字段有两类:word32(如 R_X86_64_32)与 word32 带符号
    扩展语义的类型。此处统一用「无符号位宽」判溢出,是否要求符号可扩展由
    调用方决定(见 check_signed32)。
    """
    if field not in FIELD_MASK:
        return True
    masked = value & FIELD_MASK[field]
    return masked == (value & 0xFFFFFFFFFFFFFFFF) or 0 <= value <= FIELD_MASK[field]


def check_signed32(value):
    """word32 字段被当作有符号 32 位用时(典型:PLT32/PC32 的位移),
    值必须能放进 [-2^31, 2^31-1]。"""
    return -0x80000000 <= value <= 0x7FFFFFFF


def apply_reloc(image, place, field, value, base_of_image=0):
    """把 value 按 field 宽度写进 image 的 place 处。

    place 是**运行期虚拟地址**;image 被视为从 base_of_image 开始的一片内存。
    返回新的 bytes。
    """
    size = FIELD_SIZE[field]
    if size == 0:
        return image  # R_X86_64_NONE / R_X86_64_COPY 等不写字面值
    if field == "word64x2":
        raise ValueError("R_X86_64_TLSDESC 写的是两个 word64,请用 apply_tlsdesc")
    if not fits(value, field):
        raise ValueError("value 0x%x 放不进 %s" % (value, field))
    lo = place - base_of_image
    if lo < 0 or lo + size > len(image):
        raise ValueError("place 0x%x 越界(image len=%d)" % (place, len(image)))
    body = struct.pack(_STRUCT_FMT[field], value & FIELD_MASK[field])
    return image[:lo] + body + image[lo + size:]


def apply_tlsdesc(image, place, resolver_fn, argument, base_of_image=0):
    """R_X86_64_TLSDESC 落地为「一对 word64:(函数指针, 参数)」。

    psABI 原文:resolves to a pair of word64s, called TLS Descriptor, the first of
    which is a pointer to a function, followed by an argument.
    """
    lo = place - base_of_image
    body = struct.pack("<QQ", resolver_fn, argument)
    return image[:lo] + body + image[lo + 16:]
