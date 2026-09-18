"""Result / `?` / From 转换 / Box<dyn Error> / source 链 的可运行模型。

依据：
  * Rust Reference — try propagation operator：可作用于 Result<T,E>、Option<T>、
    ControlFlow<B,C>、Poll<Result<T,E>>、Poll<Option<Result<T,E>>>；
    去糖为 `Try::branch` + `FromResidual::from_residual`；`Try` trait 目前 unstable。
  * The Book ch09-02：`?` 走的 error 会经过 `From::from`；`Box<dyn Error>` = "any kind of
    error"；`main` 返回 `Result<(), E>` 时 Ok→0、Err→非 0；生产代码更倾向 expect 而非 unwrap。
  * std::error::Error：`pub trait Error: Debug + Display`；`source()` 用于跨抽象边界；
    "the underlying error should be either returned by ... source(), or rendered by
    ... Display ..., **but not both**"。
  * thiserror：derive 生成 Display，`#[from]` 生成 From 且**隐含** `#[source]`，
    `#[error(transparent)]` 把 source 与 Display 一并转发。
  * anyhow：`anyhow::Error` 是 trait object；`?` 可传播任何实现 `std::error::Error` 的
    错误；`.context()` 追加上下文；支持 downcast；Rust ≥1.65 自带 backtrace。
"""

# ---------------------------------------------------------------- 值构造

def ok(v):
    return ("Ok", v)


def err(e):
    return ("Err", e)


def some(v):
    return ("Some", v)


NONE = ("None",)


def cont(c):
    return ("Continue", c)


def brk(b):
    return ("Break", b)


def pending():
    return ("Pending",)


def ready(v):
    return ("Ready", v)


# ---------------------------------------------------------------- ? 传播

YIELD = "yield"       # 表达式求值出值，继续往下走
RETURN = "return"     # 提前从整个函数返回


def q_result(r, convert):
    """Result<T, E>：Ok(val) → val；Err(e) → return Err(From::from(e))。"""
    if r[0] == "Ok":
        return (YIELD, r[1])
    return (RETURN, ("Err", convert(r[1])))


def q_option(o, convert):
    """Option<T>：Some(val) → val；None → return None（**不经过 From**）。"""
    if o[0] == "Some":
        return (YIELD, o[1])
    return (RETURN, NONE)


def q_control_flow(cf, convert):
    """ControlFlow<B, C>：Continue(c) → c；Break(b) → return Break(b)（不经 From）。"""
    if cf[0] == "Continue":
        return (YIELD, cf[1])
    return (RETURN, brk(cf[1]))


def q_poll_result(p, convert):
    """Poll<Result<T, E>>：Ready(Ok(v)) → v；Ready(Err(e)) → return Ready(Err(From::from(e)))；
    Pending → return Pending。"""
    if p[0] == "Pending":
        return (RETURN, pending())
    inner = p[1]
    if inner[0] == "Ok":
        return (YIELD, inner[1])
    return (RETURN, ready(err(convert(inner[1]))))


# ---------------------------------------------------------------- 错误类型

class ErrorBase:
    """`std::error::Error` 的最小形态：必须有 Debug + Display，可选 source()。"""

    def display(self):
        raise NotImplementedError

    def debug(self):
        return f"{type(self).__name__} {{ .. }}"

    def source(self):
        return None


class IoError(ErrorBase):
    def __init__(self, kind="NotFound", msg="No such file or directory", code=2):
        self.kind, self.msg, self.code = kind, msg, code

    def display(self):
        return f"{self.msg} (os error {self.code})"     # 官方示例字符串


class ParseIntError(ErrorBase):
    def __init__(self, raw="abc"):
        self.raw = raw

    def display(self):
        return "invalid digit found in string"           # 官方示例字符串


class AppError(ErrorBase):
    """业务层错误：一个变体包一种下层错误（thiserror 的 `#[from]` 就是这个形状）。"""

    def __init__(self, variant, msg, source=None):
        self.variant, self.msg, self._source = variant, msg, source

    def display(self):
        return self.msg

    def source(self):
        return self._source

    def debug(self):
        return f"AppError::{self.variant} {{ msg: {self.msg!r} }}"


class BadAppError(AppError):
    """反例：Display 里把 source 的文本也渲染了一遍（std 明确说不要两者都做）。"""

    def display(self):
        base = super().display()
        src = self.source()
        return f"{base}: {src.display()}" if src is not None else base


class BoxDynError:
    """`Box<dyn Error>` —— 官方读作 "any kind of error"。"""

    def __init__(self, inner):
        if not isinstance(inner, ErrorBase):
            raise TypeError(
                f"E0277: `{type(inner).__name__}` doesn't implement `std::error::Error`")
        self.inner = inner

    def display(self):
        return self.inner.display()

    def source(self):
        return self.inner.source()

    def downcast_ref(self, cls):
        """anyhow 文档：downcasting 支持 by value / shared ref / mut ref。"""
        return self.inner if isinstance(self.inner, cls) else None


# ---------------------------------------------------------------- From 注册表

class FromRegistry:
    def __init__(self):
        self.table = {}          # (src_type_name, dst_name) -> callable
        self.blankets = {}       # dst_name -> callable（如 Box<dyn Error> 的 blanket impl）

    def add(self, src_name, dst_name, fn):
        self.table[(src_name, dst_name)] = fn

    def add_blanket(self, dst_name, fn):
        self.blankets[dst_name] = fn

    def convert(self, e, dst_name):
        src = type(e).__name__
        if (src, dst_name) in self.table:
            return self.table[(src, dst_name)](e)
        if dst_name in self.blankets:
            return self.blankets[dst_name](e)
        raise TypeError(
            f"E0277: the trait bound `{dst_name}: From<{src}>` is not satisfied\n"
            f"  = `?` couldn't convert the error `{src}` to `{dst_name}`")


# ---------------------------------------------------------------- 辅助判据

def error_chain(e):
    """沿 source() 走到底（anyhow 打印的 "Caused by:" 链就是这个）。"""
    out, node, guard = [], e, 0
    while node is not None and guard < 100:
        guard += 1
        out.append(node)
        node = node.source()
    return out


def obeys_not_both_rule(e):
    """std 原文：底层错误要么由 source() 返回，要么被 Display 渲染，**不能两者都做**。"""
    s = e.source()
    if s is None:
        return True
    return s.display() not in e.display()


def exit_code(res):
    """`main() -> Result<(), E>`：Ok → 0，Err → 非 0（官方：兼容 C 的退出码约定）。"""
    return 0 if res[0] == "Ok" else 1


def unwrap(res):
    if res[0] == "Ok":
        return res[1]
    raise RuntimeError(f"called `Result::unwrap()` on an `Err` value: {res[1].debug()}")


def expect(res, msg):
    if res[0] == "Ok":
        return res[1]
    raise RuntimeError(f"{msg}: {res[1].debug()}")
