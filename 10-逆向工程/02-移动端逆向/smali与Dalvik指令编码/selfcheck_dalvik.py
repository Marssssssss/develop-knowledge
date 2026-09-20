"""Dalvik 指令编码自检：逐条对照 AOSP 官方格式表与 opcode 表。

运行：python selfcheck_dalvik.py
"""

from dalvik_isa import (
    OPCODES, FORMAT_LAYOUT, TYPE_LETTER_BITS,
    IDENT_PACKED_SWITCH, IDENT_SPARSE_SWITCH, IDENT_FILL_ARRAY_DATA,
    parse_format_id, format_units, decode,
)
from dalvik_encode import (
    encode, arg_registers_35c, arg_registers_3rc,
    packed_switch_units, sparse_switch_units, fill_array_data_units,
    branch_offset_ok, disassemble,
)

PASS = [0]


def eq(a, b, label):
    assert a == b, "FAILED: %s (期望 %r 实际 %r)" % (label, b, a)
    PASS[0] += 1


def ok(cond, label):
    assert cond, "FAILED: " + label
    PASS[0] += 1


# ---------- 1. 格式 ID 的命名规则（官方「格式 ID」） ----------

eq(parse_format_id("21t"), (2, "1", "t"), "21t = 2 单元 / 1 寄存器 / 分支目标")
eq(parse_format_id("35c"), (3, "5", "c"), "35c = 3 单元 / 最多 5 寄存器 / 常量池索引")
eq(parse_format_id("3rc"), (3, "r", "c"), "3rc 的 'r' 表示编码了一系列寄存器")
eq(parse_format_id("51l"), (5, "1", "l"), "51l = 5 单元 / 1 寄存器 / 64 位立即数")
eq(parse_format_id("10x"), (1, "0", "x"), "10x = 1 单元 / 0 寄存器 / 无额外数据")
# 官方：静态链接格式加 's'，内联链接加 'i'
eq(parse_format_id("22cs"), (2, "2", "c"), "后缀 s 表示建议的静态链接格式")
eq(parse_format_id("35mi"), (3, "5", "m"), "后缀 i 表示建议的内联链接格式")
eq(format_units("35c"), 3, "35c 占 3 个代码单元")
eq(format_units("51l"), 5, "51l 占 5 个代码单元")

# ---------- 2. 类型代码字母表（官方「类型代码字母的完整列表」） ----------

for letter, bits in (("b", 8), ("c", 16), ("f", 16), ("h", 16), ("i", 32), ("l", 64),
                     ("m", 16), ("n", 4), ("s", 16), ("t", 8), ("x", 0)):
    eq(TYPE_LETTER_BITS[letter], bits, "类型字母 %s 的位宽" % letter)

# ---------- 3. opcode 表（官方「Dalvik 字节码」） ----------

for op, (name, fmt) in ((0x00, ("nop", "10x")), (0x01, ("move", "12x")),
                        (0x0A, ("move-result", "11x")), (0x0E, ("return-void", "10x")),
                        (0x0F, ("return", "11x")), (0x12, ("const/4", "11n")),
                        (0x13, ("const/16", "21s")), (0x14, ("const", "31i")),
                        (0x15, ("const/high16", "21h")), (0x1A, ("const-string", "21c")),
                        (0x1B, ("const-string/jumbo", "31c")), (0x1C, ("const-class", "21c")),
                        (0x28, ("goto", "10t")), (0x29, ("goto/16", "20t")),
                        (0x2A, ("goto/32", "30t"))):
    eq(OPCODES[op], (name, fmt), "opcode 0x%02x 是 %s / %s" % (op, name, fmt))

for i, name in enumerate(("if-eq", "if-ne", "if-lt", "if-ge", "if-gt", "if-le")):
    eq(OPCODES[0x32 + i], (name, "22t"), "opcode 0x%02x 是 %s" % (0x32 + i, name))
for i, name in enumerate(("if-eqz", "if-nez", "if-ltz", "if-gez", "if-gtz", "if-lez")):
    eq(OPCODES[0x38 + i], (name, "21t"), "opcode 0x%02x 是 %s" % (0x38 + i, name))
