"""Rust 型变（variance）与 PhantomData 的可执行模型。

依据两处官方原文：

1. **Rust Reference `subtyping`**：
   - 子类型化只发生在「生命周期」和「高阶生命周期」两处；
     若把生命周期擦掉，剩下的只有**类型相等**带来的子类型关系。
   - 内置类型的型变表（`&'a T`、`&'a mut T`、`*const T`、`*mut T`、`[T]`/`[T;n]`、
     `fn() -> T`、`fn(T) -> ()`、`UnsafeCell<T>`、`PhantomData<T>`、`dyn Trait<T> + 'a`）。
   - struct/enum/union 的型变由**字段类型**决定：同一参数出现在不同型变的位置
     → 该参数 **invariant**。
   - 但**不在** struct 里时（如元组、裸函数指针类型），
     每个位置**各自**计算型变（"the variance at these positions is computed separately"）。
2. **Rustonomicon `phantom-data`**：`PhantomData` 九种写法的型变 / Send+Sync /
   drop glue 对照表，以及 `#[may_dangle]` 与「owns `T`」的关系。
"""

from __future__ import annotations

COV, CONTRA, INV, BIV = 1, -1, 0, 2      # BIV = 未被使用（bivariant）
NAME = {COV: "covariant", CONTRA: "contravariant", INV: "invariant", BIV: "bivariant"}


def join(a, b):
    """同一参数在多个位置出现时的合并：一致则保留，冲突则 invariant。"""
    if a == BIV:
        return b
    if b == BIV:
        return a
    return a if a == b else INV


def compose(shape_v, arg_v):
    """外层型变 ∘ 内层型变。BIV 表示这一支里根本没出现该参数。"""
    if arg_v == BIV:
        return BIV
    return shape_v * arg_v


# 内置容器各位置的型变（Reference 的表格）
SHAPES = {
    "ref": [("life", COV), ("ty", COV)],          # &'a T
    "refmut": [("life", COV), ("ty", INV)],       # &'a mut T
    "ptr_const": [("ty", COV)],                   # *const T
    "ptr_mut": [("ty", INV)],                     # *mut T
    "slice": [("ty", COV)],                       # [T]
    "array": [("ty", COV), ("n", BIV)],           # [T; n]
    "unsafe_cell": [("ty", INV)],                 # UnsafeCell<T>
    "cell": [("ty", INV)],                        # Cell<T>
    "phantom": [("ty", COV)],                     # PhantomData<T>
    "fnret": [("ty", COV)],                       # fn() -> T
    "fnarg": [("ty", CONTRA)],                    # fn(T) -> ()
    "unit": [],                                   # ()
}


def tuple_variance(t, param):
    """把元组当成一个「整体类型」算型变 —— 用于和官方的「逐位置」规则做对比。

    Rust **不**这样做：元组不是 struct，型变在每个位置上单独计算。
    """
    acc = BIV
    for x in t[1]:
        acc = join(acc, variance_of(x, param))
    return acc

# 生命周期的 outlives 偏序（值越大活得越久）
RANK = {"'static": 100, "'long": 3, "'middle": 2, "'short": 1, "'a": 0, "'b": 0,
        "'c": 0, "'x": 0, "'s": 0}


def outlives(a, b):
    return RANK.get(a, 0) >= RANK.get(b, 0)


class Struct:
    """用户自定义复合类型：型变由字段推导（官方规则）。"""

    def __init__(self, name, params, fields):
        self.name = name
        self.params = list(params)
        self.fields = list(fields)


STRUCTS = {}


def declare(struct):
    STRUCTS[struct.name] = struct
    return struct


def variance_of(t, param):
    """计算类型 `t` 关于参数 `param`（生命周期名或类型参数名）的型变。"""
    if t[0] == "param":
        return COV if t[1] == param else BIV
    if t[0] == "named":
        sd = STRUCTS.get(t[1])
        if sd is None:
            return BIV
        acc = BIV
        for f in sd.fields:
            acc = join(acc, variance_of(f, param))
        return acc
    if t[0] == "fn":
        _, params, ret = t
        acc = compose(COV, variance_of(ret, param))
        for a in params:
            acc = join(acc, compose(CONTRA, variance_of(a, param)))
        return acc
    if t[0] == "dyn":
        _, _trait, life, tyargs = t
        acc = compose(COV, variance_of(("param", life), param))
        for a in tyargs:
            acc = join(acc, compose(INV, variance_of(a, param)))
        return acc
    shape = SHAPES[t[0]]
    acc = BIV
    for i, (_pos, sv) in enumerate(shape):
        arg = t[i + 1]
        if isinstance(arg, tuple):
            acc = join(acc, compose(sv, variance_of(arg, param)))
        else:  # 生命周期 / 长度常量
            acc = join(acc, compose(sv, COV if arg == param else BIV))
    return acc


