#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cfg_builder.py —— 控制流图恢复与结构化反编译（入口 / 分析驱动）

链路：
  指令流 → 基本块划分(leader 算法) → CFG(含虚拟 entry/exit)
        → 逆后序 RPO → 迭代式支配者求解 → 支配树
        → 回边与自然循环 → 可归约性判定
        → 结构恢复（while / if-else），不可结构化时退化为 goto

三个测试程序分别覆盖：单循环 / 2 路条件汇合 / 不可归约（多入口循环）。

模块拆分（OPTIMIZATION.md §1.1）：
  cfg_core.py    指令集 + 切块 + 连边 + 支配树
  cfg_struct.py  回边 / 自然循环 / 可归约性 / 结构恢复
  cfg_builder.py 测试程序 + 分析驱动 + CLI（本文件）

运行：python3 cfg_builder.py
参考：Nystrom (Cornell) §2.1-2.1.2；Cifuentes, "Structuring Decompiled Graphs" (1994)
"""

from __future__ import annotations

from cfg_core import (Instr, build_cfg, compute_idom, dom_tree_depth,
                      dominators_of, split_basic_blocks)
from cfg_struct import (Structurer, back_edges, check_reducible, loop_entries,
                        natural_loop)

# ------------------------------------------------------------- 测试程序


def prog_count_down() -> list[Instr]:
    """单循环：while (n != 0) { out; n -= 1 }"""
    return [
        Instr(0x00, "loadi", 5),
        Instr(0x02, "jz", 0x0A),
        Instr(0x04, "out"),
        Instr(0x06, "subi", 1),
        Instr(0x08, "jmp", 0x02),
        Instr(0x0A, "halt"),
    ]


def prog_if_else() -> list[Instr]:
    """2 路条件：if (Z) { x -= 1 } else { x += 1 }"""
    return [
        Instr(0x00, "loadi", 3),
        Instr(0x02, "jz", 0x08),
        Instr(0x04, "addi", 1),
        Instr(0x06, "jmp", 0x0A),
        Instr(0x08, "subi", 1),
        Instr(0x0A, "out"),
        Instr(0x0C, "halt"),
    ]


def prog_irreducible() -> list[Instr]:
    """不可归约：循环体有两个外部入口（B1 与 B2）"""
    return [
        Instr(0x00, "jz", 0x06),      # 入口 A：条件成立跳到 L1
        Instr(0x02, "out"),           # 循环体的另一个入口
        Instr(0x04, "jmp", 0x08),
        Instr(0x06, "jmp", 0x02),     # L1 -> 进入循环体
        Instr(0x08, "jnz", 0x06),     # L2 -> 回到 L1
    ]


# ----------------------------------------------------------------- 分析

def analyze(name: str, prog: list[Instr]) -> None:
    print("=" * 72)
    print(f"程序 {name}")
    print("=" * 72)
    print("指令流：")
    for ins in prog:
        print(f"  0x{ins.addr:02x}: {ins.text()}")

    blocks = split_basic_blocks(prog)
    build_cfg(blocks, prog)

    print("\n基本块划分（leader 算法）：")
    for b in blocks.values():
        addrs = " ".join(f"0x{a:02x}" for a in b.addrs)
        print(f"  B{b.idx}: [{addrs}]  {b.instrs[-1].text():<10} "
              f"→ {['B%d' % s for s in b.succs] or ['(exit)']}")

    entry = 0
    idom = compute_idom(blocks, entry)
    depth = dom_tree_depth(idom, entry)

    print("\n支配关系：")
    for b in blocks.values():
        doms = sorted(dominators_of(b.idx, idom))
        tree = " → ".join(f"B{d}" for d in doms)
        print(f"  B{b.idx}: dom = {{{', '.join('B%d' % d for d in doms)}}}"
              f"   idom = B{idom.get(b.idx, b.idx)}   路径 {tree}")

    bes = back_edges(blocks, idom)
    print(f"\n回边（h 支配 n）：{['B%d→B%d' % e for e in bes] or ['无']}")

    loops: dict[int, set[int]] = {}
    for (n, h) in bes:
        lp = natural_loop(blocks, n, h)
        loops[h] = lp
        ents = loop_entries(blocks, lp)
        members = ", ".join(f"B{x}" for x in sorted(lp))
        flag = "单入口 ✓" if ents == {h} else "多入口 ✗ " + ", ".join(f"B{x}" for x in sorted(ents))
        print(f"  自然循环 header=B{h}: {{{members}}}  "
              f"外部入口={{{', '.join('B%d' % x for x in sorted(ents))}}}  {flag}")

    ok, bad = check_reducible(blocks, entry, idom)
    print(f"\n可归约性：{'可归约 ✓（所有回退边都是回边）' if ok else '不可归约 ✗'}")
    if not ok:
        print(f"  违规边（是 DFS 回退边但非支配性回边）："
              f"{['B%d→B%d' % e for e in bad]}")
        print("  → 循环存在多个外部入口，结构化模板不适用；需退化为 goto 或做节点分裂")

    print("\n结构恢复结果：")
    if ok:
        st = Structurer(blocks, idom, loops, depth)
        lines = st.emit_lines(entry, None, 0)
        print("  " + "\n  ".join(lines) if lines else "  （空）")
    else:
        print("  /* 不可归约：无法用 while / if 表达，真实反编译器在此输出 goto 标签 */")
        for b in blocks.values():
            nxt = ", ".join(f"B{s}" for s in b.succs) or "exit"
            print(f"  L{b.idx}:  {b.last.text():<10} ; 后继 {nxt}")
    print()


def main() -> None:
    analyze("count_down（单循环）", prog_count_down())
    analyze("if_else（2 路条件汇合）", prog_if_else())
    analyze("irreducible（多入口循环）", prog_irreducible())
    print("=" * 72)
    print("小结：")
    print("  · 支配树是全流程的枢纽：回边判定、循环头、follow 节点都靠它")
    print("  · 结构化语言产生的 CFG 一定可归约；goto / 优化器可能造出不可归约图")
    print("  · 不可归约时不做节点分裂，就只能退化成 goto —— 这正是现代反编译器")
    print("    输出里偶尔出现 goto 的根本原因")
    print("=" * 72)


if __name__ == "__main__":
    main()
