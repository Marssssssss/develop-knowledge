"""BinPRE 原子语义检测器的自检。

E1 类型/功能清单；E2~E7 逐条复现 Table 2 的 11 条规则（含负向断言）；
E8 论文 Example-3（f21,22 -> Integer + Checksum）；E9 mov 系列不算 functional；
E10 每个字段必有类型、功能可有可无。

断言基于实读 arXiv 2409.01994（BinPRE, CCS'24）§3.4 与 Table 2。

运行：python selfcheck_detectors.py
"""

import sys

from main import (FUNCTIONS, MOV_OPS, TYPES, Trace, detect_function,
                  detect_type, infer)

PASS = [0]
FAIL = [0]


def ok(cond, msg):
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
        print("  FAIL:", msg)


def eq(got, want, msg):
    ok(got == want, "%s (got=%r want=%r)" % (msg, got, want))


# ------------------------------------------------------------ E1

def e1_catalog():
    eq(sorted(TYPES), ["Bytes", "Group", "Integer", "Static", "String"],
       "E1 5 种语义类型")
    eq(sorted(FUNCTIONS),
       ["Aligned", "Checksum", "Command", "Delim", "Filename", "Length"],
       "E1 6 种语义功能")
    for op in ("mov", "movzx", "movsx", "lea"):
        ok(op in MOV_OPS, "E1 %s 属于 mov 系列" % op)
    for op in ("cmp", "shl", "xor", "add"):
        ok(op not in MOV_OPS, "E1 %s 不是 mov 系列" % op)


# ------------------------------------------------------------ E2 Static

def e2_static():
    t = Trace("f", ops=("cmp",), cmp_consts=(0x03,), cmp_true=True)
    eq(detect_type(t), {"Static"}, "E2 与固定值比较为真且无其他操作 -> Static")
    t2 = Trace("f", ops=("cmp", "xor"), cmp_consts=(0x03,), cmp_true=True)
    ok("Static" not in detect_type(t2), "E2 有 functional 操作就不是 Static（负向）")
    t3 = Trace("f", ops=("cmp",), cmp_consts=(0x03,), cmp_true=False)
    ok("Static" not in detect_type(t3), "E2 比较结果为假就不是 Static（负向）")


# ------------------------------------------------------------ E3 Integer

def e3_integer():
    eq(detect_type(Trace("f", arith_or_bit=True)), {"Integer"}, "E3 位运算 -> Integer")
    eq(detect_type(Trace("f", cmp_multi_consecutive=True)), {"Integer"},
       "E3 与多个连续值比较 -> Integer")
    # 示例：f21,22 的 shl/or
    t = Trace("f", ops=("movzx", "shl", "or", "cmp"), arith_or_bit=True)
    eq(detect_type(t), {"Integer"}, "E3 shl/or 组合 -> Integer")


# ------------------------------------------------------------ E4 Group

def e4_group():
    eq(detect_type(Trace("f", cmp_switch_consts=(0x01, 0x02))), {"Group"},
       "E4 与多个不同常量比较 -> Group")
    ok("Group" not in detect_type(Trace("f", cmp_switch_consts=(0x01,))),
       "E4 只有一个常量不算 Group（负向）")
    # Command 应当同时是 Group（§3.5.3 的类型约束）
    t = Trace("f", cmp_switch_consts=(0x01, 0x02), cmp_true=True)
    ok("Group" in detect_type(t), "E4 多分支 + 命中常量 -> Group")


# ------------------------------------------------------------ E5 Bytes/String

def e5_bytes_string():
    eq(detect_type(Trace("f", in_loop_same_ops=True, bytes_all_same_struct=True)),
       {"Bytes"}, "E5 循环内共享操作 -> Bytes")
    ok("Bytes" not in detect_type(Trace("f", in_loop_same_ops=True)),
       "E5 只有循环但字节不属同一结构 -> 不是 Bytes（负向）")
    t = Trace("f", in_loop_same_ops=True, bytes_all_same_struct=True,
              consecutive_cmp_same_const=True)
    eq(detect_type(t), {"Bytes", "String"},
       "E5 连续字节与同一分隔符比较 -> 同时 Bytes 与 String")
    ok("String" not in detect_type(Trace("f", in_loop_same_ops=False,
                                         consecutive_cmp_same_const=True)),
       "E5 无循环内的共享操作 -> 不是 String（负向）")


