"""Rust 方法调用解析模型（依据 Rust Reference `expressions/method-call-expr`）。

官方原文的两段核心规则（本模块逐条落地）：

1. **候选接收者列表** = 反复解引用接收者类型、把遇到的每个类型加入列表，
   最后再尝试一次数组未定长强制转换（unsized coercion）并把结果加入；
   然后「对每个候选 `T`，把 `&T` 与 `&mut T` 紧跟在 `T` 之后加入列表」。
   官方给的例子是 `Box<[i32;2]>` → 9 个候选，顺序固定。
2. **候选搜索** = 按候选列表顺序逐个类型找：先 `T` 的固有方法（inherent），
   再看 `T` 实现的可见 trait 提供的方法（类型参数时先查 bound 里的 trait）。
   官方明确「这个过程**不考虑**接收者的可变性 / 生命周期 / 是否 unsafe」，
   选定之后若因为上述原因调不了，才是编译错误。
"""

from __future__ import annotations

import re

ARRAY_RE = re.compile(r"^\[.+;\d+\]$")

# 接收者形态：声明为 self / &self / &mut self 时，真正参与候选比较的类型
def receiver_type(owner_ty: str, kind: str) -> str:
    if kind == "self":
        return owner_ty
    if kind == "&self":
        return "&" + owner_ty
    if kind == "&mut self":
        return "&mut " + owner_ty
    raise ValueError("unknown receiver kind: " + kind)


class TypeEnv:
    """解引用与未定长强制转换的规则表（真实 Rust 里由 Deref / Unsize 决定）。"""

    def __init__(self, deref=None, unsize=None):
        self.deref = dict(deref or {})
        self.unsize = dict(unsize or {})

    def deref_step(self, ty):
        return self.deref.get(ty)

    def unsize_step(self, ty):
        return self.unsize.get(ty)


def std_env() -> TypeEnv:
    return TypeEnv(
        deref={"Box<[i32;2]>": "[i32;2]", "Box<String>": "String", "String": "str"},
        unsize={"[i32;2]": "[i32]", "[i32;3]": "[i32]", "[&str;2]": "[&str]"},
    )


def candidate_types(ty: str, env: TypeEnv):
    """第一步：反复解引用，最后尝试未定长强制转换（不含自动借用）。"""
    steps = []
    cur = ty
    while True:
        steps.append(cur)
        nxt = env.deref_step(cur)
        if nxt is not None:
            cur = nxt
            continue
        unsized = env.unsize_step(cur)
        if unsized is not None:
            steps.append(unsized)
        break
    return steps


def candidate_receivers(ty: str, env: TypeEnv):
    """第二步：在每个 `T` 之后紧跟 `&T` 与 `&mut T`（官方例子顺序）。"""
    out = []
    for t in candidate_types(ty, env):
        out.append(t)
        out.append("&" + t)
        out.append("&mut " + t)
    return out


class Method:
    def __init__(self, name, owner, kind, item=None):
        self.name = name
        self.owner = owner          # "inherent" 或 "Trait"
        self.kind = kind            # self / &self / &mut self
        self.item = item            # 仅供 demo 展示返回元素类型

    @property
    def is_inherent(self):
        return self.owner == "inherent"

    def __repr__(self):
        return "%s::%s(%s)" % (self.owner, self.name, self.kind)


class MethodTable:
    """按「接收者类型」索引的方法表：key 已经是 `T` / `&T` / `&mut T`。"""

    def __init__(self):
        self.by_receiver = {}

    def add(self, owner_ty, method):
        self.by_receiver.setdefault(receiver_type(owner_ty, method.kind), []).append(method)

    def at(self, receiver_ty, name):
        return [m for m in self.by_receiver.get(receiver_ty, []) if m.name == name]


class Ambiguity(Exception):
    def __init__(self, code, detail):
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail


class Resolution:
    def __init__(self, index, candidate, method):
        self.index = index
        self.candidate = candidate
        self.method = method

    def __repr__(self):
        return "#%d %s -> %r" % (self.index, self.candidate, self.method)


def _visible(cands, methods, name, bound_traits, edition, index):
    """在单个候选类型上取可见方法；返回命中列表（已按固有 / bound / 其余排序）。"""
    hits = methods.at(cands, name)
    if edition < 2021 and ARRAY_RE.match(cands):
        hits = [m for m in hits if m.owner != "IntoIterator"]
    if not hits:
        return []
    # 三级优先：固有方法 > bound 里的 trait > 作用域内其余 trait。
    # 只有「同一级里出现多个」才是 E0034；跨级时高一级直接胜出，不算歧义。
    inherent = [m for m in hits if m.is_inherent]
    if inherent:
        return inherent
    in_bound = [m for m in hits if m.owner in bound_traits]
    if in_bound:
        return in_bound
    return hits


def resolve(name, receiver_ty, env, methods, bound_traits=(), edition=2021,
            trait_object=False):
    """按 Reference 的两步流程解析一次方法调用。"""
    cands = candidate_receivers(receiver_ty, env)
    for i, c in enumerate(cands):
        hits = _visible(c, methods, name, bound_traits, edition, i)
        if not hits:
            continue
        if len(hits) > 1:
            raise Ambiguity("E0034", "multiple applicable items in scope at %s: %r"
                            % (c, hits))
        m = hits[0]
        base = c[len("&mut "):] if c.startswith("&mut ") else (
            c[1:] if c.startswith("&") else c)
        if trait_object and base.startswith("dyn "):
            clash = [x for x in methods.at(c, name)
                     if (x.is_inherent) != (m.is_inherent)]
            if clash:
                raise Ambiguity(
                    "E0034", "trait object `%s` has both an inherent and a trait "
                    "method named `%s`; use disambiguating syntax" % (c, name))
        return Resolution(i, c, m)
    return None


def apply_call(res, place_is_mut, receiver_is_unsafe=False):
    """选定之后再检查可变性 / unsafe —— 官方原文：这些**不参与**查找。"""
    if res is None:
        return "E0599"          # no method named `x` found
    need_mut = res.method.kind == "&mut self"
    if need_mut and not place_is_mut:
        return "E0596"          # cannot borrow as mutable
    if receiver_is_unsafe:
        return "E0133"          # call to unsafe function
    return None


def disambiguate(name, trait, receiver_ty, env, methods):
    """完全限定语法 `<T as Trait>::f` 绕过候选搜索直接命中。

    官方 WARNING：对 trait object，该语法**只**命中 trait 方法，
    「there is no way to call the inherent method」——故这里对 `dyn` 上的
    固有方法返回 None，把「调不到」这件事显式建模出来。
    """
    for c in candidate_receivers(receiver_ty, env):
        base = c[len("&mut "):] if c.startswith("&mut ") else (
            c[1:] if c.startswith("&") else c)
        for m in methods.at(c, name):
            if base.startswith("dyn ") and trait == "inherent":
                return None
            if m.owner == trait:
                return Resolution(-1, c, m)
    return None
