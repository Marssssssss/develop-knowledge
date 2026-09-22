"""trait 高级形态：关联类型 / supertrait / GAT / dyn 兼容（原 object safety）。

依据三处官方原文：

1. **The Book ch20-02 Advanced Traits**：关联类型与泛型参数的分工——
   泛型参数允许「同一类型实现同一 trait 多次」，关联类型只允许一次，
   因此用关联类型时调用方不必到处标注类型。
2. **Rust Reference `items/associated-items`**：关联类型**可以**带泛型参数与
   where 子句，即 *generic associated types (GATs)*，写法
   `<Thing as Trait>::Item<'x>`。
3. **Rust Reference `items/traits`（Dyn compatibility）**：能作为 trait object
   基 trait 的条件清单（原名 object safety）。
"""

from __future__ import annotations

# 可分派的接收者类型（Reference 的清单）
DISPATCHABLE_RECEIVERS = {
    "&self", "&mut self", "Box<Self>", "Rc<Self>", "Arc<Self>",
    "Pin<&Self>", "Pin<Box<Self>>", "Pin<Rc<Self>>", "Pin<Arc<Self>>",
    "&Self+liftime",
}

# 官方点名「不是 dyn 兼容」的异步闭包三 trait
ASYNC_FN_TRAITS = {"AsyncFn", "AsyncFnMut", "AsyncFnOnce"}


class Item:
    def __init__(self, kind, name, generics=(), lifetimes=(), self_kind=None,
                 uses_self_other=False, is_async=False, rpit=False,
                 where_sized=False, c_variadic=False):
        self.kind = kind              # const / type / fn
        self.name = name
        self.generics = list(generics)     # 类型参数（GAT 的关键）
        self.lifetimes = list(lifetimes)
        self.self_kind = self_kind
        self.uses_self_other = uses_self_other
        self.is_async = is_async
        self.rpit = rpit
        self.where_sized = where_sized
        self.c_variadic = c_variadic


class Trait:
    def __init__(self, name, params=(), supertraits=(), items=()):
        self.name = name
        self.params = list(params)
        self.supertraits = list(supertraits)
        self.items = list(items)


class Registry:
    def __init__(self):
        self.traits = {}

    def add(self, trait):
        self.traits[trait.name] = trait
        return trait

    def supertrait_cycle(self, name, seen=None):
        """官方：'It is an error for a trait to be its own supertrait.'"""
        seen = list(seen or [])
        if name in seen:
            return True
        seen.append(name)
        tr = self.traits.get(name)
        if tr is None:
            return False
        return any(self.supertrait_cycle(s, seen) for s in tr.supertraits)

    def closure_of(self, name, acc=None):
        """supertrait 是传递的：bound 一个 trait 就拿到它全部祖先的关联项。"""
        acc = acc if acc is not None else []
        tr = self.traits.get(name)
        if tr is None or name in acc:
            return acc
        acc.append(name)
        for s in tr.supertraits:
            self.closure_of(s, acc)
        return acc

    def items_in_scope(self, name):
        out = []
        for t in self.closure_of(name):
            tr = self.traits.get(t)
            if tr:
                out.extend(tr.items)
        return out


def _check_fn(it):
    """返回 None 表示该方法合法（可分派或显式不可分派），否则给出原因。"""
    # 显式不可分派：有 `where Self: Sized`；按 Self 值接收（self）也隐含 Sized
    if it.where_sized or it.self_kind == "self":
        return None
    if it.name in ASYNC_FN_TRAITS:
        return "AsyncFn* 不是 dyn 兼容"
    if it.generics:
        return "方法带类型参数且没有 where Self: Sized"
    if it.uses_self_other:
        return "Self 出现在接收者类型以外"
    if it.self_kind not in DISPATCHABLE_RECEIVERS:
        return "接收者类型 %r 不可分派" % (it.self_kind,)
    if it.is_async:
        return "async fn（隐藏了 Future 类型）"
    if it.rpit:
        return "返回位置 impl Trait"
    if it.c_variadic:
        return "C 可变参数"
    return None


def dyn_compatible(reg, name, _stack=()):
    """按 Reference 的清单判定 trait 能否做 trait object 的基 trait。"""
    if name in ASYNC_FN_TRAITS:
        return False, ["AsyncFn* 不是 dyn 兼容"]
    tr = reg.traits.get(name)
    if tr is None:
        return False, ["unknown trait " + name]
    reasons = []
    for s in tr.supertraits:
        if s == "Sized":
            reasons.append("Sized 不能是 supertrait")
        elif s in ASYNC_FN_TRAITS:
            reasons.append("supertrait %s 不是 dyn 兼容" % s)
        else:
            ok, sub = dyn_compatible(reg, s, _stack + (name,))
            if not ok:
                reasons.append("supertrait %s 不是 dyn 兼容：%s" % (s, sub[0]))
    for it in tr.items:
        if it.kind == "const":
            reasons.append("有关联常量 %s" % it.name)
        if it.kind == "type" and (it.generics or it.lifetimes):
            reasons.append("关联类型 %s 带泛型参数（GAT）" % it.name)
        if it.kind == "fn":
            why = _check_fn(it)
            if why:
                reasons.append("%s：%s" % (it.name, why))
    return (not reasons), reasons


def dyn_object_name(reg, name, given_params=None, given_assoc=None):
    """构造 `dyn Trait<..>` 时，泛型参数与关联类型都必须写全。"""
    given_params = given_params or {}
    given_assoc = given_assoc or {}
    tr = reg.traits.get(name)
    missing = [p for p in tr.params if p not in given_params]
    missing += ["%s=%s" % (it.name, "?") for it in tr.items
                if it.kind == "type" and it.name not in given_assoc]
    return missing


class Impl:
    def __init__(self, trait, self_ty, args=(), assoc=None):
        self.trait = trait
        self.self_ty = self_ty
        self.args = tuple(args)
        self.assoc = dict(assoc or {})


def conflicting(impls, trait, self_ty, args):
    """E0119：同一 trait 对同一类型只能有一份实现（关联类型版）。

    泛型参数版的 trait 可以对同一类型实现多次（换参数即可），
    所以这里只在参数也相同时才算冲突。
    """
    for im in impls:
        if im.trait == trait and im.self_ty == self_ty and im.args == tuple(args):
            return True
    return False


def needs_annotation(impls, trait, self_ty):
    """调用方是否需要显式标注：同一类型上有多份实现就说不清用的是哪份。"""
    return [im for im in impls if im.trait == trait and im.self_ty == self_ty]
