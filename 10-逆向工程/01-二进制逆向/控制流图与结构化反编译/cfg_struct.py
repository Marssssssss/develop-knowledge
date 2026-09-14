#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cfg_struct.py —— 回边与自然循环、可归约性判定、结构化恢复（Cifuentes 风格）

自 `cfg_builder.py` 拆出（OPTIMIZATION.md §1.1「单源代码文件 ≤ 300 行」）。
依赖 `cfg_core.py` 提供的 BasicBlock / 支配关系。

参考：Cifuentes, "Structuring Decompiled Graphs" (1994)
"""

from __future__ import annotations

from typing import Optional

from cfg_core import COND, BasicBlock, dominators_of

# ------------------------------------------------------- 4. 回边与自然循环


def back_edges(blocks: dict[int, BasicBlock], idom: dict[int, int]) -> list[tuple[int, int]]:
    """回边 = (n -> h) 且 h 支配 n。注意：DFS 祖先只是必要条件，支配性才是定义"""
    out = []
    for b in blocks.values():
        for s in b.succs:
            if b.idx == s or s in dominators_of(b.idx, idom):
                out.append((b.idx, s))
    return out


def natural_loop(blocks: dict[int, BasicBlock], n: int, h: int) -> set[int]:
    """节点集 = {h} ∪ {所有能不经过 h 到达 n 的节点}"""
    loop = {h, n}
    stack = [n]
    while stack:
        m = stack.pop()
        for p in blocks[m].preds:
            if p not in loop:
                loop.add(p)
                if p != h:
                    stack.append(p)
    return loop


def loop_entries(blocks: dict[int, BasicBlock], loop: set[int]) -> set[int]:
    """循环的**外部入口块**：源在循环外、目标在循环内的边的目标集合。

    入口不止循环头 ⇒ 多入口循环 ⇒ 必然不可归约（README 坑 6）。
    """
    return {s for b in blocks.values() for s in b.succs
            if s in loop and b.idx not in loop}


# ------------------------------------------------------- 5. 可归约性判定

def dfs_ancestry(blocks: dict[int, BasicBlock], entry: int):
    """DFS 的 pre/post 编号，用于判定"回退边"（retreating edge）"""
    pre: dict[int, int] = {}
    post: dict[int, int] = {}
    counter = [0]

    def dfs(n: int) -> None:
        pre[n] = counter[0]
        counter[0] += 1
        for s in blocks[n].succs:
            if s not in pre:
                dfs(s)
        post[n] = counter[0]
        counter[0] += 1

    dfs(entry)
    return pre, post


def check_reducible(blocks: dict[int, BasicBlock], entry: int,
                    idom: dict[int, int]) -> tuple[bool, list[tuple[int, int]]]:
    pre, post = dfs_ancestry(blocks, entry)
    bad = []
    for b in blocks.values():
        for s in b.succs:
            u, v = b.idx, s
            if u not in pre or v not in pre:
                continue
            retreating = pre[v] <= pre[u] and post[v] >= post[u]
            is_back = (u == v) or v in dominators_of(u, idom)
            if retreating and not is_back:
                bad.append((u, v))
    return (len(bad) == 0), bad


# ------------------------------------------------------- 6. 结构恢复

def arm_reachable(blocks: dict[int, BasicBlock], start: int) -> set[int]:
    seen: set[int] = set()
    stack = [start]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        stack.extend(blocks[n].succs)
    return seen


def find_follow(blocks: dict[int, BasicBlock], a: int, b: int,
                depth: dict[int, int]) -> Optional[int]:
    """follow 节点 = 两条分支第一个共同到达的节点（README 坑 5）"""
    common = arm_reachable(blocks, a) & arm_reachable(blocks, b)
    common.discard(a)
    common.discard(b)
    if not common:
        return None
    return min(common, key=lambda n: (depth.get(n, 1 << 30), n))


class Structurer:
    def __init__(self, blocks, idom, loops, depth):
        self.b = blocks
        self.idom = idom
        self.loops = loops              # header -> set(blocks)
        self.depth = depth
        self.visited: set[int] = set()
        self.notes: list[str] = []

    def cond_of(self, block: BasicBlock) -> str:
        return COND.get(block.last.mnem, "?")

    def loop_cond_of(self, block: BasicBlock) -> str:
        """循环条件语义与 if 相反：jz 是"条件成立则跳出"，故继续条件取反"""
        last = block.last
        if last.mnem == "jz":
            return "!" + COND["jz"]
        if last.mnem == "jnz":
            return COND["jnz"]
        return "true"

    def emit_lines(self, b: int, follow: Optional[int], indent: int) -> list[str]:
        pad = "  " * indent
        out: list[str] = []
        cur: Optional[int] = b
        guard = 0
        while cur is not None and cur != follow and guard < 64:
            guard += 1
            if cur in self.visited:
                out.append(f"{pad}/* 回边汇入 B{cur}，结构在此闭合 */")
                break
            self.visited.add(cur)

            # --- 循环 ---
            if cur in self.loops:
                lp = self.loops[cur]
                hdr = self.b[cur]
                members = ", ".join(f"B{x}" for x in sorted(lp))
                out.append(f"{pad}while ({self.loop_cond_of(hdr)}) {{"
                           f"   /* 循环头 B{cur}，循环体 {{{members}}} */")
                for s in hdr.succs:
                    if s in lp:
                        out += self.emit_lines(s, cur, indent + 1)
                out.append(f"{pad}}}")
                outside = [s for s in hdr.succs if s not in lp]
                cur = outside[0] if outside else None
                continue

            # --- 2 路条件 ---
            if len(self.b[cur].succs) == 2:
                s0, s1 = self.b[cur].succs
                f = find_follow(self.b, s0, s1, self.depth)
                out.append(f"{pad}if ({self.cond_of(self.b[cur])}) {{   /* 判据在 B{cur} */")
                out += self.emit_lines(s0, f, indent + 1)
                out.append(f"{pad}}} else {{")
                out += self.emit_lines(s1, f, indent + 1)
                out.append(f"{pad}}}")
                cur = f
                continue

            # --- 普通块 ---
            blk = self.b[cur]
            # 尾部的 jmp 属于控制流而非代码（它指向 follow 或循环头），不作为语句输出
            for ins in blk.instrs:
                if ins is blk.last and ins.mnem == "jmp":
                    continue
                out.append(f"{pad}{ins.text()}      /* B{cur} @ 0x{ins.addr:02x} */")
            if blk.last.mnem == "halt":
                cur = None
            else:
                cur = blk.succs[0] if blk.succs else None
        return out
