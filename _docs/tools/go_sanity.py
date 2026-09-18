#!/usr/bin/env python3
"""Go 源码机械核查(无 go 工具链时的替代品)。

用法:
    python go_sanity.py <file.go> [file2.go ...]
    python go_sanity.py go/*.go            # 通配符交给 shell 展开

选项:
    --no-argcheck        跳过参个数检查(跨 demo 批量回归时用,各 demo 的
                         check 签名不同:多数为 3 参,部分历史 demo 为 4 参)
    --spec check=4       覆盖某个断言函数的期望参个数(可重复)

检查三项:
  1. 未使用的 import —— Go 里这是**硬编译错**,拆文件后最易踩
  2. 断言函数参个数 —— 默认 `check(label, cond, detail)` 3 参、`expectErr(label, err)` 2 参
  3. 重复的函数/类型定义 —— 拆文件时两边各留一份是最隐蔽的错误
     (按「目录 + receiver 类型 + 名字」判重,跨包同名与合法覆盖都不误报)

退出码 0 = 全部通过,1 = 有问题。

已知边界:
  - import 使用判定按「包名后跟点」的文本匹配,不做类型分析;`sort.Strings`
    能被识别,把它赋给变量后再调用则识别不到(极少见)。
  - 参个数判定用「括号/引号感知的顶层逗号拆分」,支持嵌套调用、字符串字面量
    (含反引号原始字符串与 `'\\''` 转义)、rune 字面量。
  - 只统计 `func name` / `func (recv) name`,不区分方法与函数。
"""

import io
import os
import re
import sys


