#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mangling.py 自检：逐条对着 Itanium C++ ABI §5.1 的文法与示例断言。

判据来源（本轮实测下载并提取正文）：
  Itanium C++ ABI https://itanium-cxx-abi.github.io/cxx-abi/abi.html
    * §5.1.3 <operator-name> 码表
    * §5.1.5 Compression：substitution 编号规则 + "_ZN1N1TIiiE2mfES0_IddE"
      -> Ret? N::T<int, int>::mf(N::T<double, double>) 的 S_/S0_/S1_/S2_ 清单
    * §5.1.5.2 <builtin-type> 表与 St/Sa/Sb/Ss/Si/So/Sd 缩写表

运行：python mangling_check.py
"""
import sys

from mangling import Demangler, demangle, BUILTIN, OPERATORS, SUBST_ABBREV

FAILS = []


def check(cond, label):
    if cond:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s" % label)
        FAILS.append(label)


def eq(got, want, label):
    check(got == want, "%s  (got=%r want=%r)" % (label, got, want))


def raises(fn, label):
    """真断言：fn 必须真的抛异常。不用 `or True` 之类的伪断言。"""
    try:
        fn()
    except Exception:
        check(True, "%s 应抛异常（确实抛了）" % label)
        return
    check(False, "%s 应抛异常（但没有）" % label)


def subs_of(mangled):
    """解完整个符号（含参数列表）后的 substitution 表。"""
    d = Demangler(mangled)
    d.take(2)
    d.name()
    while d.i < len(d.s):
        d.type()
    return d.subs


print("[1] 内建类型与指针/引用/右值引用")
eq(demangle("_Z3fooi"), "foo(int)", "_Z3fooi")
eq(demangle("_Z1fv"), "f()", "v = 无参数，不是 void")
eq(demangle("_Z3foocc"), "foo(char, char)", "两个 char 参数")
eq(demangle("_Z3addPi"), "add(int*)", "P = 指针")
eq(demangle("_Z1fRK1A"), "f(const A&)", "R+K = const 左值引用")
eq(demangle("_Z1fOi"), "f(int&&)", "O = 右值引用")
eq(demangle("_Z3fooiPd"), "foo(int, double*)", "多参数按序还原")

print("[2] CV 限定符顺序：[r][V][K] 写，const volatile 读")
eq(demangle("_Z1fVKi"), "f(const volatile int)", "VKi -> const volatile int")
eq(demangle("_Z1fKi"), "f(const int)", "Ki -> const int")
eq(demangle("_Z1fVi"), "f(volatile int)", "Vi -> volatile int")
check(BUILTIN["v"] == "void" and BUILTIN["z"] == "...", "builtin 表含 void 与 ...")

print("[3] 嵌套名与操作符")
eq(demangle("_ZN3Foo3barEi"), "Foo::bar(int)", "N..E 多层前缀")
eq(demangle("_ZN1AplERK1A"), "A::operator+(const A&)", "pl = operator+")
eq(demangle("_ZN1AneERK1A"), "A::operator!=(const A&)", "ne = operator!=")
raises(lambda: demangle("_ZN1AcvEv"), "'cv' 不是合法操作符码（co 才是 ~）")
eq(OPERATORS.get("co"), "~", "co = operator~")
eq(OPERATORS.get("cl"), "()", "cl = operator()")

print("[4] 模板实参")
eq(demangle("_ZNSaIiE4sizeEv"), "std::allocator<int>::size()", "Sa 缩写 + 模板实参")
eq(demangle("_ZNSt6vectorIiSaIiEE9push_backERKi"),
   "std::vector<int, std::allocator<int> >::push_back(const int&)",
   "嵌套模板：实参以 > 结尾时补空格")
check(not demangle("_ZNSaIiE4sizeEv").endswith("int >"),
      "单层模板实参不带多余空格")

print("[5] St 展开：<unscoped-name> ::= St <unqualified-name>")
eq(demangle("_ZSt9terminatev"), "std::terminate()", "St = ::std::")
eq(demangle("_ZNSt3_In4wardE"), "std::_In::ward", "数据符号不带括号")
eq(SUBST_ABBREV["St"], "std", "St 缩写值为 std")

print("[6] 成员函数 CV / ref-qualifier")
eq(demangle("_ZNK3Foo3barEv"), "Foo::bar() const", "NK..E = const 成员函数")
eq(demangle("_ZNKR3Foo3barEv"), "Foo::bar() const &", "R = & ref-qualifier")
eq(demangle("_ZNKO3Foo3barEv"), "Foo::bar() const &&", "O = && ref-qualifier")

print("[7] substitution 表（文档示例 _ZN1N1TIiiE2mfES0_IddE）")
eq(demangle("_ZN1N1TIiiE2mfES0_IddE"),
   "N::T<int, int>::mf(N::T<double, double>)", "文档示例原样还原")
eq(subs_of("_ZN1N1TIiiE2mfES0_IddE"),
   ["N", "N::T", "N::T<int, int>", "N::T<double, double>"],
   "文档 S_/S0_/S1_/S2_ 四条目（末位 unqualified-name ::mf 不是候选）")
eq(subs_of("_Z3fooi"), [], "builtin 参数不产生 substitution 条目")
eq(subs_of("_Z1fRK1A"), ["A", "const A", "const A&"],
   "组件先于包含它的结构入表")

print("[8] 负向：长度前缀优先于两位操作符码")
eq(demangle("_Z2nei"), "ne(int)", "'2ne' 是长度 2 的标识符 ne，不是 operator!=")
eq(demangle("_Z1fvi"), "f(void, int)", "形参位置的 v 是内建类型 void")
raises(lambda: demangle("_ZN1AzzEi"), "未知操作符码 zz")
raises(lambda: demangle("_Z1fS5_"), "substitution 下标越界")
raises(lambda: demangle("_ZNK3Foo3bar"), "nested-name 缺少收尾 E")
eq(demangle("printf"), "printf", "非 _Z 前缀（extern \"C\"）原样返回")

print("[9] 表规模与集合判等")
eq(len(BUILTIN), 21, "builtin 类型 21 种（v..z）")
eq(len(OPERATORS), 42, "操作符码条目数 42")
eq(len(OPERATORS) - len(set(OPERATORS.values())), 4,
   "4 个符号在值上重复（一元/二元同形：+ - & *）")
eq(sorted(SUBST_ABBREV.keys()),
   ["Sa", "Sb", "Sd", "Si", "So", "Ss", "St"], "7 个 Sx 缩写")

print()
if FAILS:
    print("FAILED %d" % len(FAILS))
    for f in FAILS:
        print("  - %s" % f)
    sys.exit(1)
print("ALL PASS")
