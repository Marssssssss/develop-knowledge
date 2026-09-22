#!/usr/bin/env python3
"""OPA ``v1/ast`` 的 ``Unify`` 与 ``outputVarsForExpr``（逐行转写）。

* ``v1/ast/unify.go``   —— ``Unify`` / ``unifier`` / ``isRefSafe``
* ``v1/ast/compile.go`` —— ``outputVarsForExpr`` / ``outputVarsForExprEq`` /
  ``outputVarsForExprCall`` / ``outputVarsForTerms`` / ``outputVarsForBody``

两个极易写反的点：

* ``outputVarsForTerms`` 的结果要与 ``safe`` **取并集**再交给 ``Unify``，
  最后再减掉 ``safe``；不是「取 Unify 结果代替」。
* ``:=`` 会把 LHS 的变量**整体**从安全基里删掉（issue #3546），
  所以 ``input.a[i] == 1`` 产出 ``{i}`` 而 ``input.a[i] := 1`` 产出空集。
"""

from __future__ import annotations

from typing import Any, Iterator, List, Set, Tuple

from terms import Array, CallT, Const, Obj, Ref, SetT, Term, Var, is_ref_safe
from exprs import Bare, Call, Eq, Every, Expr, Logical, Not, With

# Unify（v1/ast/unify.go）
# --------------------------------------------------------------------------


class Unifier:
    def __init__(self, safe: Set[str]):
        self.safe = set(safe)
        self.unified: Set[str] = set()
        self.unknown: dict = {}

    def is_safe(self, v: str) -> bool:
        return v in self.safe or v in self.unified

    def mark_safe(self, v: str) -> None:
        self.unified.add(v)
        for w in list(self.unknown.pop(v, ())):
            self.mark_safe(w)
        for w in list(self.unknown.keys()):
            deps = self.unknown.get(w)
            if deps is None:
                continue
            if v in deps:
                deps.discard(v)
                if not deps:
                    del self.unknown[w]
                    self.mark_safe(w)

    def mark_unknown(self, a: str, b: str) -> None:
        self.unknown.setdefault(a, set()).add(b)

    def mark_all_safe(self, t: Term) -> None:
        for v in t.output_vars():
            self.mark_safe(v)

    def unify_all(self, a: Var, b: Term) -> None:
        if self.is_safe(a.name):
            self.mark_all_safe(b)
            return
        unsafe = b.output_vars() - self.safe - self.unified
        if not unsafe:
            self.mark_safe(a.name)
        else:
            for v in unsafe:
                self.mark_unknown(a.name, v)

    def unify(self, a: Term, b: Term) -> None:
        if isinstance(a, Var):
            if isinstance(b, Var):
                if self.is_safe(b.name):
                    self.mark_safe(a.name)
                elif self.is_safe(a.name):
                    self.mark_safe(b.name)
                else:
                    self.mark_unknown(a.name, b.name)
                    self.mark_unknown(b.name, a.name)
            elif isinstance(b, (Array, Obj)):
                self.unify_all(a, b)
            elif isinstance(b, Ref):
                if is_ref_safe(b, self.safe):
                    self.mark_safe(a.name)
            else:
                self.mark_safe(a.name)          # default: Const / Set / 调用项
        elif isinstance(a, Ref):
            if is_ref_safe(a, self.safe):
                if isinstance(b, Var):
                    self.mark_safe(b.name)
                elif isinstance(b, (Array, Obj)):
                    self.mark_all_safe(b)
        elif isinstance(a, Array):
            if isinstance(b, Var):
                self.unify_all(b, a)
            elif isinstance(b, Array) and len(a.items) == len(b.items):
                for x, y in zip(a.items, b.items):
                    self.unify(x, y)
        elif isinstance(a, Obj):
            if isinstance(b, Var):
                self.unify_all(b, a)
            elif isinstance(b, Obj) and len(a.pairs) == len(b.pairs):
                bm = {_okey(k): v for k, v in b.pairs}
                for k, v in a.pairs:
                    if _okey(k) in bm:
                        self.unify(v, bm[_okey(k)])
        else:                                    # default: Const / Set 在左侧
            if isinstance(b, Var):
                self.mark_safe(b.name)


