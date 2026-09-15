#!/usr/bin/env python3
"""结构校验:剔除注释与字符串字面量后,检查括号配平与文件行数上限。

用法: syntax_lint.py <lang> <path>...
  lang ∈ {c, go, js, py, rs}
  - rs: 额外处理 char 字面量('\n')与生命周期标注('a),否则把 'a 误判成未闭合引号
"""
import re
import sys
import pathlib

LIMIT = 300


def strip_rust(src):
    """Rust 专用:先吃掉注释与字符串,再吃掉 char 字面量与生命周期标注。"""
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if src.startswith('r#"', i):
            j = src.find('"#', i + 3)
            i = n if j < 0 else j + 2
            continue
        c = src[i]
        if c == '"':
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if c == "'":
            # char 字面量 'x' / '\n' / '\''
            if i + 1 < n and (src[i + 2 : i + 3] == "'" or src[i + 1] == "\\"):
                j = src.find("'", i + 1 + (2 if src[i + 1] == "\\" else 1))
                i = n if j < 0 else j + 1
                continue
            # 生命周期标注 'a / 'static / '_
            m = re.match(r"'[A-Za-z_][A-Za-z0-9_]*", src[i:])
            if m:
                i += len(m.group(0))
                continue
        out.append(c)
        i += 1
    return "".join(out)


def strip_code(src, lang):
    if lang == "rs":
        return strip_rust(src)
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if lang == "c" and src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c in "\"'":
            q, i = c, i + 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == q:
                    i += 1
                    break
                i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def check(path, lang):
    text = pathlib.Path(path).read_text(encoding="utf-8")
    code = strip_code(text, lang)
    problems = []
    for op, cl in (("{", "}"), ("(", ")"), ("[", "]")):
        if code.count(op) != code.count(cl):
            problems.append(f"{op}{cl}: {code.count(op)} vs {code.count(cl)}")
    lines = text.count("\n") + 1
    if lines > LIMIT:
        problems.append(f"行数 {lines} > {LIMIT}(OPTIMIZATION §1.1)")
    tag = "OK " if not problems else "FAIL"
    print(f"[{tag}] {path}  ({lines} 行)" + ("" if not problems else "  " + "; ".join(problems)))
    return not problems


if __name__ == "__main__":
    lang = sys.argv[1]
    ok = all(check(p, lang) for p in sys.argv[2:])
    sys.exit(0 if ok else 1)
