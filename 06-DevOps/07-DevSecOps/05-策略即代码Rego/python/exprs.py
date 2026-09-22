#!/usr/bin/env python3
"""OPA ``v1/ast`` 的表达式（Expr）最小模型。

``terms()`` 供 ``outputVarsForTerms`` 遍历；``vars()`` 用**含引用头**的口径，
用于 bodyVars / unsafe 判定。
"""

from __future__ import annotations

from typing import Iterator, List, Optional, Set

from terms import Term

# 表达式
# --------------------------------------------------------------------------


class Expr:
    def vars(self) -> Set[str]:
        raise NotImplementedError

    def terms(self) -> Iterator[Term]:
        raise NotImplementedError


class Eq(Expr):
    def __init__(self, lhs: Term, rhs: Term, from_assignment: bool = False):
        self.lhs = lhs
        self.rhs = rhs
        self.from_assignment = from_assignment

    def vars(self) -> Set[str]:
        return self.lhs.vars() | self.rhs.vars()

    def terms(self) -> Iterator[Term]:
        yield self.lhs
        yield self.rhs

    def __repr__(self) -> str:
        return "%r %s %r" % (self.lhs, ":=" if self.from_assignment else "==", self.rhs)


class Call(Expr):
    """内置/函数调用。``operands`` 不含操作符项（源码 terms[0] 是操作符）。"""

    def __init__(self, name: str, operands: List[Term], arity: int):
        self.name = name
        self.operands = list(operands)
        self.arity = arity

    def vars(self) -> Set[str]:
        out: Set[str] = set()
        for t in self.operands:
            out |= t.vars()
        return out

    def terms(self) -> Iterator[Term]:
        yield from self.operands

    def __repr__(self) -> str:
        return "%s(%s)" % (self.name, ", ".join(repr(t) for t in self.operands))


class Not(Expr):
    """取反表达式：``IsNegated`` 直接返回空产出。"""

    def __init__(self, inner: List[Expr]):
        self.inner = list(inner)

    def vars(self) -> Set[str]:
        out: Set[str] = set()
        for e in self.inner:
            out |= e.vars()
        return out

    def terms(self) -> Iterator[Term]:
        for e in self.inner:
            yield from e.terms()

    def __repr__(self) -> str:
        return "not {%s}" % "; ".join(repr(e) for e in self.inner)


class With(Expr):
    """``expr with input as target``。"""

    def __init__(self, inner: Expr, target: Term):
        self.inner = inner
        self.target = target

    def vars(self) -> Set[str]:
        return self.inner.vars() | self.target.vars()

    def terms(self) -> Iterator[Term]:
        yield from self.inner.terms()
        yield self.target

    def __repr__(self) -> str:
        return "%r with input as %r" % (self.inner, self.target)


class Logical(Expr):
    """``and`` / ``or``：不给外围 body 贡献绑定。"""

    def __init__(self, op: str, inner: List[Expr]):
        self.op = op
        self.inner = list(inner)

    def vars(self) -> Set[str]:
        out: Set[str] = set()
        for e in self.inner:
            out |= e.vars()
        return out

    def terms(self) -> Iterator[Term]:
        for e in self.inner:
            yield from e.terms()

    def __repr__(self) -> str:
        return "%s{%s}" % (self.op, "; ".join(repr(e) for e in self.inner))


class Every(Expr):
    """``every k, v in domain { body }``。key/value 是**声明变量**，
    不是 output var（源码 ``rewriteEveryStatement`` 走 declaredVar 路径）。
    """

    def __init__(self, domain: Term, keys: List[str], body: Optional[List[Expr]] = None):
        self.domain = domain
        self.keys = list(keys)
        self.body = list(body or [])

    def vars(self) -> Set[str]:
        out = self.domain.vars() | set(self.keys)
        for e in self.body:
            out |= e.vars()
        return out

    def terms(self) -> Iterator[Term]:
        yield self.domain
        for e in self.body:
            yield from e.terms()

    def __repr__(self) -> str:
        return "every %s in %r" % (",".join(self.keys), self.domain)


class Bare(Expr):
    """裸项表达式（源码 ``case *Term``）。"""

    def __init__(self, term: Term):
        self.term = term

    def vars(self) -> Set[str]:
        return self.term.vars()

    def terms(self) -> Iterator[Term]:
        yield self.term

    def __repr__(self) -> str:
        return repr(self.term)


# --------------------------------------------------------------------------