for i, name in enumerate(("invoke-virtual", "invoke-super", "invoke-direct",
                          "invoke-static", "invoke-interface")):
    eq(OPCODES[0x6E + i], (name, "35c"), "opcode 0x%02x 是 %s" % (0x6E + i, name))
    eq(OPCODES[0x74 + i], (name + "/range", "3rc"), "opcode 0x%02x 是 %s/range" % (0x74 + i, name))
eq(OPCODES[0xFA], ("invoke-polymorphic", "45cc"), "invoke-polymorphic 用 45cc")
eq(OPCODES[0xFB], ("invoke-polymorphic/range", "4rcc"), "invoke-polymorphic/range 用 4rcc")
eq(OPCODES[0xFE], ("const-method-handle", "21c"), "const-method-handle（039 起）")
eq(OPCODES[0xFF], ("const-method-type", "21c"), "const-method-type（039 起）")
# 官方：invoke-kind 与 /range 变体的格式不同但语义一致
for op in range(0x6E, 0x73):
    eq(OPCODES[op][1], "35c", "invoke-kind 用 35c")
    eq(OPCODES[op + 6][0], OPCODES[op][0] + "/range", "/range 变体同名")
# 每个 opcode 的格式都必须在格式表里
for op, (name, fmt) in OPCODES.items():
    ok(fmt in FORMAT_LAYOUT, "%s 的格式 %s 已登记" % (name, fmt))

# ---------- 4. 编解码往返（官方位布局） ----------

CASES = [
    ("10x", 0x0E, {}),
    ("12x", 0x01, {"A": 3, "B": 12}),
    ("11n", 0x12, {"A": 5, "B": -3}),
    ("11x", 0x0F, {"A": 200}),
    ("10t", 0x28, {"A": -2}),
    ("20t", 0x29, {"A": -300}),
    ("21t", 0x38, {"A": 7, "B": 1234}),
    ("21s", 0x13, {"A": 9, "B": -2500}),
    ("21h", 0x15, {"A": 1, "B": 0x1234}),
    ("21c", 0x1A, {"A": 4, "B": 0xABCD}),
    ("23x", 0x00, {"A": 1, "B": 2, "C": 3}),
    ("22b", 0x00, {"A": 1, "B": 2, "C": -5}),
    ("22t", 0x32, {"A": 2, "B": 3, "C": -100}),
    ("22s", 0x00, {"A": 1, "B": 2, "C": 3000}),
    ("22c", 0x00, {"A": 1, "B": 2, "C": 0xBEEF}),
    ("30t", 0x2A, {"A": -70000}),
    ("32x", 0x00, {"A": 65535, "B": 1}),
    ("31i", 0x14, {"A": 6, "B": -123456}),
    ("31c", 0x1B, {"A": 2, "B": 0x7FFFFFFF}),
    ("35c", 0x6E, {"A": 5, "G": 11, "B": 0x1234, "C": 1, "D": 2, "E": 3, "F": 4}),
    ("3rc", 0x74, {"A": 4, "B": 0x2222, "C": 16}),
    ("45cc", 0xFA, {"A": 3, "G": 0, "B": 0x55, "C": 1, "D": 2, "E": 3, "H": 0x77}),
    ("4rcc", 0xFB, {"A": 5, "B": 0x55, "C": 8, "H": 0x77}),
    ("51l", 0x00, {"A": 3, "B": 0x0123456789ABCDEF}),
]
for fmt, op, fields in CASES:
    units = encode(fmt, op, **fields)
    eq(len(units), FORMAT_LAYOUT[fmt][0], "%s 编码出 %d 个代码单元" % (fmt, FORMAT_LAYOUT[fmt][0]))
    back = decode(units, fmt)
    eq(back["op"], op, "%s 往返后 opcode 不变" % fmt)
    eq(back["format"], fmt, "%s 往返后格式不变" % fmt)
    for k, v in fields.items():
        eq(back[k], v, "%s 往返后字段 %s 不变" % (fmt, k))

# 10x：高字节是被忽略的 ØØ（必须为 0 才是规范形式）
eq(encode("10x", 0x0E), [0x000E], "return-void 编码成单个 0x000e")
eq(decode([0x000E])["mnemonic"], "return-void", "0x000e 解成 return-void")
# 20t/30t/32x 的首单元低字节是 op、高字节是 ØØ
eq(encode("20t", 0x29, A=5)[0], 0x0029, "goto/16 首单元高字节为 0")

