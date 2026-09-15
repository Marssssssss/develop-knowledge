#!/usr/bin/env python3
"""粗粒度语法健康检查（本机无 C/Go 工具链时的兜底）。

做三件事：
  1. .py —— 用 ast.parse 真编译，最可靠；
  2. .c/.go —— 先剥掉注释、字符串（含 Go 反引号裸字符串）与字符字面量，
     再检查 () [] {} 是否平衡，以及是否缺少 main；
  3. 所有文件 —— 检查 CRLF 行尾混用。

它**不能替代编译器**，只能挡住"漏大括号/引号未闭合"这类低级错误。

用法：python3 _docs/tools/syntax_sanity.py "03-系统编程/**/*.c" "**/*.go" ...
"""

from __future__ import annotations

import ast
import glob
import re
import sys
from pathlib import Path

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT = re.compile(r"//[^\n]*")
STR_PAT = re.compile(r'"(?:\\.|[^"\\])*"')
CHR_PAT = re.compile(r"'(?:\\.|[^'\\])*'")
# Go 的裸字符串（反引号）可以跨行且内部不转义，必须先整体剥掉，否则它内部的
# 括号会被误算 —— 曾因此把 SO-REUSEADDR/go/main.go 误判为括号不平衡。
GO_RAW_PAT = re.compile(r"`[^`]*`", re.S)


def strip_noise(src: str, is_go: bool = False) -> str:
    out = BLOCK_COMMENT.sub(" ", src)
    if is_go:
        out = GO_RAW_PAT.sub('""', out)
    out = LINE_COMMENT.sub("", out)
    out = STR_PAT.sub('""', out)
    out = CHR_PAT.sub("''", out)
    return out


def check_c_like(path: str) -> list[str]:
    src = open(path, encoding="utf-8", newline="").read()
    problems: list[str] = []

    if "\r\n" in src:
        problems.append("含 CRLF 行尾（本仓库统一 LF）")

    body = strip_noise(src, is_go=path.endswith(".go"))
    for open_c, close_c in (("{", "}"), ("(", ")"), ("[", "]")):
        d = body.count(open_c) - body.count(close_c)
        if d:
            problems.append(f"{open_c}{close_c} 不平衡: {d:+d}")

    # 顶层函数定义数量做一次"看起来是否完整"的粗判（入口文件必须要有 main）
    base = Path(path).name
    if base == "main.go" and "func main(" not in src:
        problems.append("Go 入口文件缺少 func main(")
    if base == "main.c" and "int main(" not in src and "main(void)" not in src:
        problems.append("C 入口文件缺少 main 函数")

    # 本地 #include "x.h" 必须真的存在 —— 把源文件拆成多个文件后，
    # 最容易犯的错就是 include 名字写错，而本机没有编译器能发现它。
    for inc in re.findall(r'#\s*include\s+"([^"]+)"', src):
        if not (Path(path).parent / inc).exists():
            problems.append(f'#include "{inc}" 指向的文件不存在')

    # Go 的未使用 import 是**编译错误**（不是警告），拆文件时必须查
    if path.endswith(".go"):
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
    if "\r\n" in src:
        problems.append("含 CRLF 行尾（本仓库统一 LF）")
    try:
        ast.parse(src)
    except SyntaxError as e:
        problems.append(f"语法错误 第 {e.lineno} 行: {e.msg}")
    return problems


def check(path: str) -> list[str]:
    if path.endswith(".py"):
        return check_python(path)
    return check_c_like(path)


def main(argv: list[str]) -> int:
    patterns = argv[1:] or ["*.c", "*.go", "*.py"]
    files: list[str] = []
    for p in patterns:
        files.extend(sorted(glob.glob(p, recursive=True)))
    # 只检查源码，跳过工具自身与第三方
    files = [f for f in files if not f.startswith("node_modules")]
    bad = 0
    for f in files:
        probs = check(f)
        if probs:
            bad += 1
            print(f"[WARN] {f}")
            for p in probs[:8]:
                print(f"       {p}")
    print(f"检查 {len(files)} 个文件，{bad} 个有问题")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
