#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go 编译器内联预算模型（常量取自 cmd/compile/internal/inline/inl.go）。

被 python/main.py 以 `from inline_budget import *` 使用。
"""
BUDGET = 80                  # inlineMaxBudget
COST_CALL = 57               # inlineExtraCallCost
COST_PARAM_CALL = 17         # inlineParamCallCost
COST_PANIC = 1               # inlineExtraPanicCost
COST_THROW = BUDGET          # inlineExtraThrowCost
BIG_FUNC_NODES = 5000        # inlineBigFunctionNodes
BIG_FUNC_MAX_COST = 20       # inlineBigFunctionMaxCost
CLOSURE_CALLED_ONCE = 10 * BUDGET   # inlineClosureCalledOnceCost
HOT_BUDGET = 2000            # inlineHotMaxBudget（PGO 热函数）
# ---------------------------------------------------------------- 内联代价模型
# 与 hairyVisitor.doNode 同口径：预算从 80 开始递减，减到负数即 tooHairy。
BLOCKERS = ("closure", "defer", "recover", "go", "select", "range_func",
            "go:noinline", "go:uintptrescapes", "no_body", "unhandled_call")


def inline_cost(nodes=1, calls=0, param_calls=0, panics=0, throws=0, blockers=()):
    """返回 (cost, reason)。cost 越小越容易内联；reason 非空表示硬性不可内联。"""
    for b in blockers:
        if b in BLOCKERS:
            return None, b
    cost = nodes + calls * COST_CALL + param_calls * COST_PARAM_CALL \
        + panics * COST_PANIC + throws * COST_THROW
    return cost, ""


def can_inline(nodes=1, calls=0, param_calls=0, panics=0, throws=0, blockers=(),
               is_leaf=True, debug_l=1, caller_nodes=0, closure_called_once=False):
    """复刻 CanInline 的两道闸：预算闸 + 结构闸（叶子要求 / 大函数闸）。"""
    cost, reason = inline_cost(nodes, calls, param_calls, panics, throws, blockers)
    if reason:
        return False, "%s（不是预算问题，结构上就不可内联）" % reason
    if debug_l == 0:
        return False, "-l=0 完全关闭内联"
    if debug_l == 1 and not is_leaf:
        return False, "-l=1（默认）只内联叶子函数"
    limit = BUDGET
    if caller_nodes >= BIG_FUNC_NODES:
        limit = BIG_FUNC_MAX_COST           # inlineBigFunctionMaxCost
    if closure_called_once:
        limit = CLOSURE_CALLED_ONCE         # 闭包只被调用一次，预算放宽到 800
    if cost > limit:
        return False, "function too complex: cost %d exceeds budget %d" % (cost, limit)
    return True, "cost %d <= budget %d" % (cost, limit)
