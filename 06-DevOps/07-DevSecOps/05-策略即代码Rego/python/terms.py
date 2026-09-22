#!/usr/bin/env python3
"""OPA ``v1/ast`` 的项（Term）最小模型。

两套变量口径（全篇最容易写反的地方）：

* ``vars()``        —— 含引用头。用于 bodyVars / unsafe 判定
  （``SafetyCheckVisitorParams = {SkipRefCallHead: true, SkipClosures: true}``，
  头只在是函数调用时才跳过）
* ``output_vars()`` —— 跳过**所有层级**的引用头。用于 outputVarsForTerms
  与调用的输入/输出判定（``VarVisitorParams{SkipRefHead: true}``）

其它几个 visitor 开关也各自落地成一个方法：``SkipSets`` -> ``SetT.output_vars``
恒为空；``SkipObjectKeys`` -> ``Obj.output_vars`` 只看值。
"""

from __future__ import annotations

from typing import Any, List, Optional, Set, Tuple

# 项
# --------------------------------------------------------------------------


class Term:
    def vars(self) -> Set[str]:
        raise NotImplementedError

    def output_vars(self) -> Set[str]:
        return self.vars()

    def is_ground(self) -> bool:
        return not self.vars()


class Const(Term):
    def __init__(self, value: Any):
        self.value = value

    def vars(self) -> Set[str]:
        return set()

    def is_ground(self) -> bool:
        return True

    def __repr__(self) -> str:
        return repr(self.value)


class Var(Term):
    def __init__(self, name: str):
        self.name = name

    def vars(self) -> Set[str]:
        return {self.name}

    def __repr__(self) -> str:
        return self.name


class Ref(Term):
    """形如 ``input.a[i]``。head 可以是变量名或任意项（复合头如 ``{1,2}[1]``）。"""

    def __init__(self, head: Any, parts: Optional[List[Any]] = None):
        self.head = Var(head) if isinstance(head, str) else head
        self.parts = list(parts or [])

    def vars(self) -> Set[str]:
        out = set(self.head.vars())
        for p in self.parts:
            if isinstance(p, Term):
                out |= p.vars()
        return out

    def output_vars(self) -> Set[str]:
        out: Set[str] = set()
        for p in self.parts:
            if isinstance(p, Term):
                out |= p.output_vars()
        return out

    def is_ground(self) -> bool:
        # Ref.IsGround: len(ref) < 2 || every(ref[1:], ground)
        return len(self.parts) == 0 or all(_ground(p) for p in self.parts)

    def __repr__(self) -> str:
        s = repr(self.head)
        for p in self.parts:
            s = s + ".%s" % p if isinstance(p, str) else s + "[%r]" % p
        return s


def _ground(p: Any) -> bool:
    return True if isinstance(p, str) else p.is_ground()


class Array(Term):
    def __init__(self, items: List[Term]):
        self.items = list(items)

    def vars(self) -> Set[str]:
        out: Set[str] = set()
        for i in self.items:
            out |= i.vars()
        return out

    def is_ground(self) -> bool:
        return all(i.is_ground() for i in self.items)

    def __repr__(self) -> str:
        return "[%s]" % ", ".join(repr(i) for i in self.items)


class Obj(Term):
    def __init__(self, pairs: List[Tuple[Any, Term]]):
        self.pairs = [(Const(k) if isinstance(k, str) else k, v) for k, v in pairs]

    def vars(self) -> Set[str]:
        out: Set[str] = set()
        for k, v in self.pairs:
            out |= k.vars() | v.vars()
        return out

    def output_vars(self) -> Set[str]:
        out: Set[str] = set()          # SkipObjectKeys
        for _, v in self.pairs:
            out |= v.output_vars()
        return out

    def is_ground(self) -> bool:
        return all(k.is_ground() and v.is_ground() for k, v in self.pairs)

    def __repr__(self) -> str:
        return "{%s}" % ", ".join("%r: %r" % (k, v) for k, v in self.pairs)


class SetT(Term):
    """集合：unsafe 判定计入其变量，产出判定一律跳过（SkipSets）。"""

    def __init__(self, items: List[Term]):
        self.items = list(items)

    def vars(self) -> Set[str]:
        out: Set[str] = set()
        for i in self.items:
            out |= i.vars()
        return out

    def output_vars(self) -> Set[str]:
        return set()

    def __repr__(self) -> str:
        return "{%s}" % ", ".join(repr(i) for i in self.items)


class CallT(Term):
    """作为引用头出现的调用项（``split(z, "")[y]`` 的 ``split(z, "")``）。"""

    def __init__(self, name: str, operands: List[Term]):
        self.name = name
        self.operands = list(operands)

    def vars(self) -> Set[str]:
        out: Set[str] = set()
        for t in self.operands:
            out |= t.vars()
        return out

    def __repr__(self) -> str:
        return "%s(%s)" % (self.name, ", ".join(repr(t) for t in self.operands))


def is_ref_safe(ref: Ref, safe: Set[str]) -> bool:
    """isRefSafe：只看**头**是否安全，下标变量不影响（复合头走 default 分支）。"""
    return ref.head.vars() <= set(safe)


# --------------------------------------------------------------------------
