"""unsafe Rust 边界的可运行模型：五大 superpower、裸指针、UnsafeCell、transmute、safe 抽象。

依据：
  * The Book ch20-01 —— 五件只有 unsafe 能做的事（unsafe superpowers）；
    "unsafe **doesn't turn off the borrow checker** or disable any of Rust's other
    safety checks"；官方建议 "Keep `unsafe` blocks small" 并把它包进 safe abstraction；
    `split_at_mut` 作为标准库「安全封装不安全代码」的范例。
  * std::cell::UnsafeCell —— "The core primitive for interior mutability in Rust"；
    只解除 `&T` 的不可变保证，**不**解除 `&mut T` 的唯一性保证
    （"There is no legal way to obtain aliasing `&mut`, not even with `UnsafeCell<T>`"）；
    "UnsafeCell does nothing to avoid data races"；`.get()` 给出 `*mut T`。
  * Rustonomicon — Transmutes —— `transmute` 只检查**尺寸相同**；
    "Transmuting an `&` to `&mut` is **always** Undefined Behavior"；
    "Do not transmute 3 to bool"；`repr(C)` / `repr(transparent)` 布局有定义，
    `repr(Rust)` **没有**（"Even different instances of the same generic type can
    have wildly different layout"）；转出未标注生命周期的引用会得到 unbounded lifetime。
  * std::pin —— pinning 用于自引用类型（unsafe 代码的又一类正当用途）。
"""


class UB(Exception):
    """未定义行为：编译器**不保证**能发现，本模型只是把它显式化以便断言。"""


class CompileError(Exception):
    """编译期错误：rustc 一定会拦下来。"""


# ---------------------------------------------------------------- 五大 superpower

DEREF_RAW = "dereference a raw pointer"
CALL_UNSAFE_FN = "call an unsafe function or method"
MUT_STATIC = "access or modify a mutable static variable"
IMPL_UNSAFE_TRAIT = "implement an unsafe trait"
UNION_FIELD = "access fields of unions"

SUPERPOWERS = {DEREF_RAW, CALL_UNSAFE_FN, MUT_STATIC, IMPL_UNSAFE_TRAIT, UNION_FIELD}


def perform(op, in_unsafe):
    """只有 unsafe 块里才能做这五件事。"""
    if op in SUPERPOWERS and not in_unsafe:
        raise CompileError(f"error[E0133]: {op} requires unsafe function or block")
    return True


# ---------------------------------------------------------------- 借用检查器仍然开着

class Ref:
    """普通引用 `&T` / `&mut T`。"""

    def __init__(self, value, mutable=False):
        self.value = value
        self.mutable = mutable

    def read(self):
        return self.value

    def write(self, v):
        if not self.mutable:
            # 官方："unsafe doesn't turn off the borrow checker"
            raise CompileError("error[E0596]: cannot borrow data as mutable, "
                               "as it is behind a `&` reference")
        self.value = v


# ---------------------------------------------------------------- 裸指针

class RawPtr:
    """`*const T` / `*mut T`：可以在**安全代码**里创建，但解引用必须 unsafe。"""

    def __init__(self, addr, target=None, mutable=False):
        self.addr = addr
        self.target = target          # None 表示空指针或悬垂
        self.mutable = mutable
        self.null = target is None and addr == 0

    def deref(self, in_unsafe):
        if not in_unsafe:
            raise CompileError("error[E0133]: dereference of raw pointer requires unsafe")
        if self.null or self.target is None:
            raise UB("null / dangling pointer dereference")
        return self.target.value if hasattr(self.target, "value") else self.target

    def write(self, v, in_unsafe):
        if not in_unsafe:
            raise CompileError("error[E0133]: dereference of raw pointer requires unsafe")
        if not self.mutable:
            raise CompileError("error[E0594]: cannot assign to `*const T`")
        if self.null or self.target is None:
            raise UB("null / dangling pointer write")
        self.target.value = v


def create_raw(r, mutable=False):
    """官方：创建裸指针是安全的（We can create raw pointers in safe code）——
    只有解引用不安全。"""
    return RawPtr(id(r), target=r, mutable=mutable)


# ---------------------------------------------------------------- UnsafeCell

class Cell:
    """`T` 或 `UnsafeCell<T>` 的最小模型。

    interior=True 表示被 UnsafeCell 包住：解除 `&T` 的不可变保证。
    """

    def __init__(self, value, interior=False):
        self.value = value
        self.interior = interior

    def get(self):
        """UnsafeCell::get() -> *mut T：这是内部可变性的唯一入口。"""
        return RawPtr(id(self), target=self, mutable=True)

    def write_through_shared(self, v, in_unsafe):
        """通过 `&self`（共享引用）改写内容。"""
        if not in_unsafe:
            raise CompileError("error[E0133]: dereference of raw pointer requires unsafe")
        if not self.interior:
            # 官方：&T 指向不可变数据，改它就是 UB
            raise UB("mutating through a `&T` that is not behind UnsafeCell is UB")
        self.value = v
        return self.value

    def aliasing_mut(self):
        """官方：唯一性保证不受影响 —— 永远不可能合法地拿到两个交叠的 &mut。"""
        raise UB("no legal way to obtain aliasing `&mut`, not even with UnsafeCell<T>")


# ---------------------------------------------------------------- transmute

def transmute(size_from, size_to, *, layout_defined=True, ref_to_mut=False,
              creates_invalid_value=False, unbounded_lifetime=False):
    """`mem::transmute::<T, U>` 的判定。

    官方：唯一的**编译期**检查是尺寸相同（"The only restriction is that the T and U
    are verified to have the same size"）；其余全是 UB 且编译器不拦。
    """
    if size_from != size_to:
        raise CompileError(
            f"error[E0512]: cannot transmute between types of different sizes "
            f"({size_from} bytes to {size_to} bytes)")
    if ref_to_mut:
        raise UB("transmuting an `&` to `&mut` is **always** Undefined Behavior")
    if creates_invalid_value:
        raise UB("creating an instance of a type with an invalid state is UB "
                 "(nomicon: 'Do not transmute 3 to bool')")
    if unbounded_lifetime:
        raise UB("transmuting to a reference without an explicit lifetime "
                 "produces an unbounded lifetime")
    if not layout_defined:
        raise UB("`repr(Rust)` layout is not guaranteed — even two instances of the "
                 "same generic type may differ")
    return "ok"


# ---------------------------------------------------------------- safe 抽象

def unsafe_split_impl(values, mid):
    """去掉边界检查的裸指针版本：越界就是 UB。"""
    if mid > len(values):
        raise UB("out-of-bounds pointer arithmetic (no bounds check in the unsafe impl)")
    return values[:mid], values[mid:]


def split_at_mut(values, mid):
    """标准库的 `split_at_mut`：安全 API 包住上面的不安全实现。

    官方："Wrapping unsafe code in a safe abstraction prevents uses of `unsafe` from
    leaking out into all the places that you or your users might want to use the
    functionality" —— 关键就是**这个断言**。
    """
    assert mid <= len(values), f"mid index out of bounds: {mid} > {len(values)}"
    return unsafe_split_impl(values, mid)
