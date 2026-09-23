"""654 · Ghidra SLEIGH 处理器规范语言演示。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sleigh as SL  # noqa: E402


def banner(t):
    print("\n== %s ==" % t)


def build_spec():
    """照 6502.slaspec 的形态搭一个玩具 ISA。"""
    sp = SL.Spec()
    sp.define_endian("little")
    sp.define_alignment(1)
    sp.define_space("RAM", {"type": "ram_space", "size": 2, "default": True})
    sp.define_register(0x00, 1, ["A", "X", "Y"])

    tok = SL.Token("opbyte", 8)
    tok.add_field("op", 0, 7)
    tok.add_field("op6", 2, 7)
    tok.add_field("r1", 0, 1)
    sp.define_token(tok)
    sp.attach_variables(["r1"], ["R0", "R1", "R2", "R3"])

    sp.add(SL.Constructor("", "halt", [], SL.parse_pattern("op=0x00", sp)))
    sp.add(SL.Constructor("", "nop", [], SL.parse_pattern("op=0xEA", sp)))
    sp.add(SL.Constructor("", "inc", ["r1"], SL.parse_pattern("op6=0x3E & r1", sp)))
    sp.add(SL.Constructor("", "lda", ["r1"], SL.parse_pattern("op6=0x3D & r1", sp)))
    return sp


def main():
    banner("1. token 的位编号：最低位是 0")
    t = SL.Token("word", 16)
    t.add_field("lo8", 0, 7)
    t.add_field("hi8", 8, 15)
    for e in ("big", "little"):
        v = t.value(b"\x12\x34", e)
        print("  %-6s -> 整数 0x%04x，hi8=0x%02x lo8=0x%02x"
              % (e, v, t.field_value("hi8", v), t.field_value("lo8", v)))

    banner("2. signed 属性与默认十六进制显示")
    d = SL.Token("data8", 8)
    d.add_field("rel", 0, 7, attrs=("signed",))
    print("  0xFE 作为 signed 是 %d，显示成 %s" % (d.field_value("rel", 0xFE),
                                                 d.display("rel", 0xFE)))

    banner("3. attach variables：字段变查表，索引从 0 起")
    sp = build_spec()
    print("  r1 的查表 =", sp.attach["r1"])
    for i in range(4):
        print("    r1=%d -> %s" % (i, sp.attach["r1"][i]))

    banner("4. 解码")
    for byte in (0x00, 0xEA, (0x3E << 2) | 2, (0x3D << 2) | 3):
        ins = SL.decode(sp, bytes([byte]), 0)
        print("  %#04x -> %s" % (byte, ins))
    print("  0xFF  ->", SL.decode(sp, b"\xFF", 0))

    banner("5. `...` 处理变长指令")
    v = SL.Spec()
    v.define_endian("little")
    vt = SL.Token("opbyte", 8)
    vt.add_field("op", 0, 7)
    v.define_token(vt)
    dt = SL.Token("data8", 8)
    dt.add_field("imm8", 0, 7)
    v.define_token(dt)
    v.add(SL.Constructor("", "lda", ["imm8"], SL.parse_pattern("op=0xA9 ... & imm8", v)))
    ins = SL.decode(v, b"\xA9\x99", 0)
    print("  A9 99 -> %s（长度 %d 字节，操作数 %s）" % (ins.mnemonic, ins.length, ins.operands))
    print("  A9    ->", SL.decode(v, b"\xA9", 0), "（字节不够）")


if __name__ == "__main__":
    main()
