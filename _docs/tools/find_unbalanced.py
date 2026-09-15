#!/usr/bin/env python3
"""定位 C/Go 文件里 () [] {} 不平衡的具体行（语法健全性检查的辅助工具）。

用法：python3 _docs/tools/find_unbalanced.py <文件路径>
"""

from __future__ import annotations

import re
import sys

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT = re.compile(r"//[^\n]*")
STR_PAT = re.compile(r'"(?:\\.|[^"\\])*"')
CHR_PAT = re.compile(r"'(?:\\.|[^'\\])*'")


def clean(line: str) -> str:
    line = LINE_COMMENT.sub("", line)
    line = STR_PAT.sub('""', line)
    return CHR_PAT.sub("''", line)


def main(path: str) -> int:
    raw = open(path, encoding="utf-8", newline="").read()
    raw = BLOCK_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), raw)
    depth = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    for i, line in enumerate(raw.split("\n"), 1):
        for ch in clean(line):
            if ch in depth:
                depth[ch] += 1
            elif ch in pairs:
                depth[pairs[ch]] -= 1
        if any(v < 0 for v in depth.values()):
            print(f"第 {i} 行后出现负深度 {depth}  {line.strip()[:80]}")
    print(f"最终深度: {depth}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
