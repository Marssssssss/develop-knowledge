#!/usr/bin/env python3
"""C 源码机械核查(无 C 工具链时的替代品)。

用法:
    python c_sanity.py <file.c> [file2.c ...]
    python c_sanity.py *.c
    python c_sanity.py --tu otel_demo.c otel_core.h otel_pipeline.h

检查:
  1. static 函数的定义参个数 vs 各处调用参个数不一致 —— C 里这是 UB/硬错,
     拆文件或改签名后最容易漏
  2. `#include "x.h"` 的本地头文件是否存在(实现头模式下必然存在于同目录)

`--tu` 把多个文件当作**同一个翻译单元**合并分析 —— 「实现头」模式下
(`#include "x_impl.h"`) 头里的 static 函数只会在 .c 里被调用,逐文件看
查不到任何交叉不一致,必须合并才有意义。

退出码 0 = 全部通过,1 = 有问题。

复用 go_sanity.py 的词法剥离与顶层逗号拆分,避免同一逻辑两处实现。
"""

import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from go_sanity import find_calls, strip_literals_and_comments, top_level_args  # noqa: E402


def count_args(text):
    """参数列表文本 -> 参数个数。空列表与 `void` 都是 0 个。

    必须先剔除字符串/字符字面量:字面量里的逗号(例如 `join(", ")` 或
    `kv_set(&ctx, "a", "coffee,tea,water")`)不是参数分隔符。
    2026-09-19 实测:helm_render.c 因此被误报「传 5 个参数,定义处为 4 个」。
    """
    s = strip_literals_and_comments(text, fill="x").strip()
    if s == "" or s == "void":
        return 0
    return len(top_level_args(s))


def extract_paren(src, lparen_index):
    """从 `(` 开始取到配对 `)` 之间的文本。"""
    depth, j = 0, lparen_index
    while j < len(src):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[lparen_index + 1 : j]
        j += 1
    return ""


def collect_defs(src):
    """static 函数定义 -> {名字: 参数个数}。"""
    defs = {}
    clean = strip_literals_and_comments(src)
    for m in re.finditer(r"^static\s+.*?([A-Za-z_][A-Za-z0-9_]*)\s*\(", clean, re.M | re.S):
        name = m.group(1)
        defs[name] = count_args(extract_paren(clean, m.end() - 1))
    return defs


def check_local_includes(path, src):
    issues = []
    base = os.path.dirname(os.path.abspath(path))
    for inc in re.findall(r'#include\s+"([^"]+)"', src):
        if not os.path.exists(os.path.join(base, inc)):
            issues.append('#include "%s" 找不到(同目录应存在该头文件)' % inc)
    return issues


def check_file(path):
    src = io.open(path, encoding="utf-8").read()
    issues = check_local_includes(path, src)
    defs = collect_defs(src)
    for name, want in sorted(defs.items()):
        for lineno, args in find_calls(src, name):
            got = count_args(args)
            if got != want:
                issues.append(
                    "第 %d 行 %s(...) 传 %d 个参数,定义处为 %d 个" % (lineno, name, got, want)
                )
    return issues, len(defs)


def check_translation_unit(paths, label):
    """把多个文件合并成一个翻译单元做参个数交叉检查。"""
    combined = "\n".join(io.open(p, encoding="utf-8").read() for p in paths)
    defs = collect_defs(combined)
    issues = []
    for name, want in sorted(defs.items()):
        for _lineno, args in find_calls(combined, name):
            got = count_args(args)
            if got != want:
                issues.append("%s(...) 传 %d 个参数,定义处为 %d 个" % (name, got, want))
    return issues, len(defs), label


def main(argv):
    tu_mode = "--tu" in argv
    paths = [a for a in argv if a != "--tu"]
    if not paths:
        print(__doc__)
        return 2
    total = 0
    if tu_mode:
        # 本地头存在性仍按单文件判
        for p in paths:
            for s in check_local_includes(p, io.open(p, encoding="utf-8").read()):
                print("== %s\n   - %s" % (p, s))
                total += 1
        issues, ndefs, label = check_translation_unit(paths, " + ".join(paths))
        if issues:
            print("== %s" % label)
            for s in issues:
                print("   - " + s)
            total += len(issues)
        else:
            print("OK  %s(%d 个 static 函数定义参个数一致)" % (label, ndefs))
        if total:
            print("FAILED  %d 处问题" % total)
            return 1
        return 0
    for p in paths:
        issues, ndefs = check_file(p)
        if issues:
            print("== %s" % p)
            for s in issues:
                print("   - " + s)
            total += len(issues)
        else:
            print("OK  %s(%d 个 static 函数定义参个数一致)" % (p, ndefs))
    if total:
        print("FAILED  %d 处问题" % total)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
