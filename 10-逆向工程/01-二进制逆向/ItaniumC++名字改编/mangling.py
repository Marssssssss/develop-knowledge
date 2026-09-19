#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Itanium C++ ABI 名字改编（mangling）最小解 mangler。

数据来源（本轮实测下载并提取正文）：
  Itanium C++ ABI（§5.1 External Names）
    https://itanium-cxx-abi.github.io/cxx-abi/abi.html
  本 demo 逐条对着该文档实现，用到的文法行（对应文档中的行号见 README）：
    <unscoped-name> ::= <unqualified-name> | St <unqualified-name>  # ::std::
    <nested-name>   ::= N [<CV-qualifiers>] [<ref-qualifier>]
                        <prefix> <unqualified-name> E
    <CV-qualifiers> ::= [r] [V] [K]   # restrict, volatile, const
    <ref-qualifier> ::= R | O         # & / &&
    <builtin-type>  ::= v/w/b/c/a/h/s/t/i/j/l/m/x/y/n/o/f/d/e/g/z
    <type> 前缀     ::= P 指针 / R 左值引用 / O 右值引用
    <substitution>  ::= S_ | S<seq-id>_ , 缩写 St/Sa/Sb/Ss/Si/So/Sd

刻意不含：lambda、表达式模板实参、<special-name> 的虚函数表族（TV/TT/TI/TS）、
<local-name>/<unnamed-type-name>/<ctor-dtor-name> 等需要编译器上下文才能还原的部分。
"""

from mangling_const import (  # noqa: F401
    BUILTIN, OPERATORS, SUBST_ABBREV, CV_ORDER,
)




class Demangler(object):
    """一次 demangle 的上下文：位置指针 + substitution 表。

    substitution 表是**有状态的**：它按解析顺序累积"可替换的符号构造"，
    文档给的判据是"组件先于包含它的结构入表"（见 _ZN1N1TIiiE2mfES0_IddE 例）。
    """

    def __init__(self, text):
        self.s = text
        self.i = 0
        self.subs = []
        self.cv = ""        # nested-name 上的成员函数 CV 限定
        self.ref = ""       # nested-name 上的 ref-qualifier

    # --- 基础取字符 ----------------------------------------------------

    def peek(self, n=1):
        return self.s[self.i:self.i + n]

    def take(self, n=1):
        v = self.s[self.i:self.i + n]
        self.i += n
        return v

    def expect(self, ch):
        if self.peek() != ch:
            raise ValueError("期望 %r，实为 %r（位置 %d）"
                             % (ch, self.peek(), self.i))
        self.i += 1

    def digits(self):
        start = self.i
        while self.i < len(self.s) and self.s[self.i].isdigit():
            self.i += 1
        return self.s[start:self.i]

    def source_name(self):
        """<source-name> ::= <positive length number> <identifier>"""
        n = int(self.digits())
        return self.take(n)

    def add_sub(self, text):
        """登记一个可替换组件。builtin 与 substitution 自身不登记。"""
        if text and text not in self.subs:
            self.subs.append(text)
        return text

    def sub_id(self):
        """解析 S<seq-id>_，返回 substitution 表里的下标。

        S_ 是第 0 项，之后是 S0_ / S1_ / ... S9_ / SA_ ... （base36 + 1）。
        """
        if self.peek() == "_":
            self.take()
            return 0
        text = ""
        while self.peek() and self.peek() != "_":
            text += self.take()
        self.expect("_")
        return int(text or "0", 36) + 1

    def substitution(self):
        """'S' 之后的部分：缩写（St/Sa/Ss/...）或 seq-id。"""
        ch = self.peek()
        if ch in ("t", "a", "b", "s", "i", "o", "d"):
            self.take()
            return SUBST_ABBREV["S" + ch]
        idx = self.sub_id()
        if idx >= len(self.subs):
            raise ValueError("substitution 下标 %d 越界（表长 %d）"
                             % (idx, len(self.subs)))
        return self.subs[idx]

    # --- 限定符 --------------------------------------------------------

    @staticmethod
    def cv_text(quals):
        """把解析到的 [r][V][K] 集合按 C++ 习惯顺序拼成字符串。"""
        out = [word for code, word in CV_ORDER if code in quals]
        return " ".join(out)

    def cv_qualifiers(self):
        """<CV-qualifiers> ::= [r] [V] [K]，返回如 'const volatile'。"""
        got = ""
        for q in ("r", "V", "K"):
            if self.peek() == q:
                self.take()
                got += q
        return self.cv_text(got)

    def ref_qualifier(self):
        """<ref-qualifier> ::= R | O"""
        if self.peek() in ("R", "O"):
            return "&" if self.take() == "R" else "&&"
        return ""

    # --- 类型 ----------------------------------------------------------

    def type(self):
        """<type> —— 只实现本 demo 需要的几种形态。"""
        ch = self.peek()
        if ch == "P":
            self.take()
            return self.add_sub(self.type() + "*")
        if ch == "R":
            self.take()
            return self.add_sub(self.type() + "&")
        if ch == "O":
            self.take()
            return self.add_sub(self.type() + "&&")
        if ch in "rVK":
            quals = self.cv_qualifiers()
            base = self.type()
            return self.add_sub((quals + " " + base) if quals else base)
        if ch == "F":
            return self.function_type()
        if ch == "S":
            self.take()
            base = self.substitution()
            if self.peek() == "I":
                return self.add_sub(self.render_template(base, self.template_args()))
            return base
        if ch in BUILTIN:
            self.take()
            return BUILTIN[ch]       # builtin 不进 substitution 表
        if ch == "N":
            self.take()
            base = self.type()
            self.expect("E")
            return base
        # 其余按 class/enum：一个 source-name（可能带模板实参）
        return self.class_enum_type()

    def class_enum_type(self):
        name = self.source_name()
        full = self.add_sub(name)
        if self.peek() == "I":
            args = self.template_args()
            full = self.add_sub(self.render_template(full, args))
        return full

    def template_args(self):
        """<template-args> ::= I <template-arg>+ E"""
        self.expect("I")
        args = []
        while self.peek() != "E":
            args.append(self.type())
        self.expect("E")
        return ", ".join(args)

    def render_template(self, name, args):
        """拼 <name<args>>：实参以 '>' 结尾时补空格，避免连成 '>>'。

        注意空格由**外层**补，而不是 template_args 自己补 —— 否则
        SaIiE 会被还原成 'std::allocator<int >' 这种多余的空格。
        """
        return "%s<%s%s>" % (name, args, " " if args.endswith(">") else "")

    def function_type(self):
        """F ... E：返回类型 + 参数类型列表。"""
        self.expect("F")
        ret = self.type()
        params = []
        while self.peek() != "E":
            params.append(self.type())
        self.expect("E")
        return "%s(%s)" % (ret, ", ".join(params))

    # --- 名字 ----------------------------------------------------------

    def unqualified_name(self):
        """<unqualified-name> ::= <operator-name> | <source-name>（本节范围）

        长度前缀必须**先于**两位操作符码判定：否则 "2ne" 这种以 'n' 开头的
        两位标识符会被误当成 operator!=。
        """
        if self.peek().isdigit():
            return self.source_name()
        code = self.peek(2)
        if code in OPERATORS:
            self.take(2)
            return "operator" + OPERATORS[code]
        raise ValueError("不认识的 <unqualified-name>: %r（位置 %d）"
                         % (self.peek(), self.i))

    def unscoped_name(self):
        """<unscoped-name> ::= <unqualified-name> | St <unqualified-name>"""
        self.expect("S")
        if self.peek() == "t":
            self.take()
            self.add_sub("std")
            return self.add_sub("std::" + self.unqualified_name())
        base = self.substitution()
        if self.peek() == "I":
            return self.add_sub(self.render_template(base, self.template_args()))
        return base

    def name(self):
        ch = self.peek()
        if ch == "N":
            return self.nested_name()
        if ch == "S":
            return self.unscoped_name()
        return self.unqualified_name()

    def nested_name(self):
        """N [<CV-qualifiers>] [<ref-qualifier>] <prefix> <unqualified-name> E

        逐段推进 substitution 表：每拼出一层前缀就登记一次。
        """
        self.expect("N")
        self.cv = self.cv_qualifiers()
        self.ref = self.ref_qualifier()
        parts = []
        while self.peek() != "E":
            if self.peek() == "S":
                part = self.unscoped_name()
            elif self.peek() == "I":
                # 模板名先入表、模板 id 后入表（文档原话：template-id comes
                # before template —— 此处"先"指模板名 N::T 先于 N::T<...>）
                self.add_sub("::".join(parts))
                parts[-1] = self.render_template(parts[-1],
                                                 self.template_args())
                self.add_sub("::".join(parts))
                continue
            else:
                part = self.unqualified_name()
            parts.append(part)
            # 只有 <prefix> 是替换候选；最后那个 unqualified-name 不是
            if self.peek() != "E":
                self.add_sub("::".join(parts))
        self.expect("E")
        return "::".join(parts)


def demangle(mangled):
    """把一个 Itanium mangled name 还原成人类可读形式。"""
    if not mangled.startswith("_Z"):
        return mangled                      # extern "C" / 非 C++ 符号原样返回
    d = Demangler(mangled)
    d.take(2)
    base = d.name()
    if d.i >= len(d.s):
        return base                         # 数据符号：没有参数列表
    # 函数符号：<name> <bare-function-type>（参数列表，无返回类型）
    if d.peek() == "v" and d.i + 1 == len(d.s):
        d.take()
        params = ""               # 单独的 v 表示"无参数"；形参位置的 v 是 void
    else:
        got = []
        while d.i < len(d.s):
            got.append(d.type())
        params = ", ".join(got)
    tail = ""
    if d.cv:
        tail += " " + d.cv
    if d.ref:
        tail += " " + d.ref
    return "%s(%s)%s" % (base, params, tail)
