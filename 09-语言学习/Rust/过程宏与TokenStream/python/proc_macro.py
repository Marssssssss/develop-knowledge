"""过程宏与 `TokenStream` 模型（依据 Rust Reference `procedural-macros`）。

官方原文里最有意思的是这一段：声明宏与过程宏用的是**两套不同的 token 定义**，
并且在跨越边界时各自做转换。落地规则：

**传给过程宏时（declarative → proc_macro）**

- 多字符运算符拆成单字符（`+=` → `+` `=`）；
- 生命周期拆成 `'` + 标识符（`'a` → `'` `a`）；
- 元变量 `$crate` 作为一个**标识符**传入；
- 其它元变量替换展开成其底层 token stream，必要时用 `Delimiter::None` 的分组包起来
  以保住优先级；`tt` 与 `ident` **从不**被包。

**从过程宏输出时（proc_macro → declarative）**

- 标点字符在适用时粘回多字符运算符；
- `'` 与标识符粘回生命周期；
- 负数常量拆成 `-` 与常量两个 token（必要时用 `Delimiter::None` 分组包起来）。

此外：`///` 文档注释两种宏都不支持，一律先转成 `#[doc = r".."]`。
"""

from __future__ import annotations

import re

DELIMS = {"(": ")", "[": "]", "{": "}"}
MULTI_OPS = ["=>", "->", "::", ">>=", "<<=", ">>", "<<", "..=", "..",
             "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "&&", "||",
             "==", "!=", "<=", ">=", "<-"]
PUNCT_CHARS = set("+-*/%=<>!&|^~?:;,.$#@'")


class ProcMacroError(Exception):
    pass


def tokenize(src, dialect="decl"):
    """dialect="decl" 用声明宏的 token 定义；"proc" 用过程宏的定义。"""
    toks, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
            continue
        if c in DELIMS:
            depth, j = 0, i
            while j < n:
                if src[j] in DELIMS:
                    depth += 1
                elif src[j] in ")]}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            toks.append(("group", c, tokenize(src[i + 1:j], dialect)))
            i = j + 1
            continue
        if dialect == "decl" and c == "'" and i + 1 < n and (
                src[i + 1].isalpha() or src[i + 1] == "_"):
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            toks.append(("lifetime", src[i:j]))
            i = j
            continue
        if src[i:i + 2] == "//" or src[i:i + 2] == "/*":
            end = src.find("\n", i) if src[i:i + 2] == "//" else src.find("*/", i)
            i = n if end < 0 else end + (0 if src[i:i + 2] == "//" else 2)
            continue
        if c.isdigit() or (c == "-" and dialect == "proc" and i + 1 < n
                           and src[i + 1].isdigit()):
            j = i + 1
            while j < n and (src[j].isdigit() or src[j] in "._"):
                j += 1
            toks.append(("lit", src[i:j]))
            i = j
            continue
        if c == '"':
            j = src.index('"', i + 1) + 1
            toks.append(("lit", src[i:j]))
            i = j
            continue
        if c in PUNCT_CHARS:
            if dialect == "decl":
                op = next((o for o in MULTI_OPS if src.startswith(o, i)), None)
                if op:
                    toks.append(("punct", op))
                    i += len(op)
                    continue
            toks.append(("punct", c))
            i += 1
            continue
        j = i
        while j < n and not src[j].isspace() and src[j] not in DELIMS \
                and src[j] not in ")]}" and src[j] not in PUNCT_CHARS:
            j += 1
        word = src[i:j]
        toks.append(("crate_meta", word) if word.startswith("$crate")
                    else ("ident", word))
        i = j
    return toks


# ------------------------------------------------------------------ 双向转换
def to_proc_macro(toks):
    """声明宏 token → 过程宏 token。"""
    out = []
    for t in toks:
        if t[0] == "punct" and len(t[1]) > 1:
            out.extend(("punct", ch) for ch in t[1])       # 拆成单字符
        elif t[0] == "lifetime":
            out.append(("punct", "'"))
            out.append(("ident", t[1][1:]))                # 拆成 ' + ident
        elif t[0] == "crate_meta":
            out.append(("ident", "$crate"))                # 作为单个标识符
        elif t[0] == "meta":
            # tt / ident 从不包装；其余可能被 Delimiter::None 的分组包起来
            inner = t[2] if t[1] in ("tt", "ident") else [("group", None, t[2])]
            out.extend(inner)
        elif t[0] == "group":
            out.append(("group", t[1], to_proc_macro(t[2])))
        else:
            out.append(t)
    return out


