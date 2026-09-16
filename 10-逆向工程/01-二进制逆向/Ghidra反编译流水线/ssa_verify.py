#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SSA 性质校验与图形化 dump —— 从 pcode_ssa.py 拆出(单文件 ≤300 行硬约束)。

校验三条性质:唯一命名(每个 varnode 恰好一个定义)、定义支配所有使用、
phi 入边数 = 前驱数。其中 phi 的入边是**边上的使用**,判据是
「定义支配对应前驱」而不是「支配 phi 所在块」——这一点最易写错。
"""

from pcode_ssa import fmt, is_const


# --------------------------------------------------------------- 校验

def verify_ssa(func):
    """校验 SSA 三条性质:唯一命名、定义支配所有使用、phi 入边数 = 前驱数。

    注意 phi 的入边是**边上的使用**:第 k 个入边在「前驱 k → 本块」这条边上求值,
    因此要求它的定义支配**对应前驱**,而不是支配 phi 所在的块。这是手写 SSA 校验
    最容易搞错的一点(本 demo 首版就把它当普通使用,误报了两条)。
    """
    problems = []
    defs = {}
    for n in func.order:
        b = func.blocks[n]
        for phi in b.phis:
            if phi.out in defs:
                problems.append("重复定义 %s" % fmt(phi.out))
            defs[phi.out] = n
            if len(phi.ins) != len(b.preds):
                problems.append("phi @%s 入边 %d ≠ 前驱 %d"
                                % (n, len(phi.ins), len(b.preds)))
        for op in b.ops:
            if op.out is not None:
                if op.out in defs:
                    problems.append("重复定义 %s" % fmt(op.out))
                defs[op.out] = n
    for n in func.order:
        b = func.blocks[n]
        reach = set(func.dom_chain(n))
        for op in b.ops:
            for v in op.ins:
                if v is None or is_const(v):
                    continue
                if v in defs and defs[v] not in reach:
                    problems.append("%s 在 @%s 被使用,但其定义点 @%s 不支配它"
                                    % (fmt(v), n, defs[v]))
        for phi in b.phis:
            for k, v in enumerate(phi.ins):
                if v is None or is_const(v):
                    continue
                pred = b.preds[k]
                if v in defs and defs[v] not in set(func.dom_chain(pred)):
                    problems.append("phi @%s 的第 %d 条入边 %s 的定义点 @%s 不支配前驱 @%s"
                                    % (n, k, fmt(v), defs[v], pred))
    return problems


def dump(func, title=""):
    print("  %s%s" % ("## " if title else "", title))
    for n in func.order:
        b = func.blocks[n]
        print("    %-4s (pred=%s succ=%s)" % (n, ",".join(b.preds) or "-",
                                              ",".join(b.succ) or "-"))
        for op in b.all_ops():
            print("        %s" % op.text())