# ------------------------------------------------------------------ 子类型判定
def equal_types(a, b):
    return a == b


def is_subtype(a, b):
    """按 Reference 的规则判定 `a <: b`（只考虑生命周期带来的子类型化）。"""
    if a[0] != b[0]:
        return False
    if a[0] == "param":
        return a[1] == b[1]
    if a[0] == "named":
        return all(is_subtype(x, y) for x, y in zip(a[2], b[2])) if a[1] == b[1] else False
    if a[0] == "ref":
        return outlives(a[1], b[1]) and is_subtype(a[2], b[2])
    if a[0] == "refmut":
        # &'a mut T 在 T 上 invariant
        return outlives(a[1], b[1]) and equal_types(a[2], b[2])
    if a[0] == "ptr_const":
        return is_subtype(a[1], b[1])
    if a[0] == "ptr_mut":
        return equal_types(a[1], b[1])
    if a[0] == "tuple":
        # 不在 struct 里时，每个位置各自算型变
        return len(a[1]) == len(b[1]) and all(
            is_subtype(x, y) for x, y in zip(a[1], b[1]))
    if a[0] == "fn":
        if len(a[1]) != len(b[1]):
            return False
        return (all(is_subtype(y, x) for x, y in zip(a[1], b[1]))  # 参数逆变
                and is_subtype(a[2], b[2]))                        # 返回协变
    if a[0] == "phantom":
        return is_subtype(a[1], b[1])
    return equal_types(a, b)


def instantiate(forall_ty, life):
    """把 `for<'a> T` 里的 'a 替换成具体生命周期。"""
    _, var, body = forall_ty
    return _subst(body, var, life)


def _subst(t, var, life):
    if t[0] == "param":
        return t
    if t[0] in ("ref", "refmut"):
        return (t[0], life if t[1] == var else t[1], _subst(t[2], var, life))
    if t[0] == "forall":
        return (t[0], t[1], _subst(t[2], var, life)) if t[1] != var else t
    if t[0] == "fn":
        return ("fn", tuple(_subst(x, var, life) for x in t[1]),
                _subst(t[2], var, life))
    if t[0] == "tuple":
        return ("tuple", tuple(_subst(x, var, life) for x in t[1]))
    if t[0] == "named":
        return ("named", t[1], tuple(_subst(x, var, life) for x in t[2]))
    return t


def hrtb_subtype(src, dst):
    """`for<'a…> F` 与另一个类型的子类型关系：用目标的生命周期实例化源。"""
    if src[0] == "forall":
        target = dst[1] if dst[0] == "forall" else "'static"
        src = instantiate(src, target)
    if dst[0] == "forall":
        return False            # 具体函数指针不是 for<'a> 函数的子类型（方向不可逆）
    return is_subtype(src, dst)


# ------------------------------------------------- PhantomData 对照表（nomicon）
# (variance of 'a, variance of T, send_sync, dangling_allowed)
PHANTOM_TABLE = {
    "PhantomData<T>": (BIV, COV, "inherited", False),
    "PhantomData<&'a T>": (COV, COV, "Send+Sync if T: Sync", True),
    "PhantomData<&'a mut T>": (COV, INV, "inherited", True),
    "PhantomData<*const T>": (BIV, COV, "!Send + !Sync", True),
    "PhantomData<*mut T>": (BIV, INV, "!Send + !Sync", True),
    "PhantomData<fn(T)>": (BIV, CONTRA, "Send + Sync", True),
    "PhantomData<fn() -> T>": (BIV, COV, "Send + Sync", True),
    "PhantomData<fn(T) -> T>": (BIV, INV, "Send + Sync", True),
    "PhantomData<Cell<&'a ()>>": (INV, BIV, "Send + !Sync", True),
}


def phantom_variance(kind, param):
    """用上面的型变代数**算**出 PhantomData 各种写法的型变（不查表）。"""
    inner = {
        "PhantomData<T>": ("param", "T"),
        "PhantomData<&'a T>": ("ref", "'a", ("param", "T")),
        "PhantomData<&'a mut T>": ("refmut", "'a", ("param", "T")),
        "PhantomData<*const T>": ("ptr_const", ("param", "T")),
        "PhantomData<*mut T>": ("ptr_mut", ("param", "T")),
        "PhantomData<fn(T)>": ("fn", (("param", "T"),), ("unit",)),
        "PhantomData<fn() -> T>": ("fn", (), ("param", "T")),
        "PhantomData<fn(T) -> T>": ("fn", (("param", "T"),), ("param", "T")),
        "PhantomData<Cell<&'a ()>>": ("cell", ("ref", "'a", ("unit",))),
    }[kind]
    return variance_of(("phantom", inner), param)
