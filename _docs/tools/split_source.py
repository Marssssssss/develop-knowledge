#!/usr/bin/env python3
"""把超长源文件按行区间拆分到新文件（配合 OPTIMIZATION.md §1.1 的 ≤300 行硬约束）。

为什么需要它：一个 400+ 行的文件要拆，但**不能重打一遍**（重打等于把已验证的
代码重新引入错字）。这个工具只做搬运，不做改写，所以被搬运的代码逐字节不变。

用法：
    python3 _docs/tools/split_source.py spec.json

spec.json 是一个数组，每个元素一次搬运：
    {
      "src":         "绝对路径/被拆的文件",
      "start":       38,          # 1-based，含
      "end":         216,         # 1-based，含
      "dst":         "绝对路径/新文件",
      "mode":        "cut",       # cut = 从 src 剪下并搬到 dst
                                  # head = 把 src 的 1..end 整段搬到 dst
      "preamble":    "新文件开头的说明/保护宏（可省）",
      "insert_text": "#include \"x_impl.h\"",   # 剪下后 src 原位补回的文本（可省）
      "append":      false,       # dst 已存在时是否追加（多段搬进同一文件）
      "dedent":      0            # 搬到 dst 时整体去掉的缩进空格数（可省）
    }

同一文件的多次搬运按 start **倒序**处理，避免行号漂移。搬运后打印每个文件的
行数变化表，便于核对是否都落到了 300 行以内。

为什么 C 用"实现头文件"而不是拆成两个 .c：本机没有 C 工具链，拆成两个翻译单元
必须同步改 static/原型，改错就编译不过且无法本地发现。用 #include 是**文本级**
包含，所有 static 定义仍在同一个 TU，零链接风险。Go 同包多文件、Python 多模块
同理，都是零语义变化的搬运。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

    # 每个 src 内部的多次搬运倒序执行，保证行号不漂移
    for src in {op["src"] for op in spec}:
        ops = [op for op in spec if op["src"] == src]
        ops.sort(key=lambda o: o["start"], reverse=True)
        lines = Path(src).read_text(encoding="utf-8").splitlines(keepends=True)
        before = len(lines)
        for op in ops:
            start, end = op["start"], op["end"]
            if not (1 <= start <= end <= len(lines)):
                print(f"  拒绝：{src} 的区间 {start}..{end} 越界（现有 {len(lines)} 行）")
                return 1
            block = lines[start - 1:end]
            dedent = op.get("dedent", 0)
            if dedent:
                pad = " " * dedent
                block = [ln[len(pad):] if ln.startswith(pad) else ln for ln in block]
            dst = Path(op["dst"])
            head = ""
            if not (op.get("append") and dst.exists()):
                head = op.get("preamble", "") or ""
                if head and not head.endswith("\n"):
                    head += "\n"
            with dst.open("a" if op.get("append") else "w", encoding="utf-8", newline="") as fh:
                fh.write(head)
                fh.write("".join(block))
            ins = op.get("insert_text", "")
            if ins and not ins.endswith("\n"):
                ins += "\n"
            lines[start - 1:end] = [ins] if ins else []
            print(f"  搬 {op['dst'].split('/')[-1]:24s} <- {src.split('/')[-1]} "
                  f"[{start}..{end}] {end - start + 1} 行")
        text = "".join(lines)
        Path(src).write_text(text, encoding="utf-8", newline="")
        after = len(text.splitlines())
        print(f"  → {src.split('/')[-1]}: {before} 行 → {after} 行"
              f"{'  ✅' if after <= 300 else '  ❌ 仍超 300'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
