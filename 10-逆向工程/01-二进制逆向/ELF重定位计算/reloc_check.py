#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ELF 重定位自检入口 —— python reloc_check.py

与 elf_reloc.py 分离只为实现「单文件 ≤ 300 行」,断言逻辑本身不变。
"""

import struct

from elf_reloc import *

FAIL = []


def check(cond, msg):
    if not cond:
        FAIL.append(msg)


def raises(fn, exc, label):
    """断言 fn() 会抛 exc:既能抓住『该报错却不报』,也防止 count 被 `or True` 空转。"""
    try:
        fn()
    except exc:
        return True
    except Exception as e:  # 抛了别的异常类型也算失败
        FAIL.append("%s:应抛 %s,实抛 %r" % (label, exc.__name__, e))
        return False
    FAIL.append("%s:应抛 %s 却没有" % (label, exc.__name__))
    return False


def main():
    print("== 1. Table 4.10 的 Value 编号逐条核对 ==")
    expect = {
        "R_X86_64_NONE": 0, "R_X86_64_64": 1, "R_X86_64_PC32": 2,
        "R_X86_64_GOT32": 3, "R_X86_64_PLT32": 4, "R_X86_64_COPY": 5,
        "R_X86_64_GLOB_DAT": 6, "R_X86_64_JUMP_SLOT": 7,
        "R_X86_64_RELATIVE": 8, "R_X86_64_GOTPCREL": 9, "R_X86_64_32": 10,
        "R_X86_64_32S": 11, "R_X86_64_16": 12, "R_X86_64_PC16": 13,
        "R_X86_64_8": 14, "R_X86_64_PC8": 15, "R_X86_64_DTPMOD64": 16,
        "R_X86_64_DTPOFF64": 17, "R_X86_64_TPOFF64": 18,
        "R_X86_64_TLSGD": 19, "R_X86_64_TLSLD": 20,
        "R_X86_64_DTPOFF32": 21, "R_X86_64_GOTTPOFF": 22,
        "R_X86_64_TPOFF32": 23, "R_X86_64_PC64": 24,
        "R_X86_64_GOTOFF64": 25, "R_X86_64_GOTPC32": 26,
        "R_X86_64_SIZE32": 32, "R_X86_64_SIZE64": 33,
        "R_X86_64_GOTPC32_TLSDESC": 34, "R_X86_64_TLSDESC_CALL": 35,
        "R_X86_64_TLSDESC": 36, "R_X86_64_IRELATIVE": 37,
    }
    for name, val in expect.items():
        check(name in RELOC_TABLE, "%s 不在表里" % name)
        check(value_of(name) == val, "%s 应为 %d,实为 %d" % (name, val, value_of(name)))
    check(len(RELOC_TABLE) == len(expect),
          "Table 4.10 条目数应正好 %d,实为 %d" % (len(expect), len(RELOC_TABLE)))
    # 27..31 属于大代码模型表,刻意不在通用表里
    for nm in ("R_X86_64_GOT64", "R_X86_64_GOTPLT64", "R_X86_64_PLTOFF64"):
        check(nm not in RELOC_TABLE and nm in LARGE_MODEL_TABLE,
              "%s 应只在 Table 4.11" % nm)
    check(value_of("R_X86_64_GOTPLT64") == 30, "R_X86_64_GOTPLT64 = 30")
    check(value_of("R_X86_64_PLTOFF64") == 31, "R_X86_64_PLTOFF64 = 31")

    print("== 2. Calculation 列逐条执行 ==")
    # P 的位置取自 r_offset;A 恒来自 r_addend(RELA 无隐式加数)
    ctx = RelocContext(sym=0x401100, addend=-4, place=0x401006, base=0x7f0000000000,
                       plt=0x401020, got_off=0x18, got=0x403ff0, sym_size=0x40)
    check(reloc_value("R_X86_64_64", ctx) == 0x401100 - 4, "S + A")
    check(reloc_value("R_X86_64_PC32", ctx) == 0x401100 - 4 - 0x401006,
          "S + A - P = 0xF6")
    check(reloc_value("R_X86_64_PC32", ctx) == 0xF6, "PC32 = 0xF6")
    check(reloc_value("R_X86_64_PLT32", ctx) == 0x401020 - 4 - 0x401006, "L + A - P")
    check(reloc_value("R_X86_64_GLOB_DAT", ctx) == 0x401100, "GLOB_DAT = S")
    check(reloc_value("R_X86_64_JUMP_SLOT", ctx) == 0x401100, "JUMP_SLOT = S")
    check(reloc_value("R_X86_64_RELATIVE", ctx) == 0x7f0000000000 - 4, "B + A")
    check(reloc_value("R_X86_64_GOTPCREL", ctx) == 0x18 + 0x403ff0 - 4 - 0x401006,
          "G + GOT + A - P")
    check(reloc_value("R_X86_64_GOT32", ctx) == 0x18 - 4, "G + A")
    check(reloc_value("R_X86_64_GOTOFF64", ctx) == 0x401100 - 4 - 0x403ff0, "S + A - GOT")
    check(reloc_value("R_X86_64_GOTPC32", ctx) == 0x403ff0 - 4 - 0x401006, "GOT + A - P")
    check(reloc_value("R_X86_64_SIZE32", ctx) == 0x40 - 4, "Z + A")
    check(reloc_value("R_X86_64_SIZE64", ctx) == 0x40 - 4, "Z + A")
    check(reloc_value("R_X86_64_PC64", ctx) == 0x401100 - 4 - 0x401006, "PC64 = S+A-P")

    print("== 3. Calculation 留空的类型必须返回 None,不是 0 ==")
    for nm in ("R_X86_64_NONE", "R_X86_64_COPY", "R_X86_64_TLSGD", "R_X86_64_TLSDESC"):
        got = reloc_value(nm, ctx)
        check(nm == "R_X86_64_NONE" or got is None,
              "%s 无通用公式,应返回 None(实为 %r)" % (nm, got))
    check(reloc_value("R_X86_64_NONE", ctx) == 0, "NONE 表中公式就是 none")

    print("== 4. 字段宽度 ==")
    for nm, sz in (("R_X86_64_64", 8), ("R_X86_64_PC32", 4), ("R_X86_64_16", 2),
                   ("R_X86_64_8", 1), ("R_X86_64_TLSDESC", 16),
                   ("R_X86_64_COPY", 0),                    ("R_X86_64_TLSDESC_CALL", 0)):
        check(FIELD_SIZE[field_of(nm)] == sz, "%s 字段宽应为 %d" % (nm, sz))

    print("== 5. Elf64_Rela:r_info 高 32 位符号、低 32 位类型 ==")
    check(RELA_SIZE == 24, "Elf64_Rela 长 24 字节,实为 %d" % RELA_SIZE)
    check(rela_info(7, 2) == (7 << 32) | 2, "r_info = (sym << 32) | type")
    info = rela_info(7, 2)
    check(rela_sym(info) == 7 and rela_type(info) == 2, "r_info 拆分可逆")
    blob = encode_rela(0x401006, 7, 2, -4) + encode_rela(0x40100a, 9, 4, -4)
    r0, r1 = decode_rela(blob, 0), decode_rela(blob, 1)
    check(r0["r_offset"] == 0x401006 and r0["sym"] == 7 and r0["type"] == 2
          and r0["addend"] == -4, "RELA 记录 round-trip(abs)")
    check(r1["r_offset"] == 0x40100a and r1["type"] == 4 and r1["addend"] == -4,
          "RELA 记录 round-trip(call)")
    # 「只有 RELA」这条意味着:加数来自显式的 r_addend 字段,
    # 修改它就改结果 —— 与 REL(隐含加数)型 ABI 的关键差异。
    check(reloc_value("R_X86_64_64", RelocContext(sym=0x100, addend=0)) == 0x100,
          "addend=0 时 S + A = S")
    check(reloc_value("R_X86_64_64", RelocContext(sym=0x100, addend=8)) == 0x108,
          "addend=8 → S + A = S + 8")

    print("== 6. 写回字节与溢出 ==")
    img = bytearray(b"\x00" * 0x40)
    out = apply_reloc(bytes(img), 0x401000, "word32", 0xEFBEADDE, base_of_image=0x401000)
    check(out[0:4] == bytes([0xDE, 0xAD, 0xBE, 0xEF]), "小端写入 word32")
    out64 = apply_reloc(bytes(img), 0x401000, "word64", 0x1122334455667788,
                        base_of_image=0x401000)
    check(out64[0:8] == bytes([0x88, 0x77, 0x66, 0x55, 0x44, 0x33, 0x22, 0x11]),
          "小端写入 word64")
    raises(lambda: apply_reloc(bytes(img), 0x401000, "word64", 1,
                               base_of_image=0x401040), ValueError, "place 越界")
    # word32 溢出:0x1_0000_0000 放不进 4 字节
    check(not fits(0x100000000, "word32"), "超过 32 位不算 fits")
    check(fits(0xFFFFFFFF, "word32"), "0xFFFFFFFF 放得进 word32")
    raises(lambda: apply_reloc(bytes(img), 0x401000, "word32", 0x100000000,
                               base_of_image=0x401000), ValueError, "word32 溢出")
    raises(lambda: field_of("R_X86_64_BOGUS"), KeyError, "未知重定位类型")
    raises(lambda: apply_reloc(bytes(img), 0x401000, field_of("R_X86_64_TLSDESC"), 0,
                               base_of_image=0x401000), ValueError, "TLSDESC 走专用通道")

    print("== 7. PC 相对字段必须先看有符号 32 位范围 ==")
    check(check_signed32(-0x80000000) and check_signed32(0x7FFFFFFF), "边界在范围内")
    check(not check_signed32(0x80000000), "超过 +2^31 视为不可带符号使用")
    check(not check_signed32(-0x80000001), "低于 -2^31 视为不可带符号使用")
    # 同一个值,fits 通过但 signed32 不通过 —— 这是实践中最容易踩的区分
    check(fits(0x80000000, "word32") and not check_signed32(0x80000000),
          "0x80000000 能塞进 4 字节但不能当有符号位移")

    print("== 8. R_X86_64_TLSDESC:一对 word64 ==")
    out = bytes(bytearray(b"\x00" * 0x40))
    out2 = apply_tlsdesc(out, 0x401000, 0x7f000000abcd, 0x21, base_of_image=0x401000)
    fn, arg = struct.unpack_from("<QQ", out2, 0)
    check(fn == 0x7f000000abcd and arg == 0x21, "TLSDESC = (resolver, argument)")
    check(len(out2) - len(out) == 0, "TLSDESC 就地覆盖 16 字节,长度不变")

    print("== 9. NONE / COPY 不写字面值 ==")
    before = bytes(bytearray(b"\xAA" * 0x20))
    after = apply_reloc(before, 0x1000, field_of("R_X86_64_NONE"), 0x1234,
                        base_of_image=0x1000)
    check(after == before, "R_X86_64_NONE 写操作是 no-op")

    print("== 10. IRELATIVE:值来自 resolver(B+A) ==")
    # psABI:indirect (B + A);B+A 给出 ifunc resolver 的地址,
    # 真正写进去的是调它之后拿到的返回值。这里用字典模拟「解析一次」。
    resolvers = {0x7f0000000000 + 8: lambda: 0xdeadbeef}
    target = reloc_value("R_X86_64_IRELATIVE", RelocContext(base=0x7f0000000000, addend=8))
    check(target == 0x7f0000000008, "IRELATIVE 先算出 B + A")
    check(resolvers[target]() == 0xdeadbeef, "再调它拿返回值作为最终写入值")

    print("\n结果: %d 项失败" % len(FAIL))
    for x in FAIL:
        print("  FAIL: %s" % x)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
