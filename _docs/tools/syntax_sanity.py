#!/usr/bin/env python3
"""粗粒度语法健康检查（本机无 C/Go/Rust/Swift 工具链时的兜底）。

做四件事：
  1. .py —— 用 ast.parse 真编译，最可靠；
  2. .c/.h/.m/.go/.rs/.swift/.js/.ts —— 先剥掉注释、字符串与字符字面量，
     再检查 () [] {} 是否平衡，以及是否缺少 main；
  3. .go —— 额外检查未使用 import（Go 里这是编译错误）；
  4. 所有文件 —— 检查 CRLF 行尾混用（Windows 检出后最容易发生的污染）。

它**不能替代编译器**，只能挡住"漏大括号/引号未闭合/少 include"这类低级错误。

用法：
    python3 _docs/tools/syntax_sanity.py "**/*.c" "**/*.go" "**/*.py"
    python3 _docs/tools/syntax_sanity.py --fix-crlf "**/*.js"   # 就地转 LF

已知局限（曾造成误报，故显式记录）：
  * JavaScript 的**正则字面量**（`/.../`）未特殊处理，若正则里含括号会把平衡算歪；
  * Swift 的字符串插值 `\\(...)` 已按字符串整体剥除，插值内的括号不参与平衡；
  * Rust 的生命周期标注 `'a` 必须先于字符字面量剥除，否则 `'a>` 会被当成
    未闭合字符字面量，把后面的括号一起吃掉（曾误报 3 个 main.rs 不平衡）。
"""

from __future__ import annotations

import ast
import glob
import os
import re
import sys
import warnings
from pathlib import Path

RUST_RAW_HEAD = re.compile(r'r(#*)"')

SOURCE_SUFFIXES = (".c", ".h", ".m", ".mm", ".go", ".rs", ".swift", ".js", ".ts", ".tsx")


REGEX_PREV = set("(,=:[!&|?{};+-*%~^<>")
REGEX_KEYWORDS = ("return", "typeof", "instanceof", "case", "in", "of", "delete", "void", "do", "else", "yield", "await")


def _regex_allowed(prev: str, tail: str) -> bool:
    """判断当前 `/` 是正则字面量开头还是除号（标准 lexer 启发式）。"""
    if prev == "" or prev in REGEX_PREV:
        return True
    return any(tail.endswith(kw) for kw in REGEX_KEYWORDS)


def strip_js(src: str) -> str:
    """剥掉 JS/TS 的字符串、模板字面量与正则字面量，**保留** ${...} 插值内的代码。

    模板字面量可以跨行，且 `${}` 里可能出现真正的花括号（对象字面量），
    所以按字符扫描并维护插值嵌套深度，而不是简单正则替换。
    正则字面量（`/.../g`）里的 `\\(` 若不剥掉会凭空多出右括号 ——
    2026-09-15 之前就是这样把 compiler_check.js 误报成 `() +6` 的。
    """
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == "/" and _regex_allowed(next((ch for ch in reversed(out) if not ch.isspace()), ""), "".join(out[-8:])):
            i += 1
            in_class = False
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "[":
                    in_class = True
                elif src[i] == "]":
                    in_class = False
                elif src[i] == "/" and not in_class:
                    i += 1
                    break
                elif src[i] == "\n":
                    break
                i += 1
            out.append("/re/")
            continue
        if c in "\"'":
            i += 1
            while i < n and src[i] != c:
                i += 2 if src[i] == "\\" else 1
            i += 1
            out.append('""')
            continue
        if c == "`":
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == "`":
                    i += 1
                    break
                if src[i] == "$" and i + 1 < n and src[i + 1] == "{":
                    depth = 1
                    i += 2
                    start = i
                    while i < n and depth:
                        if src[i] == "{":
                            depth += 1
                        elif src[i] == "}":
                            depth -= 1
                        i += 1
                    out.append(strip_js(src[start:i - 1]))
                    continue
                i += 1
            out.append('""')
            continue
        out.append(c)
        i += 1
    return "".join(out)


def strip_noise(src: str, kind: str) -> str:
    """单遍扫描剥离注释 / 字符串 / 字符字面量。

    ⚠️ 必须是**单遍**扫描，不能按"先正则去注释、再正则去字符串"的顺序做：
    `printf("http://x")` 里的 `//` 在字符串内部，先跑行注释正则会把该行
    右半截（含右括号）整段删掉，于是平衡检查凭空报错。2026-09-15 之前
    就是这么误报的（es_demo.go / hcl_parser.c 等多例）。
    """
    if kind == "js":
        return strip_js(src)
    out: list[str] = []
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        # ---- 注释 ----
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        # ---- Go 裸字符串（可跨行、内部不转义）----
        if kind == "go" and c == "`":
            j = src.find("`", i + 1)
            i = n if j < 0 else j + 1
            out.append('""')
            continue
        # ---- Rust 裸字符串 r"..." / r#"..."# ----
        if kind == "rust" and c == "r":
            m = RUST_RAW_HEAD.match(src[i:])
            if m:
                closer = '"' + m.group(1)
                j = src.find(closer, i + m.end())
                i = n if j < 0 else j + len(closer)
                out.append('""')
                continue
        # ---- 字符串字面量 ----
        if c == '"':
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
            i += 1
            out.append('""')
            continue
        # ---- 字符字面量 / Rust 生命周期 ----
        if c == "'":
            if kind == "rust":
                m = re.match(r"'[A-Za-z_]\w*", src[i:])
                if m and not src.startswith(m.group(0) + "'", i):
                    # `'a` 后面不是引号 → 生命周期标注，整体丢弃
                    i += m.end()
                    continue
            i += 1
            while i < n and src[i] != "'":
                i += 2 if src[i] == "\\" else 1
            i += 1
            out.append("''")
            continue
        out.append(c)
        i += 1
    return "".join(out)