def from_proc_macro(toks):
    """过程宏 token → 声明宏 token。"""
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t[0] == "lit" and t[1].startswith("-"):
            # 负数常量拆成 `-` 与常量两个 token
            out.append(("punct", "-"))
            out.append(("lit", t[1][1:]))
            i += 1
            continue
        if t[0] == "punct" and t[1] == "'" and i + 1 < len(toks) \
                and toks[i + 1][0] == "ident":
            out.append(("lifetime", "'" + toks[i + 1][1]))
            i += 2
            continue
        if t[0] == "punct" and i + 1 < len(toks) and toks[i + 1][0] == "punct":
            merged = t[1] + toks[i + 1][1]
            if merged in MULTI_OPS:
                out.append(("punct", merged))
                i += 2
                continue
        if t[0] == "group":
            out.append(("group", t[1], from_proc_macro(t[2])))
            i += 1
            continue
        out.append(t)
        i += 1
    return out


def render(toks):
    out = []
    for t in toks:
        if t[0] == "group":
            close = DELIMS.get(t[1], "")
            out.append(t[1] + render(t[2]) + close if t[1] else render(t[2]))
        else:
            out.append(t[1])
    return " ".join(out)


# ------------------------------------------------------------ 属性宏的输入切分
def split_attribute_macro(src, name="show_streams"):
    """`#[name(..)] item` → (attr 的 token 串, item 的 token 串)。

    官方原文：第一个 TokenStream 是属性名之后的定界 token 树，**不含外层定界符**；
    第二个是 item 的其余部分（含 item 上的其它属性）。
    """
    m = re.match(r"\s*#\[\s*" + re.escape(name) + r"\s*", src)
    if not m:
        raise ProcMacroError("不是 %s 属性宏调用" % name)
    i = m.end()
    attr_src = ""
    if i < len(src) and src[i] in DELIMS:
        close = DELIMS[src[i]]
        depth, j = 0, i
        while j < len(src):
            if src[j] in DELIMS:
                depth += 1
            elif src[j] in ")]}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        attr_src = src[i + 1:j]
        i = j + 1
    # 跳过可选的 `]`
    while i < len(src) and src[i] != "]":
        i += 1
    i += 1
    return tokenize(attr_src), tokenize(src[i:])


def doc_comment_to_attr(doc):
    """`/// Doc` 两种宏都不支持，一律转成 #[doc = r".."]。"""
    text = doc.lstrip("/").strip()
    return [("punct", "#"), ("group", "[", [("ident", "doc"), ("punct", "="),
                                            ("lit", 'r"%s"' % text)])]


# ------------------------------------------------------------------ 卫生性对比
def resolve_in_proc_macro_output(name, def_env, call_env):
    """过程宏**不卫生**：输出就像被就地写在调用处，一律先在调用处解析。"""
    if name in call_env:
        return ("call", call_env[name])
    if name in def_env:
        return ("def", def_env[name])
    return ("unresolved", None)


def resolve_in_decl_macro(name, kind, def_env, call_env):
    """声明宏是 mixed-site：局部变量/标签在定义处，其余在调用处。"""
    if kind in ("local", "label"):
        return ("def", def_env[name]) if name in def_env else ("unresolved", None)
    return ("call", call_env[name]) if name in call_env else ("unresolved", None)


# ------------------------------------------------------------------ 结构性限制
CRATE_TYPE = "proc-macro"

# 官方列出的两种报错途径：panic 或 emit 一个 compile_error! 调用
COMPILE_ERROR_WAY = "both"

FUNCTION_LIKE_POSITIONS = [
    "Statements", "Expressions", "Patterns", "Type expressions", "Items",
    "Inherent and trait implementations", "Trait definitions",
]

ATTRIBUTE_POSITIONS = [
    "Items", "Items in extern blocks", "Inherent and trait implementations",
    "Trait definitions",
]

DERIVE_INPUT_KINDS = ["struct", "enum", "union"]


# ------------------------------------------------------------------ 结构性限制
# 与声明宏成对对照：过程宏的限制严格得多，换来的是任意 Rust 代码的处理能力。
RESTRICTIONS = {
    "proc_macro": {
        "must_be_crate_root": True,
        "usable_in_defining_crate": False,
        "needs_proc_macro_crate_type": True,
    },
    "macro_rules": {
        "must_be_crate_root": False,
        "usable_in_defining_crate": True,
        "needs_proc_macro_crate_type": False,
    },
}

SECURITY = {
    "proc_macro": "same as build scripts (stdin/stdout/stderr, file access)",
    "build_script": "same as build scripts (stdin/stdout/stderr, file access)",
}


def run_proc_macro(behavior, tokens):
    """官方原文：过程宏要么返回语法、要么 panic、要么死循环。"""
    if behavior == "panic":
        raise ProcMacroError("panic 被编译器捕获 → 变成编译错误")
    if behavior == "loop":
        return "HANG"          # 死循环不会被捕获，编译器会被挂住
    return tokens              # 返回语法（替换或追加）
