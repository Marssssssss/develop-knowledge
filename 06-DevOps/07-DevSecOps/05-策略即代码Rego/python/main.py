#!/usr/bin/env python3
"""OPA body 的安全重排（``reorderBodyForSafety``）与编译期校验。

不动点迭代的语义照抄源码注释：

    Expressions are added to the re-ordered body as soon as they are considered
    safe. If multiple expressions become safe in the same pass, they are added
    in their original order. This results in minimal re-ordering of the body.

两个容易漏掉的源码细节：

* ``checkBodySafety`` 在**报错时返回原始 body**，不是重排后的 body
  （``v1/ast/compile.go``: ``if errs := safetyErrorSlice(...); len(errs) > 0 {
  c.err(errs...); return b }``）
* ``safetyErrorSlice`` 会把 ``:=`` 的 LHS 变量压掉，只要同一批里还有
  非 LHS 的 unsafe 变量（``hasNonAssignmentLHS``）。所以 ``n := count(x, m)``
  只报 ``x`` / ``m``，不报 ``n``
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from exprs import Bare, Call, Eq, Every, Expr, Logical, Not, With  # noqa: F401
from terms import Array, Const, Obj, Ref, SetT, Term, Var  # noqa: F401
from safety import output_vars_for_expr  # noqa: F401


def reorder_body_for_safety(globals_: Set[str], body: List[Expr]
                            ) -> Tuple[List[Expr], Dict[int, Set[str]]]:
    """返回 (重排后的 body, 仍然 unsafe 的 {body 下标: 变量集})。"""
    body_vars: Set[str] = set()
    for e in body:
        body_vars |= e.vars()

    safe = body_vars & set(globals_)          # 含 input / data：引用头计入 bodyVars
    unsafe: Dict[int, Set[str]] = {i: set(e.vars() - safe) for i, e in enumerate(body)}

    reordered: List[Expr] = []
    done: Set[int] = set()

    while True:
        n = len(reordered)
        for i, e in enumerate(body):
            if i in done:
                continue
            ovs = output_vars_for_expr(e, safe)
            for v in list(unsafe[i]):
                if v in ovs or v in safe:
                    unsafe[i].discard(v)
            if not unsafe[i]:
                done.add(i)
                reordered.append(e)
                safe |= ovs
        if len(reordered) == n:                # 不动点：这一趟一条都没排进去
            break
        if len(reordered) == len(body):
            break

    return reordered, {i: v for i, v in unsafe.items() if v}


def safety_errors(body: List[Expr], unsafe: Dict[int, Set[str]]) -> List[str]:
    """safetyErrorSlice：报错列表（含 assignmentLHS 压制规则）。"""
    lhs: Set[str] = set()
    for i in unsafe:
        e = body[i]
        if isinstance(e, Eq) and e.from_assignment:
            lhs |= e.lhs.vars()

    pairs = [(i, v) for i in sorted(unsafe) for v in sorted(unsafe[i])]
    has_non = any(v not in lhs for _, v in pairs)

    errs: List[str] = []
    for i, v in pairs:
        if has_non and v in lhs:
            continue
        errs.append("var %s is unsafe" % v)
    if errs:
        return errs
    # unsafe 全是生成变量时，源码改成报「表达式不安全」
    return ["expression is unsafe at index %d" % i for i in sorted(unsafe)]


def check_body_safety(globals_: Set[str], body: List[Expr]) -> Tuple[List[Expr], List[str]]:
    """checkBodySafety：报错时返回原始 body。"""
    ordered, unsafe = reorder_body_for_safety(globals_, body)
    if unsafe:
        return list(body), safety_errors(body, unsafe)
    return ordered, []


def compile_rule(globals_: Set[str], body: List[Expr]) -> Tuple[List[Expr], List[str]]:
    return check_body_safety(globals_, body)


def check_every(globals_: Set[str], ev: Every) -> List[str]:
    """every 体内：key/value 按**已声明**处理（declaredVar），domain 必须已安全。"""
    safe = set(globals_) | set(ev.keys)
    errs: List[str] = []
    for v in sorted(ev.domain.vars() - safe):
        errs.append("var %s is unsafe" % v)
    _, unsafe = reorder_body_for_safety(safe, ev.body)
    for i in sorted(unsafe):
        for v in sorted(unsafe[i]):
            errs.append("var %s is unsafe" % v)
    return errs
