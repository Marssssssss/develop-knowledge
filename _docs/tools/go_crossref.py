"""Go 跨文件符号交叉核对（无编译工具链时的补充手段）。

go_sanity.py 覆盖「未使用 import / 断言实参个数 / (目录,receiver,方法名) 唯一性」，
本脚本补两件它不做的：

1. 顶层（无 receiver）函数 / 类型 / 常量 是否重名；
2. 被调用的大写开头标识符是否在本包内有定义（即「调了不存在的函数」）。
"""

import glob
import re
import sys

FUNC_RE = re.compile(r"^func\s+([A-Za-z_]\w*)\s*\(", re.M)
METHOD_RE = re.compile(r"^func\s+\([^)]*\)\s*([A-Za-z_]\w*)", re.M)
TYPE_RE = re.compile(r"^type\s+([A-Za-z_]\w*)", re.M)
CONST_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=", re.M)
VARBLOCK_RE = re.compile(r"^(?:var|const)\s+\(([^)]*)\)", re.M | re.S)

STR_RE = re.compile(r'`[^`]*`|"(?:[^"\\]|\\.)*"')
# Go rune 字面量 `'"'` / `'\''` / `'\\'` / `'\t'`:必须先于 STR_RE 剔除,
# 否则 `== '"'` 里的那个双引号会被当成字符串起点,一路吞掉后面的 func 定义
# (2026-09-19 实测:helm_render.go 因此把 parseArg / EvalAction / Render / main
#  全部判成"未定义的大写调用")。
RUNE_RE = re.compile(r"'(?:\\.|[^'\\])'")
COMMENT_RE = re.compile(r"//[^\n]*")
CALL_RE = re.compile(r"(?<![.\w])([A-Z][A-Za-z0-9_]*)\s*\(")

STDLIB = {
    "Sprintf", "Sprintln", "Printf", "Println", "Atoi", "FormatFloat", "ParseFloat",
    "ToLower", "TrimPrefix", "HasPrefix", "Contains", "Builder", "Sum256",
    "EncodeToString", "MustCompile", "Compile", "Marshal", "Unmarshal", "Float64s",
    "Exit", "Panic",
}


def strip(src: str) -> str:
    src = RUNE_RE.sub("_", src)
    src = STR_RE.sub('""', src)
    return COMMENT_RE.sub("", src)


def main(paths: list[str]) -> int:
    funcs: dict[str, list[str]] = {}
    methods: set[str] = set()
    types: dict[str, list[str]] = {}
    consts: dict[str, list[str]] = {}
    bodies: dict[str, str] = {}

    for path in paths:
        src = open(path, encoding="utf-8").read()
        clean = strip(src)
        bodies[path] = clean
        for name in FUNC_RE.findall(clean):
            funcs.setdefault(name, []).append(path)
        methods.update(METHOD_RE.findall(clean))
        for name in TYPE_RE.findall(clean):
            types.setdefault(name, []).append(path)
        for block in VARBLOCK_RE.findall(clean):
            for name in CONST_RE.findall(block):
                consts.setdefault(name, []).append(path)
        for name in re.findall(r"^(?:var|const)\s+([A-Za-z_]\w*)\s*=", clean, re.M):
            consts.setdefault(name, []).append(path)

    problems = 0
    for label, table in (("func", funcs), ("type", types), ("const/var", consts)):
        for name, where in sorted(table.items()):
            if len(set(where)) > 1:
                print(f"  重名 {label} {name}: {sorted(set(where))}")
                problems += 1

    known = set(funcs) | set(types) | set(consts) | methods | STDLIB
    unknown: set[str] = set()
    for src in bodies.values():
        for name in CALL_RE.findall(src):
            if name not in known:
                unknown.add(name)
    for name in sorted(unknown):
        print(f"  未定义的大写调用: {name}")
        problems += 1

    print(f"  顶层函数 {len(funcs)} / 方法 {len(methods)} / 类型 {len(types)} / 常量变量 {len(consts)}")
    if problems:
        print(f"CROSSREF FAILED ({problems} 处)")
        return 1
    print("CROSSREF OK (无重名、无未定义调用)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
