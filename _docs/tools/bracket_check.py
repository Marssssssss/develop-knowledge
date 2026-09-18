"""括号配平检查(语言感知版)。

用法:python _docs/tools/bracket_check.py <file> [file2 ...]
      (不入参通配符由 shell 展开即可;也可一次传多个文件)

为什么不是简单的「数括号」:
  括号会出现在注释、字符串、文档串里(如 Python 注释里的 `# 采样格 ∈ [-half, half)`,
  或文档串里的 `[(1, 2)]`),必须先按语言把「注释与字符串字面量」置空再统计,
  否则会报假阳性。

历史坑(2026-09-18 修复):
  旧版只处理 `//` 行注释 + 正则贪心匹配双引号字符串,对 Python 的 `#` 注释与三引号文档串
  完全无效;更糟的是贪心引号匹配会把「上一段文档串的收尾引号」与「下一段文档串的开头引号」
  配成一对,把两者之间的真实代码整段吃掉 —— 于是既可能报假 UNCLOSED,也可能因右方括号失配
  直接抛 AssertionError。改为逐字符状态机置空后,两个方向的误报都消失。

置空而非删除:被置空的字符写成空格(换行保留),这样报出的 offset 仍能对回原文件。
"""
import os
import sys

PAIRS = {"(": ")", "[": "]", "{": "}"}
CLOSERS = (")", "]", "}")


def mask_comments_and_literals(src, ext):
    """把注释与字符串字面量替换为空格,保持偏移与行号不变。"""
    out = list(src)
    n = len(src)

    def blank(a, b):
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    i = 0
    while i < n:
        c = src[i]
        if ext == ".py":
            if c == "#":                                  # 行注释
                j = src.find("\n", i)
                j = n if j < 0 else j
            elif src.startswith('"""', i) or src.startswith("'''", i):   # 三引号(含文档串)
                q = src[i:i + 3]
                j = src.find(q, i + 3)
                j = n if j < 0 else j + 3
            elif c in "\"'":                              # 普通字符串/字符字面量
                j = i + 1
                while j < n and src[j] != c:
                    j += 2 if src[j] == "\\" else 1
                j = min(j + 1, n)
            else:
                i += 1
                continue
        else:                                             # C / Go / Java / JS 等
            if c == "/" and src.startswith("//", i):       # 行注释
                j = src.find("\n", i)
                j = n if j < 0 else j
            elif c == "/" and src.startswith("/*", i):     # 块注释(可跨行)
                j = src.find("*/", i + 2)
                j = n if j < 0 else j + 2
            elif c == "`":                                 # Go 原始字符串
                j = src.find("`", i + 1)
                j = n if j < 0 else j + 1
            elif c == '"':                                 # 普通字符串字面量
                j = i + 1
                while j < n and src[j] != '"':
                    j += 2 if src[j] == "\\" else 1
                j = min(j + 1, n)
            else:
                i += 1
                continue
        blank(i, j)
        i = j
    return "".join(out)


def check(path):
    """返回 (是否通过, 输出行列表)。"""
    src = open(path, encoding="utf-8").read()
    code = mask_comments_and_literals(src, os.path.splitext(path)[1].lower())
    stack, mismatch = [], []
    for i, ch in enumerate(code):
        if ch in PAIRS:
            stack.append((ch, i))
        elif ch in CLOSERS:
            if not stack or PAIRS[stack[-1][0]] != ch:
                mismatch.append((ch, i))
                stack = []
                continue
            stack.pop()
    lines = []
    if not stack and not mismatch:
        lines.append("brackets: BALANCED")
    elif mismatch:
        ch, i = mismatch[0]
        lines.append("brackets: MISMATCH %r at offset %d" % (ch, i))
        lines.append("  ctx: %r" % (code[max(0, i - 70):i + 30],))
    else:
        lines.append("brackets: UNCLOSED %r" % (stack[:5],))
        ch, i = stack[0]
        lines.append("  ctx: %r" % (code[max(0, i - 70):i + 30],))
    lines.append("lines: %d" % len(src.splitlines()))
    return (not stack and not mismatch), lines


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    bad = 0
    for path in sys.argv[1:]:
        try:
            ok, lines = check(path)
        except OSError as exc:                      # 目录或通配符未展开
            print("%s\n  ERROR: %s" % (path, exc))
            bad += 1
            continue
        if len(sys.argv) > 2:
            print("== %s" % path)
        for l in lines:
            print(l)
        if not ok:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