# ---------- 5. 35c 与 3rc 的参数寄存器语义 ----------

f5 = decode(encode("35c", 0x6E, A=5, G=11, B=0x1234, C=1, D=2, E=3, F=4))
eq(arg_registers_35c(f5), [1, 2, 3, 4, 11], "A=5 时第五个参数寄存器取自 G 位")
f3 = decode(encode("35c", 0x71, A=3, G=0, B=0x1234, C=7, D=8, E=9))
eq(arg_registers_35c(f3), [7, 8, 9], "A=3 时只用 C/D/E")
f0 = decode(encode("35c", 0x71, A=0, B=0x1234))
eq(arg_registers_35c(f0), [], "A=0 时 invoke-static 无参数")
# 官方：A 决定计数 0..5，A<5 时 G 位不使用
for a in range(6):
    dec = decode(encode("35c", 0x6E, A=a, G=0, B=1, C=0, D=1, E=2, F=3))
    eq(len(arg_registers_35c(dec)), a, "A=%d 时参数寄存器个数" % a)
# 官方：NNNN = CCCC + AA - 1
fr = decode(encode("3rc", 0x74, A=4, B=0x11, C=16))
eq(arg_registers_3rc(fr), [16, 17, 18, 19], "3rc 的范围是 C .. C+A-1")
fr1 = decode(encode("3rc", 0x74, A=1, B=0x11, C=5))
eq(arg_registers_3rc(fr1), [5], "A=1 时只有一个寄存器")
fr0 = decode(encode("3rc", 0x74, A=0, B=0x11, C=5))
eq(arg_registers_3rc(fr0), [], "A=0 时 3rc 是空范围")

# ---------- 6. 三个 payload 伪运算码 ----------

eq(IDENT_PACKED_SWITCH, 0x0100, "packed-switch-payload 的 ident")
eq(IDENT_SPARSE_SWITCH, 0x0200, "sparse-switch-payload 的 ident")
eq(IDENT_FILL_ARRAY_DATA, 0x0300, "fill-array-data-payload 的 ident")
eq(packed_switch_units(3), 10, "官方：packed-switch 单元数 = size*2 + 4")
eq(packed_switch_units(0), 4, "packed-switch 空表也有 4 个单元")
eq(sparse_switch_units(2), 10, "官方：sparse-switch 单元数 = size*4 + 2")
eq(sparse_switch_units(1), 6, "sparse-switch 单条目 6 个单元")
eq(fill_array_data_units(4, 2), 8, "官方：fill-array-data 单元数 = (size*width+1)/2 + 4")
eq(fill_array_data_units(3, 1), 6, "奇数字节时的整除口径")
eq(fill_array_data_units(1, 8), 8, "单个 8 字节元素")

# ---------- 7. 分支偏移不得为 0 ----------

ok(branch_offset_ok(1), "非零分支偏移合法")
ok(branch_offset_ok(-4), "负分支偏移合法")
ok(not branch_offset_ok(0), "官方：分支偏移量不得为 0")

# ---------- 8. 反汇编一段指令流 ----------

stream = []
stream += encode("10t", 0x28, A=3)                             # goto +3
stream += encode("21c", 0x1A, A=0, B=2)                        # const-string v0, string@2
stream += encode("35c", 0x71, A=2, G=0, B=5, C=0, D=1)         # invoke-static {v0, v1}
stream += encode("3rc", 0x74, A=3, B=5, C=10)                  # invoke-virtual/range {v10..v12}
stream += encode("10x", 0x0E)                                  # return-void
text = disassemble(stream)
ok("goto +3" in text, "反汇编出 goto +3")
ok("const-string v0, string@2" in text, "反汇编出 const-string")
ok("invoke-static {v0, v1}" in text, "反汇编出 invoke-static 的参数列表")
ok("invoke-virtual/range {v10 .. v12}" in text, "反汇编出 /range 的寄存器区间")
ok("return-void" in text, "反汇编出 return-void")
eq(len(text.split("\n")), 5, "共 5 条指令")
# 指令流长度必须与各格式单元数之和一致
eq(len(stream), 1 + 2 + 3 + 3 + 1, "指令流长度等于各指令代码单元之和")

print("PASS %d 项断言全部通过" % PASS[0])
