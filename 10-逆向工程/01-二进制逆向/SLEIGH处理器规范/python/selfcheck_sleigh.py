"""654 Ghidra SLEIGH 处理器规范 —— 自检（实跑）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sleigh as SL  # noqa: E402

PASS = 0
FAIL = []


def check(c, m):
    global PASS
    if c:
        PASS += 1
    else:
        FAIL.append(m)


def eq(a, b, m):
    check(a == b, "%s: 期望 %r 实得 %r" % (m, b, a))


# ------------------------------------------------ 定义语句

sp = SL.Spec()
sp.define_endian("little")
eq(sp.endian, "little", "全局字节序")
try:
    sp.define_endian("big")
    check(False, "endian 只能定义一次")
except ValueError:
    check(True, "endian 只能定义一次")

sp.define_alignment(2)
eq(sp.alignment, 2, "alignment")
sp.define_space("ram", {"type": "ram_space", "size": 4, "default": True})
check("ram" in sp.spaces, "space 登记")
sp.define_register(0x20, 1, ["A", "X"])
eq(sp.registers["A"], (0x20, 1), "第一个寄存器偏移")
eq(sp.registers["X"], (0x21, 1), "第二个顺延 size 字节")
sp.define_bitrange("zf", "statusreg", 10, 1)
eq(sp.bitranges["zf"], ("statusreg", 10, 1), "bitrange")


# ------------------------------------------------ token 与字段

t8 = SL.Token("opbyte", 8)
t8.add_field("op", 0, 7)
t8.add_field("aaa", 5, 7)
t8.add_field("cc", 0, 1)
eq(t8.fields["op"], (0, 7), "op 区间")
eq(t8.fields["aaa"], (5, 7), "aaa 区间")
check(t8.fields["op"] != t8.fields["aaa"], "字段可与其它字段重叠")
try:
    t8.add_field("bad", 8, 0)
    check(False, "区间必须 (lo,hi)")
except ValueError:
    check(True, "区间必须 (lo,hi)")
try:
    t8.add_field("over", 0, 8)
    check(False, "字段不能超出 token 位宽")
except ValueError:
    check(True, "字段不能超出 token 位宽")

try:
    SL.Token("odd", 12)
    sp.define_token(SL.Token("odd", 12))
    check(False, "token 位宽必须是 8 的倍数")
except ValueError:
    check(True, "token 位宽必须是 8 的倍数")

# 单字节 token：字节序不影响取值
eq(t8.value(b"\x90", "big"), 0x90, "单字节大端")
eq(t8.value(b"\x90", "little"), 0x90, "单字节小端")
eq(t8.field_value("op", 0x90), 0x90, "op 取全部 8 位")
eq(t8.field_value("aaa", 0x90), 0x90 >> 5, "aaa 取高 3 位")
eq(t8.field_value("cc", 0x90), 0, "cc 取低 2 位")

# 多字节 token：位编号受字节序影响（最低位恒为 0）
t16 = SL.Token("word", 16)
t16.add_field("lo8", 0, 7)
t16.add_field("hi8", 8, 15)
eq(t16.value(b"\x12\x34", "big"), 0x1234, "大端拼整数")
eq(t16.field_value("hi8", 0x1234), 0x12, "大端下 hi8 是第一字节")
eq(t16.field_value("lo8", 0x1234), 0x34, "大端下 lo8 是第二字节")
eq(t16.value(b"\x12\x34", "little"), 0x3412, "小端拼整数")
eq(t16.field_value("lo8", 0x3412), 0x12, "小端下 lo8 是第一字节")
eq(t16.field_value("hi8", 0x3412), 0x34, "小端下 hi8 是第二字节")

# per-token 的 endian 覆盖
ovr = SL.Token("word", 16, endian="little")
eq(ovr.value(b"\x12\x34", "big"), 0x3412, "token 覆盖了全局大端")


# signed 属性与显示

d8 = SL.Token("data8", 8)
d8.add_field("rel", 0, 7, attrs=("signed",))
eq(d8.field_value("rel", 0xFE), -2, "signed 补码解释")
eq(d8.display("rel", d8.value(b"\xFE", "little")), "-0x2", "signed 的显示")
u8 = SL.Token("u", 8)
u8.add_field("imm", 0, 7)
eq(u8.display("imm", 0x2A), "0x2a", "默认十六进制显示")


# ------------------------------------------------ attach variables

sp2 = SL.Spec()
sp2.define_endian("little")
tk = SL.Token("instr", 8)
tk.add_field("opcode", 0, 5)
tk.add_field("r1", 6, 7)
sp2.define_token(tk)
sp2.attach_variables(["r1"], ["reg0", "reg1", "reg2", "reg3"])
eq(sp2.attach["r1"][0], "reg0", "索引 0 对应第一个寄存器")
eq(len(sp2.attach["r1"]), 4, "寄存器表长度")
# 两侧不要求等长：一个字段对应整张表
sp2.attach_variables(["opcode"], ["reg0", "reg1", "reg2", "reg3"])
eq(sp2.attach["opcode"], sp2.attach["r1"], "两个字段共用同一张查表")
try:
    sp2.attach_variables(["nosuchfield"], ["reg0"])
    check(False, "attach 未声明的字段应报错")
except ValueError:
    check(True, "attach 未声明的字段应报错")

# attach 之后字段既改显示也改语义
eq(SL.resolve_operand("r1", tk.field_value("r1", 0x00), sp2, tk), "reg0", "值 0 -> reg0")
eq(SL.resolve_operand("r1", tk.field_value("r1", 0x40), sp2, tk), "reg1", "值 1 -> reg1")
eq(SL.resolve_operand("r1", tk.field_value("r1", 0x80), sp2, tk), "reg2", "值 2 -> reg2")


# ------------------------------------------------ 位模式

p1 = SL.parse_pattern("opcode=7 & r1 & r2", sp2)
eq(p1[0], "and", "顶层是 and")
opnds = []
eq(SL.eval_pattern(p1, {"opcode": 7, "r1": 1, "r2": 2}, sp2, opnds), True, "and 命中")
eq(opnds, ["r1", "r2"], "单独出现的标识符被收为操作数")

opnds = []
eq(SL.eval_pattern(p1, {"opcode": 8, "r1": 1, "r2": 2}, sp2, opnds), False, "约束不符")
p2 = SL.parse_pattern("(opcode=0 & mode=0) | (opcode=15)", sp2)
eq(p2[0], "or", "顶层是 or")
eq(SL.eval_pattern(p2, {"opcode": 0, "mode": 0}, sp2, []), True, "or 左支")
eq(SL.eval_pattern(p2, {"opcode": 15}, sp2, []), True, "or 右支")
eq(SL.eval_pattern(p2, {"opcode": 0, "mode": 1}, sp2, []), False, "or 两支都不符")

# 约束用的是原始整数编码：即使 attach 给了寄存器含义，这里仍然比整数
pc = SL.parse_pattern("r1=1", sp2)
eq(SL.eval_pattern(pc, {"r1": 1}, sp2, []), True, "约束按整数比")
eq(SL.eval_pattern(pc, {"r1": 2}, sp2, []), False, "整数不符即不命中")

try:
    SL.eval_pattern(SL.parse_pattern("nosuch=1", sp2), {"r1": 1}, sp2, [])
    check(False, "未知字段应报错")
except KeyError:
    check(True, "未知字段应报错")


# ------------------------------------------------ 完整解码

spec = SL.Spec()
spec.define_endian("little")
tok = SL.Token("opbyte", 8)
tok.add_field("op", 0, 7)
tok.add_field("op6", 2, 7)
tok.add_field("r1", 0, 1)
spec.define_token(tok)
spec.attach_variables(["r1"], ["R0", "R1", "R2", "R3"])

# 根构造：空标识符
spec.add(SL.Constructor("", "halt", [], SL.parse_pattern("op=0x00", spec)))
spec.add(SL.Constructor("", "nop", [], SL.parse_pattern("op=0xEA", spec)))
spec.add(SL.Constructor("", "inc", ["r1"], SL.parse_pattern("op6=0x3E & r1", spec)))
spec.add(SL.Constructor("", "lda", ["r1"], SL.parse_pattern("op6=0x3D & r1", spec)))
eq(len(spec.root()), 4, "根表 4 个构造")
eq(spec.table("nosuch"), [], "不存在的表为空")

i = SL.decode(spec, b"\x00", 0)
eq(i.mnemonic, "halt", "解码 halt")
eq(i.length, 1, "halt 长度 1")
i = SL.decode(spec, b"\xEA", 0)
eq(i.mnemonic, "nop", "解码 nop")

# op6 = (op >> 2) & 0x3F；inc 的 op6=0x3E ⇒ op = 0x3E<<2 | r1
for r, name in ((0, "R0"), (1, "R1"), (2, "R2"), (3, "R3")):
    byte = (0x3E << 2) | r
    ins = SL.decode(spec, bytes([byte]), 0)
    eq(ins.mnemonic, "inc", "inc(r=%d)" % r)
    eq(ins.operands, [name], "attach 把 %d 翻译成 %s" % (r, name))

# 顺序：先定义的先匹配 —— op6=0x3E 与 op=0xEA 不冲突，但换个值就不然
spec2 = SL.Spec()
spec2.define_endian("little")
t2 = SL.Token("opbyte", 8)
t2.add_field("op", 0, 7)
spec2.define_token(t2)
spec2.add(SL.Constructor("", "first", [], SL.parse_pattern("op=0x11", spec2)))
spec2.add(SL.Constructor("", "second", [], SL.parse_pattern("op=0x11", spec2)))
eq(SL.decode(spec2, b"\x11", 0).mnemonic, "first", "同模式时先定义者优先")

# 无法匹配
eq(SL.decode(spec, b"\xFF", 0), None, "无匹配返回 None")

# 变长：第二个 token（6502 的 `...` 与 `;` 形态）
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
eq(ins.mnemonic, "lda", "变长指令的 opcode 匹配")
eq(ins.length, 2, "`...` 之后又吃掉一个 token")
eq(ins.operands, ["0x99"], "操作数取自第二个 token")
eq(SL.decode(v, b"\xA9", 0), None, "字节不够时不匹配")


# ------------------------------------------------ 负控

try:
    SL.parse_pattern("op=1 &", spec)
    check(False, "残缺模式应报错")
except ValueError:
    check(True, "残缺模式应报错")

empty = SL.Spec()
try:
    SL.decode(empty, b"\x00", 0)
    check(False, "没有 token 应报错")
except ValueError:
    check(True, "没有 token 应报错")

print("断言通过: %d" % PASS)
if FAIL:
    print("失败 %d 条:" % len(FAIL))
    for m in FAIL:
        print("  -", m)
    sys.exit(1)
print("ALL GREEN")