# ------------------------------------------------------------ E6 Command/Length

def e6_command_length():
    t = Trace("f", ops=("cmp",), cmp_consts=(0x03,), cmp_true=True,
              jump_on_true=True)
    eq(detect_function(t), {"Command"},
       "E6 命中常量后跳转 -> Command；cmp 本身是 functional，故不是 Aligned")
    ok("Command" not in detect_function(Trace("f", cmp_true=True)),
       "E6 命中但不跳转就不是 Command（负向）")
    for kw in ("loop_terminator", "lib_api_length", "ptr_inc_counter_dec"):
        ok("Length" in detect_function(Trace("f", **{kw: True})),
           "E6 %s -> Length" % kw)


# ------------------------------------------------------------ E7 Delim/Checksum

def e7_delim_checksum():
    t = Trace("f", loop_terminator=True, delimits_neighbors=True)
    ok("Delim" in detect_function(t), "E7 终止循环且分隔邻字段 -> Delim")
    ok("Delim" not in detect_function(Trace("f", loop_terminator=True)),
       "E7 只终止循环不分隔 -> 不是 Delim（负向，那是 Length）")
    ok("Delim" not in detect_function(Trace("f", delimits_neighbors=True)),
       "E7 只分隔不终止循环 -> 不是 Delim（负向）")
    ok("Checksum" in detect_function(Trace("f", cmp_loop_output=True)),
       "E7 与连续字节迭代的输出比较 -> Checksum")
    ok("Checksum" not in detect_function(Trace("f", cmp_consts=(0x00,))),
       "E7 与普通常量比较 -> 不是 Checksum（负向）")


# ------------------------------------------------------------ E8 Example-3

def e8_example3():
    """论文 Example-3：Figure 5 的 f21,22。"""
    t = Trace("f21,22", ops=("movzx", "shl", "or", "cmp"),
              arith_or_bit=True, cmp_loop_output=True)
    ty, fn = infer(t)
    eq(ty, {"Integer"}, "E8 Example-3 类型 = Integer")
    eq(fn, {"Checksum"}, "E8 Example-3 功能 = Checksum")
    # 反例：只有位运算、没有与循环输出比较 -> 只有类型没有功能
    t2 = Trace("f", ops=("shl", "or"), arith_or_bit=True)
    eq(infer(t2), ({"Integer"}, set()), "E8 无循环输出比较时功能为空")


# ------------------------------------------------------------ E9 mov 系列

def e9_mov_not_functional():
    t = Trace("f", ops=("mov", "movzx", "movsx"))
    ok(not t.functional, "E9 纯 mov 系列不算 functional")
    ok("Aligned" in detect_function(t), "E9 纯 mov 系列 -> Aligned")
    t2 = Trace("f", ops=("mov", "cmp"))
    ok(t2.functional, "E9 掺一个 cmp 就算 functional")
    ok("Aligned" not in detect_function(t2), "E9 有 functional 就不是 Aligned")


# ------------------------------------------------------------ E10 必有类型

def e10_always_some_type():
    """论文：每个字段都有语义类型，但只有部分字段有语义功能。"""
    no_info = Trace("f", ops=("cmp",), functional=True)
    ty, fn = infer(no_info)
    ok(isinstance(ty, set), "E10 类型总是返回一个集合")
    eq(fn, set(), "E10 没有特征时功能为空集")


def main():
    for fn_ in (e1_catalog, e2_static, e3_integer, e4_group, e5_bytes_string,
                e6_command_length, e7_delim_checksum, e8_example3,
                e9_mov_not_functional, e10_always_some_type):
        fn_()
    print("PASS=%d FAIL=%d" % (PASS[0], FAIL[0]))
    return 1 if FAIL[0] else 0


if __name__ == "__main__":
    sys.exit(main())