def _okey(k: Term) -> Tuple[str, Any]:
    """对象键的比较口径：常量键按值，变量键按名字（OPA 用 Term.Get 精确匹配）。"""
    return ("c", k.value) if isinstance(k, Const) else ("t", repr(k))


def unify(a: Term, b: Term, safe: Set[str]) -> Set[str]:
    u = Unifier(safe)
    u.unify(a, b)
    return u.unified


# --------------------------------------------------------------------------
# outputVarsFor*
# --------------------------------------------------------------------------


def walk_terms(t: Term) -> Iterator[Term]:
    yield t
    if isinstance(t, Ref):
        yield from walk_terms(t.head)
        for p in t.parts:
            if isinstance(p, Term):
                yield from walk_terms(p)
    elif isinstance(t, Array):
        for i in t.items:
            yield from walk_terms(i)
    elif isinstance(t, Obj):
        for k, v in t.pairs:
            yield from walk_terms(k)
            yield from walk_terms(v)
    elif isinstance(t, SetT):
        for i in t.items:
            yield from walk_terms(i)
    elif isinstance(t, CallT):
        for i in t.operands:
            yield from walk_terms(i)


def output_vars_for_terms(e: Any, safe: Set[str]) -> Set[str]:
    """outputVarsForTerms：收集「ref-safe 且非 ground」的引用的下标变量。"""
    out: Set[str] = set()
    src = [e] if isinstance(e, Term) else list(e.terms())
    for t in src:
        for x in walk_terms(t):
            if isinstance(x, Ref):
                if not is_ref_safe(x, safe):
                    continue
                if not x.is_ground():
                    out |= x.output_vars()
    return out


def output_vars_for_eq(e: Eq, safe: Set[str]) -> Set[str]:
    out = output_vars_for_terms(e, safe)
    out |= set(safe)
    if e.from_assignment:
        # 剔除 LHS：防止 RHS 反向通过 LHS 获得安全性（issue #3546）
        out -= e.lhs.vars()
    u = Unifier(out)
    u.unify(e.lhs, e.rhs)
    out |= u.unified
    return out - set(safe)


def output_vars_for_call(e: Call, safe: Set[str]) -> Set[str]:
    if e.arity < 0:                      # 未知 arity（源码 ar < 0）
        return set()
    out = output_vars_for_terms(e, safe)
    # numInputTerms = arity + 1（含操作符项）⟺ arity >= len(operands) 时无输出项
    if e.arity >= len(e.operands):
        return out
    unsafe: Set[str] = set()
    for t in e.operands[:e.arity]:
        unsafe |= t.output_vars()
    unsafe -= out
    unsafe -= set(safe)
    if unsafe:
        return set()
    for t in e.operands[e.arity:]:
        out |= t.output_vars()
    return out


def output_vars_for_expr(e: Expr, safe: Set[str]) -> Set[str]:
    """outputVarsForExpr：这条表达式能产出哪些变量。"""
    if isinstance(e, Not):
        return set()                               # IsNegated
    if isinstance(e, With):
        if e.target.vars() - set(safe):            # with 输入必须已安全
            return set()
        return output_vars_for_expr(e.inner, safe)
    if isinstance(e, Bare):
        return output_vars_for_terms(e.term, safe)
    if isinstance(e, Every):
        return output_vars_for_terms(e.domain, safe)
    if isinstance(e, Logical):
        return set()                               # and / or 不贡献绑定
    if isinstance(e, Eq):
        return output_vars_for_eq(e, safe)
    if isinstance(e, Call):
        return output_vars_for_call(e, safe)
    return set()


def output_vars_for_body(body: List[Expr], safe: Set[str]) -> Set[str]:
    """outputVarsForBody：把 safe 逐条喂进去累积，最后再减掉原始 safe。"""
    o = set(safe)
    for e in body:
        o |= output_vars_for_expr(e, o)
    return o - set(safe)
