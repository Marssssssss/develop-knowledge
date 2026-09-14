#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cfg_core.py —— 玩具指令集、基本块划分、CFG 连边、支配关系与支配树

自 `cfg_builder.py` 拆出（OPTIMIZATION.md §1.1「单源代码文件 ≤ 300 行」）。
模块划分：
    cfg_core.py    指令集 + 切块 + 连边 + 支配树（本文件）
    cfg_struct.py  回边 / 自然循环 / 可归约性 / 结构恢复
    cfg_builder.py 测试程序 + 分析驱动 + CLI

参考：Nystrom (Cornell) §2.1-2.1.2；Cooper-Harvey-Kennedy 迭代式支配者求解
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ------------------------------------------------------------ 玩具指令集

#: 所有指令固定 2 字节：(opcode, arg)
TERMINATORS = {"jmp", "jz", "jnz", "halt"}
BRANCHES = {"jmp", "jz", "jnz"}
COND = {"jz": "Z", "jnz": "NZ"}   # 条件码助记


@dataclass(frozen=True)
class Instr:
    addr: int
    mnem: str
    arg: Optional[int] = None

    def text(self) -> str:
        return f"{self.mnem} {self.arg}" if self.arg is not None else self.mnem


@dataclass
class BasicBlock:
    idx: int
    addrs: list[int] = field(default_factory=list)
    instrs: list[Instr] = field(default_factory=list)
    succs: list[int] = field(default_factory=list)
    preds: list[int] = field(default_factory=list)

    @property
    def start(self) -> int:
        return self.addrs[0]

    @property
    def last(self) -> Instr:
        return self.instrs[-1]


# ------------------------------------------------------- 1. 基本块划分

def split_basic_blocks(prog: list[Instr]) -> dict[int, BasicBlock]:
    """leader 算法：跳转目标开始一个块，跳转指令结束一个块"""
    addr_set = {i.addr for i in prog}
    leaders = {prog[0].addr}
    for i, ins in enumerate(prog):
        if ins.mnem in BRANCHES and ins.arg is not None:
            if ins.arg in addr_set:
                leaders.add(ins.arg)          # 跳转目标
        if ins.mnem in TERMINATORS and i + 1 < len(prog):
            leaders.add(prog[i + 1].addr)     # 跳转的下一条
    leaders = sorted(a for a in leaders if a in addr_set)

    blocks: dict[int, BasicBlock] = {}
    cur: Optional[BasicBlock] = None
    for ins in prog:
        if ins.addr in leaders or cur is None:
            cur = BasicBlock(idx=len(blocks))
            blocks[cur.idx] = cur
        cur.addrs.append(ins.addr)
        cur.instrs.append(ins)
    return blocks


# ------------------------------------------------------------- 2. 连边

def build_cfg(blocks: dict[int, BasicBlock], prog: list[Instr]) -> None:
    """连边；无分支的块连到"下一条指令所在块" """
    addr_to_block = {a: b.idx for b in blocks.values() for a in b.addrs}
    next_block = {}
    order = [b.idx for b in sorted(blocks.values(), key=lambda x: x.start)]
    for i, bi in enumerate(order):
        next_block[bi] = order[i + 1] if i + 1 < len(order) else None

    for b in blocks.values():
        last = b.last
        if last.mnem in BRANCHES and last.arg is not None:
            tgt = addr_to_block.get(last.arg)
            if tgt is not None:
                b.succs.append(tgt)                     # 跳转分支
            if last.mnem != "jmp" and next_block[b.idx] is not None:
                b.succs.append(next_block[b.idx])       # 条件不成立分支
        elif last.mnem == "halt":
            pass                                        # 出口，无后继
        else:
            if next_block[b.idx] is not None:
                b.succs.append(next_block[b.idx])

    for b in blocks.values():
        for s in b.succs:
            blocks[s].preds.append(b.idx)


# --------------------------------------------------- 3. 支配关系与支配树

def reverse_postorder(blocks: dict[int, BasicBlock], entry: int) -> list[int]:
    seen: set[int] = set()
    order: list[int] = []

    def dfs(n: int) -> None:
        seen.add(n)
        for s in blocks[n].succs:
            if s not in seen:
                dfs(s)
        order.append(n)

    dfs(entry)
    order.reverse()
    return order


def dom_intersect(a: int, b: int, idom: dict[int, int],
                  rpo_index: dict[int, int]) -> int:
    """沿支配树同时上溯，遇到的第一个公共节点即 idom 候选（Cooper-Harvey-Kennedy）"""
    while a != b:
        while rpo_index[a] > rpo_index[b]:
            a = idom[a]
        while rpo_index[b] > rpo_index[a]:
            b = idom[b]
    return a


def compute_idom(blocks: dict[int, BasicBlock], entry: int) -> dict[int, int]:
    """迭代式支配者求解：朴素但直观；工程实现用 Lengauer-Tarjan O(E·α(E,V))"""
    rpo = reverse_postorder(blocks, entry)
    rpo_index = {n: i for i, n in enumerate(rpo)}
    idom: dict[int, int] = {entry: entry}
    changed = True
    while changed:
        changed = False
        for n in rpo:
            if n == entry:
                continue
            preds = [p for p in blocks[n].preds if p in idom]
            if not preds:
                continue
            new = preds[0]
            for p in preds[1:]:
                new = dom_intersect(new, p, idom, rpo_index)
            if idom.get(n) != new:
                idom[n] = new
                changed = True
    return idom


def dominators_of(n: int, idom: dict[int, int]) -> set[int]:
    """从 n 沿 idom 上溯到根，得到 n 的全部支配者"""
    out = {n}
    cur = n
    while cur in idom and idom[cur] != cur:
        cur = idom[cur]
        out.add(cur)
    return out


def dom_tree_depth(idom: dict[int, int], root: int) -> dict[int, int]:
    depth: dict[int, int] = {}

    def walk(n: int, d: int) -> None:
        depth[n] = d
        for k, v in idom.items():
            if v == n and k != n:
                walk(k, d + 1)

    walk(root, 0)
    return depth
