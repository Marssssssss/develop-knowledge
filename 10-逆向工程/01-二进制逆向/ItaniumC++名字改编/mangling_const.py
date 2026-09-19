#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Itanium C++ ABI mangling 的常量表（从 mangling.py 拆出，逐字节搬运）。

来源：https://itanium-cxx-abi.github.io/cxx-abi/abi.html
  * <builtin-type>（§5.1.5.2）
  * <operator-name>（§5.1.3）
  * <substitution> 缩写 Sx（§5.1.5）
"""

# ---------------------------------------------------------- 内建类型（§5.1.5.2）

BUILTIN = {
    "v": "void", "w": "wchar_t", "b": "bool", "c": "char",
    "a": "signed char", "h": "unsigned char", "s": "short",
    "t": "unsigned short", "i": "int", "j": "unsigned int",
    "l": "long", "m": "unsigned long", "x": "long long",
    "y": "unsigned long long", "n": "__int128", "o": "unsigned __int128",
    "f": "float", "d": "double", "e": "long double", "g": "__float128",
    "z": "...",
}

# ------------------------------------------------------------ 操作符（§5.1.3）

OPERATORS = {
    "dl": "delete", "da": "delete[]", "ps": "+", "ng": "-",
    "ad": "&", "de": "*", "co": "~", "pl": "+", "mi": "-",
    "ml": "*", "dv": "/", "rm": "%", "an": "&", "or": "|",
    "eo": "^", "aS": "=", "pL": "+=", "mI": "-=", "mL": "*=",
    "dV": "/=", "rM": "%=", "aN": "&=", "oR": "|=", "eO": "^=",
    "ls": "<<", "lS": "<<=", "eq": "==", "ne": "!=", "lt": "<",
    "le": "<=", "ss": "<=>", "nt": "!", "aa": "&&", "oo": "||",
    "pp": "++", "mm": "--", "cm": ",", "pm": "->*", "pt": "->",
    "cl": "()", "ix": "[]", "qu": "?",
}

# ------------------------------------------------ 常用替换缩写（§5.1.5 Compression）

SUBST_ABBREV = {
    "St": "std",
    "Sa": "std::allocator",
    "Sb": "std::basic_string",
    "Ss": "std::basic_string<char, std::char_traits<char>, "
          "std::allocator<char> >",
    "Si": "std::basic_istream<char, std::char_traits<char> >",
    "So": "std::basic_ostream<char, std::char_traits<char> >",
    "Sd": "std::basic_iostream<char, std::char_traits<char> >",
}

# 打印顺序：const 最靠近基类型，其次 volatile、restrict（与 [r][V][K] 相反）
CV_ORDER = (("K", "const"), ("V", "volatile"), ("r", "restrict"))