def strip_literals_and_comments(src, fill=" "):
    """把字符串/字符字面量与注释替换成等长填充,保留偏移与换行。

    fill 默认空格。计数实参时要传一个非空白字符(如 "x"),否则空串实参
    `check("a", cond, "")` 在拆分后会因 strip() 为空而被当成「没传」。
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in ('"', "`"):
            quote, j = c, i + 1
            while j < n:
                if quote != "`" and src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    break
                j += 1
            end = min(j + 1, n)
            out.append("".join(ch if ch == "\n" else fill for ch in src[i:end]))
            i = end
            continue
        if c == "'":
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "'":
                    break
                j += 1
            end = min(j + 1, n)
            out.append("".join(ch if ch == "\n" else fill for ch in src[i:end]))
            i = end
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i)
            j = n if j < 0 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in src[i:j]))
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


def find_calls(src, name):
    """返回 [(行号, 括号内文本)],按最外层括号配对。忽略注释与字面量中的伪调用。"""
    clean = strip_literals_and_comments(src)
    found = []
    for m in re.finditer(r"(?<![A-Za-z0-9_.])" + re.escape(name) + r"\(", clean):
        i = m.end()
        depth, j = 1, i
        while j < len(clean) and depth > 0:
            if clean[j] in "([{":
                depth += 1
            elif clean[j] in ")]}":
                depth -= 1
            j += 1
        found.append((src[: m.start()].count("\n") + 1, src[i : j - 1]))
    return found


def top_level_args(s):
    """按顶层逗号拆分参数,忽略嵌套括号内的逗号。"""
    parts, cur, depth = [], "", 0
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    if cur.strip():
        parts.append(cur)
    return parts


def check_file(path, call_specs, pkg_src=None):
    src = io.open(path, encoding="utf-8").read()
    # 变参定义可能与调用点不在同一个文件（同 main 包多文件），故用整包源码判定
    pkg_src = src if pkg_src is None else pkg_src
    clean = strip_literals_and_comments(src)
    issues = []

    m = re.search(r"import \(\n(.*?)\n\)", clean, re.S)
    if m:
        body = clean[: m.start()] + clean[m.end() :]
        for line in m.group(1).split("\n"):
            pkg_path = line.strip().strip('"')
            if not pkg_path:
                continue
            pkg = pkg_path.split("/")[-1]
            if not re.search(r"(?<![A-Za-z0-9_.])" + re.escape(pkg) + r"\.", body):
                issues.append("未使用的 import: %s" % pkg_path)

    # 变参函数（如 `func check(label string, cond bool, detail ...string)`）：
    # 末位参数可省略，故合法实参个数是 [want-1, +inf)。不识别会把全仓
    # 大量合法的 2 参调用报成错误，把真正的漏参淹没掉。
    variadic = set()
    for name, _want in call_specs:
        for m in re.finditer(r"^func\s+" + re.escape(name) + r"\s*\(([^)]*)\)",
                             pkg_src, re.M):
            if "..." in m.group(1):
                variadic.add(name)

    for name, want in call_specs:
        for lineno, args in find_calls(src, name):
            # find_calls 返回的是**原始**子串（字面量内容原样保留），
            # 直接交给 top_level_args 会把字符串里的逗号当成实参分隔符
            # （如 check("x", f(a) == "Set-Cookie,ETag", "") 被数成 4 个）。
            # 先按字面量置空再拆分——strip_literals_and_comments 保持偏移不变。
            got = len(top_level_args(strip_literals_and_comments(args, fill="x")))
            ok = got >= want - 1 if name in variadic else got == want
            if not ok:
                issues.append(
                    "第 %d 行 %s(...) 参数个数 %d,期望 %d" % (lineno, name, got, want)
                )
    return issues


def collect_defs(paths):
    """收集 (目录, receiver 类型.名字) 二元键。

    两个维度都不能省:
      - receiver:同一个名字在不同 receiver 上是合法的"覆盖"
        (SendingQueue.Enqueue 与 PersistentQueue.Enqueue)。
      - 目录:同一批入参里可能含多个 package(每个 demo 一个 main 包),
        跨目录的 main/check 同名完全合法。
    """
    funcs, types = [], []
    for p in paths:
        src = io.open(p, encoding="utf-8").read()
        d = os.path.dirname(os.path.abspath(p))
        for recv, name in re.findall(
            r"^func (?:\(([^)]*)\) )?([A-Za-z_][A-Za-z0-9_]*)", src, re.M
        ):
            rtype = ""
            if recv.strip():
                rtype = recv.strip().split()[-1].lstrip("*")
            key = "%s.%s" % (rtype, name) if rtype else name
            funcs.append((d, key))
        types += [(d, t) for t in re.findall(r"^type ([A-Za-z_][A-Za-z0-9_]*)", src, re.M)]
    return funcs, types


def main(argv):
    specs = {"check": 3, "expectErr": 2}
    argcheck = True
    paths = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--no-argcheck":
            argcheck = False
        elif a == "--spec":
            i += 1
            name, _, arity = argv[i].partition("=")
            specs[name] = int(arity)
        else:
            paths.append(a)
        i += 1
    if not paths:
        print(__doc__)
        return 2
    call_specs = list(specs.items()) if argcheck else []
    total = 0
    from collections import defaultdict
    pkg_srcs = defaultdict(list)
    for p in paths:
        pkg_srcs[os.path.dirname(os.path.abspath(p))].append(
            io.open(p, encoding="utf-8").read())
    for p in paths:
        d = os.path.dirname(os.path.abspath(p))
        issues = check_file(p, call_specs, "\n".join(pkg_srcs[d]))
        if issues:
            print("== %s" % p)
            for s in issues:
                print("   - " + s)
            total += len(issues)
    funcs, types = collect_defs(paths)
    for label, items in (("函数", funcs), ("类型", types)):
        counts = {}
        for d, key in items:
            counts[(d, key)] = counts.get((d, key), 0) + 1
        dups = sorted(
            "%s (%s)" % (key, os.path.basename(d))
            for (d, key), c in counts.items()
            if c > 1
        )
        if dups:
            print("== 重复%s定义: %s" % (label, ", ".join(dups)))
            total += len(dups)
    if total:
        print("FAILED  %d 处问题" % total)
        return 1
    print("OK  %d 个文件通过(import / 参个数 / 定义唯一性)" % len(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
