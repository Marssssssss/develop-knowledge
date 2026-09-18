"""错误处理自检：`?` 的五种形态、From 转换、Box<dyn Error>、source 链、退出码。"""

from error_model import (
    RETURN, YIELD, AppError, BadAppError, BoxDynError, FromRegistry, IoError,
    ParseIntError, brk, cont, err, error_chain, exit_code, expect,
    obeys_not_both_rule, ok, pending, q_control_flow, q_option, q_poll_result,
    q_result, ready, some, unwrap, NONE,
)


def check(label, cond, detail=""):
    if not cond:
        raise AssertionError(f"{label} FAILED {detail}")


class Spy:
    """记录 From::from 被调用了几次 —— 用来验证「哪些形态的 ? 不走 From」。"""

    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def __call__(self, e):
        self.calls += 1
        return self.fn(e)


def run():
    reg = FromRegistry()
    to_app = Spy(lambda e: AppError(type(e).__name__, f"app: {e.display()}", source=e))
    reg.add("IoError", "AppError", to_app)
    reg.add("ParseIntError", "AppError", to_app)
    reg.add_blanket("Box<dyn Error>", BoxDynError)     # impl<E: Error> From<E> for Box<dyn Error>

    convert = lambda e: reg.convert(e, "AppError")

    # ---- 1. Result 上的 ?（The Book ch09-02）
    y = q_result(ok("hello"), convert)
    check("1.1 Ok 直接求值", y == (YIELD, "hello"), str(y))
    r = q_result(err(IoError()), convert)
    check("1.2 Err 提前返回", r[0] == RETURN and r[1][0] == "Err", str(r))
    check("1.3 Err 走了一次 From::from", to_app.calls == 1, str(to_app.calls))
    check("1.4 转换后是 AppError", isinstance(r[1][1], AppError))
    check("1.5 原始错误被挂到 source()", isinstance(r[1][1].source(), IoError))

    # ---- 2. Option 上的 ? —— 不经过 From
    before = to_app.calls
    check("2.1 Some 求值", q_option(some(7), convert) == (YIELD, 7))
    check("2.2 None 提前返回 None", q_option(NONE, convert) == (RETURN, NONE))
    check("2.3 Option 的 ? 完全不调 From::from", to_app.calls == before,
          f"{before} -> {to_app.calls}")

    # ---- 3. ControlFlow 上的 ? —— 同样不经过 From
    before = to_app.calls
    check("3.1 Continue 求值", q_control_flow(cont(3), convert) == (YIELD, 3))
    check("3.2 Break 提前返回", q_control_flow(brk("found"), convert) == (RETURN, brk("found")))
    check("3.3 ControlFlow 的 ? 不调 From::from", to_app.calls == before)

    # ---- 4. Poll<Result<T,E>> 上的 ?
    before = to_app.calls
    check("4.1 Ready(Ok) 求值", q_poll_result(ready(ok(1)), convert) == (YIELD, 1))
    check("4.2 Pending 原样返回", q_poll_result(pending(), convert) == (RETURN, pending()))
    check("4.3 Pending 不调 From::from", to_app.calls == before)
    rp = q_poll_result(ready(err(ParseIntError())), convert)
    check("4.4 Ready(Err) 转成 Ready(Err(AppError))",
          rp[0] == RETURN and rp[1][1][0] == "Err" and isinstance(rp[1][1][1], AppError), str(rp))
    check("4.5 这次确实走了 From::from", to_app.calls == before + 1)

    # ---- 5. 缺少 From impl → E0277
    try:
        reg.convert(IoError(), "MyError")
        raise AssertionError("5.1 缺 From 应当报错")
    except TypeError as e:
        check("5.1 报 E0277 并点名类型", "E0277" in str(e) and "IoError" in str(e), str(e))

    # ---- 6. Box<dyn Error> 的 blanket impl：任何 Error 都能进
    boxed = reg.convert(IoError(), "Box<dyn Error>")
    check("6.1 io 错误能装箱", isinstance(boxed, BoxDynError))
    boxed2 = reg.convert(ParseIntError(), "Box<dyn Error>")
    check("6.2 parse 错误也能装箱（同一静态类型）",
          type(boxed) is type(boxed2))
    check("6.3 装箱后仍能 downcast 回具体类型",
          isinstance(boxed.downcast_ref(IoError), IoError))
    try:
        BoxDynError("not an error")
        raise AssertionError("6.4 非 Error 类型不该能装箱")
    except TypeError:
        pass

    # ---- 7. source 链与「不要两边都渲染」
    io = IoError()
    app = AppError("Io", "unable to read configuration", source=io)
    chain = error_chain(app)
    check("7.1 链长 2", len(chain) == 2, str(len(chain)))
    check("7.2 链尾是最底层错误", chain[-1] is io)
    check("7.3 良构错误遵守 not-both 规则", obeys_not_both_rule(app))

    bad = BadAppError("Io", "unable to read configuration", source=io)
    check("7.4 反例把 source 文本重复渲染进 Display",
          io.display() in bad.display())
    check("7.5 反例被 not-both 规则判为违规", not obeys_not_both_rule(bad))

    # 三层嵌套
    l1 = IoError()
    l2 = AppError("Io", "decode failed", source=l1)
    l3 = AppError("App", "request failed", source=l2)
    check("7.6 三层链长 3", len(error_chain(l3)) == 3, str(len(error_chain(l3))))
    check("7.7 每一层都守规则", all(obeys_not_both_rule(e) for e in error_chain(l3)))

    # ---- 8. main 的退出码
    check("8.1 Ok(() ) → 0", exit_code(ok(())) == 0)
    check("8.2 Err → 非 0", exit_code(err(app)) == 1)

    # ---- 9. unwrap vs expect（官方：生产代码更倾向 expect）
    check("9.1 unwrap 成功取值", unwrap(ok(5)) == 5)
    try:
        unwrap(err(io))
        raise AssertionError("9.2 unwrap 失败应 panic")
    except RuntimeError as e:
        check("9.2 unwrap 的 panic 信息", "unwrap()" in str(e) and "IoError" in str(e), str(e))
    try:
        expect(err(io), "hello.txt should be included in this project")
        raise AssertionError("9.3 expect 失败应 panic")
    except RuntimeError as e:
        check("9.3 expect 带上自定义上下文",
              str(e).startswith("hello.txt should be included"), str(e))

    # ---- 10. 官方的错误文案风格：小写、无尾部标点
    check("10.1 io 文案无尾部标点", not io.display().rstrip().endswith("."))
    check("10.2 parse 文案是小写短句",
          ParseIntError().display() == "invalid digit found in string")
    check("10.3 Error 要求 Debug + Display 两者都在",
          hasattr(io, "debug") and hasattr(io, "display"))

    print("error_model: all assertions passed")


if __name__ == "__main__":
    run()