def kind_of(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".go":
        return "go"
    if suffix == ".rs":
        return "rust"
    if suffix in (".js", ".ts", ".tsx"):
        return "js"
    return "c"


def has_crlf(path: str) -> bool:
    with open(path, "rb") as fh:
        return b"\r\n" in fh.read()


def check_c_like(path: str) -> list[str]:
    src = open(path, encoding="utf-8", newline="").read()
    problems: list[str] = []
    kind = kind_of(path)

    body = strip_noise(src, kind)
    for open_c, close_c in (("{", "}"), ("(", ")"), ("[", "]")):
        d = body.count(open_c) - body.count(close_c)
        if d:
            problems.append(f"{open_c}{close_c} 不平衡: {d:+d}")

    # 顶层函数定义数量做一次"看起来是否完整"的粗判（入口文件必须要有 main）
    base = Path(path).name
    if base == "main.go" and "func main(" not in src:
        problems.append("Go 入口文件缺少 func main(")
    if base in ("main.c", "main.m") and " main(" not in src:
        problems.append("C 入口文件缺少 main 函数")

    # 本地 #include "x.h" 必须真的存在 —— 把源文件拆成多个文件后，
    # 最容易犯的错就是 include 名字写错，而本机没有编译器能发现它。
    for inc in re.findall(r'#\s*include\s+"([^"]+)"', src):
        if not (Path(path).parent / inc).exists():
            problems.append(f'#include "{inc}" 指向的文件不存在')

    # Go 的未使用 import 是**编译错误**（不是警告），拆文件时必须查
    if kind == "go":
        for pkg in go_imports(src):
            name = pkg.rsplit("/", 1)[-1]
            if not re.search(rf"\b{re.escape(name)}\s*\.", src):
                problems.append(f"import {name} 未被使用（Go 编译错误）")
    return problems


GO_IMPORT_BLOCK = re.compile(r"^import\s*\(([^)]*)\)", re.M)
GO_IMPORT_SINGLE = re.compile(r'^import\s+(?:\w+\s+)?"([^"]+)"', re.M)


def go_imports(src: str) -> list[str]:
    """取出 Go 文件 import 的包路径（支持块式与单行两种写法）。"""
    pkgs: list[str] = []
    for block in GO_IMPORT_BLOCK.findall(src):
        for line in block.splitlines():
            line = line.split("//")[0].strip()
            if line.startswith('"') and line.endswith('"'):
                pkgs.append(line.strip('"'))
    pkgs.extend(GO_IMPORT_SINGLE.findall(src))
    return pkgs


def check_python(path: str) -> list[str]:
    src = open(path, encoding="utf-8", newline="").read()
    problems: list[str] = []
    try:
        ast.parse(src)
    except SyntaxError as e:
        problems.append(f"语法错误 第 {e.lineno} 行: {e.msg}")
    # 非法转义序列（如非 raw 文档字符串里的 `\ `）今天只是 SyntaxWarning，
    # 未来 Python 版本会升级为错误；而且它常出现在 ASCII 图里，很容易漏掉。
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            compile(src, path, "exec")
        except SyntaxError:
            pass
        except ValueError:
            pass
    for item in caught:
        if "invalid escape" in str(item.message):
            problems.append(f"非法转义序列 第 {item.lineno} 行（建议改 raw 字符串）")
    return problems


def check(path: str) -> list[str]:
    if path.endswith(".py"):
        return check_python(path)
    return check_c_like(path)


def expand(patterns: list[str]) -> list[str]:
    files: list[str] = []
    for p in patterns:
        files.extend(sorted(glob.glob(p, recursive=True)))
    # glob 会把**目录**也匹配进来（仓库里真有个目录叫 `Node.js`，
    # 用 "**/*.js" 时会命中并触发 PermissionError）——必须按类型过滤。
    return [f for f in files if os.path.isfile(f) and "node_modules" not in f.split(os.sep)]


def fix_crlf(files: list[str]) -> int:
    n = 0
    for f in files:
        src = open(f, encoding="utf-8", newline="").read()
        if "\r\n" in src:
            open(f, "w", encoding="utf-8", newline="").write(src.replace("\r\n", "\n"))
            print(f"[FIX ] {f}  CRLF -> LF")
            n += 1
    return n


def main(argv: list[str]) -> int:
    args = argv[1:]
    do_fix = "--fix-crlf" in args
    patterns = [a for a in args if not a.startswith("--")] or ["*.c", "*.go", "*.py"]
    files = expand(patterns)
    if do_fix:
        print(f"CRLF 归一 {fix_crlf(files)} 个文件")
    bad = 0
    crlf = 0
    for f in files:
        if has_crlf(f):
            crlf += 1
        probs = check(f)
        if probs:
            bad += 1
            print(f"[WARN] {f}")
            for p in probs[:8]:
                print(f"       {p}")
    # CRLF 单独汇总：本机 core.autocrlf=true，工作区本就是 CRLF，
    # 逐文件刷屏会把真正的语法问题淹掉（2026-09-15 修缮）。
    print(f"检查 {len(files)} 个文件，{bad} 个有语法/结构问题；其中 {crlf} 个文件是 CRLF 行尾")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
