#!/usr/bin/env python3
"""结构校验:剔除注释与字符串字面量后,检查括号配平与文件行数上限。"""
import sys
import pathlib

LIMIT = 300


def strip_code(src, lang):
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
