# -*- coding: utf-8 -*-
"""
Python · 参数绑定与调用约定

把「def 里写的参数」与「调用时给的实参」如何配对这件事做实:
  - PEP 570 的 / (positional-only) 与 PEP 3102 的 * (keyword-only)
  - inspect.Signature / Parameter 的五种 kind
  - 手写一遍绑定算法,与 inspect 的 bind / bind_partial 对拍

参考(实读):
  - https://peps.python.org/pep-0570/      (positional-only 的语法、合法/非法形式、语义角落)
  - https://peps.python.org/pep-3102/      (keyword-only 的动机与语法)
  - https://docs.python.org/3/library/inspect.html  (Parameter.kind / Signature.bind / BoundArguments)
"""
import inspect

PASS = []
PO, PK, VP, KO, VK = ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD",
                      "VAR_POSITIONAL", "KEYWORD_ONLY", "VAR_KEYWORD")


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


# ------------------------------------------------- 1. 五种 kind 与内建函数签名
def demo_kinds():
    assert str(inspect.signature(len)) == "(obj, /)"
    assert str(inspect.signature(divmod)) == "(x, y, /)"
    assert str(inspect.signature(abs)) == "(x, /)"
    assert str(inspect.signature(sorted)) == "(iterable, /, *, key=None, reverse=False)"
    # 关键形态:positional-only 之后还能跟 *args
    assert str(inspect.signature(map)) == "(function, iterable, /, *iterables)"
    assert str(inspect.signature(str.replace)) == "(self, old, new, /, count=-1)"
    # PEP 3102:  *args 之后的参数是 keyword-only
    assert str(inspect.signature(print)) == \
        "(*args, sep=' ', end='\\n', file=None, flush=False)"
    assert [p.kind.name for p in inspect.signature(print).parameters.values()] == \
        [VP, KO, KO, KO, KO]

    def f(a, b, /, c, d=4, *args, e, f=6, **kw):
        pass

    kinds = [p.kind.name for p in inspect.signature(f).parameters.values()]
    assert kinds == [PO, PO, PK, PK, VP, KO, KO, VK], kinds
    ok("五种 kind 齐全:/ 左边 POSITIONAL_ONLY,* 右边 KEYWORD_ONLY;map 是 / 后接 *args")


# ------------------------------------------------- 2. PEP 570 的语法约束
def compiles(src):
    try:
        compile(src, "<s>", "exec")
        return True
    except SyntaxError:
        return False


def demo_pep570_syntax():
    valid = [
        "def f(p1, p2, /, p_or_kw, *, kw): pass",
        "def f(p1, p2=None, /, p_or_kw=None, *, kw): pass",
        "def f(p1, p2=None, /, *, kw): pass",
        "def f(p1, p2=None, /): pass",
        "def f(p1, p2, /): pass",
        "def f(p_or_kw, *, kw): pass",
    ]
    invalid = [
        "def f(p1, p2=None, /, p_or_kw, *, kw): pass",   # 带默认之后不能再有无默认的
        "def f(p1=None, p2, /, p_or_kw=None, *, kw): pass",
        "def f(p1=None, p2, /): pass",
    ]
    for src in valid:
        assert compiles(src), f"PEP 570 认为合法: {src}"
    for src in invalid:
        assert not compiles(src), f"PEP 570 认为非法: {src}"
    ok("PEP 570 语法:6 条合法 / 3 条非法形式与规范逐条吻合")


def demo_pep570_corner():
    """规范里的语义角落:positional-only 的名字可以在 **kwds 里出现。"""
    def plain(name, **kwds):
        return "name" in kwds

    def posonly(name, /, **kwds):
        return "name" in kwds

    assert plain(1) is False
    try:
        plain(1, **{"name": 2})
        raise AssertionError("应报 multiple values")
    except TypeError as e:
        assert "multiple values" in str(e), f"实为 {e}"
    assert posonly(1, **{"name": 2}) is True, "/ 之后 name 不再是保留字,可以进 **kwds"

    def only(a, /):
        return a

    try:
        only(a=1)
        raise AssertionError("positional-only 不能按关键字传")
    except TypeError as e:
        assert "positional-only" in str(e), f"实为 {e}"
    ok("PEP 570 角落:`/` 让参数名可安全出现在 **kwds(这正是 dict.update 需要的)")


# ------------------------------------------------- 3. PEP 3102 keyword-only
def demo_pep3102():
    def compare1(a, b, *, key=None):
        return (a, b, key)

    def compare2(*wordlist, case_sensitive=False):
        return (wordlist, case_sensitive)

    def required(a, *, opt):        # 无默认 → 必填的关键字参数
        return (a, opt)

    assert compare1(1, 2, key=len) == (1, 2, len)
    try:
        compare1(1, 2, 3)
        raise AssertionError("第 3 个位置参数不能落进 key")
    except TypeError as e:
        assert "positional arguments" in str(e), f"实为 {e}"
    assert compare2("a", "b", case_sensitive=True) == (("a", "b"), True)
    assert required(1, opt=2) == (1, 2)
    try:
        required(1)
        raise AssertionError("无默认的 keyword-only 必须给")
    except TypeError as e:
        assert "required keyword-only" in str(e) or "opt" in str(e), f"实为 {e}"
    ok("PEP 3102:* 或 *args 之后是 keyword-only;无默认即必填,位置实参填不进去")


# ------------------------------------------------- 4. 手写绑定算法
def my_bind(params, args, kwargs, partial=False):
    """params: [(name, kind, has_default)]; 返回 {name: value}。"""
    bound, pos, rest = {}, list(args), {}
    var_pos = var_kw = None
    slots = []
    for name, kind, has_default in params:
        if kind == VP:
            var_pos = name
        elif kind == VK:
            var_kw = name
        else:
            slots.append((name, kind, has_default))

    for name, kind, _ in slots:
        if not pos or kind == KO:
            break
        bound[name] = pos.pop(0)
    if pos:
        if var_pos is None:
            raise TypeError(f"too many positional arguments")
        bound[var_pos] = tuple(pos)
    # 注意:空的 *args 不会被 inspect 收录进 arguments,这里保持一致

    by_name = {n: k for n, k, _ in slots}
    for k, v in kwargs.items():
        if k not in by_name:
            if var_kw is None:
                raise TypeError(f"unexpected keyword argument {k!r}")
            rest[k] = v
            continue
        kind = by_name[k]
        if kind == PO:                       # 名字只是"占位",不是关键字入口
            if var_kw is None:
                raise TypeError(f"{k!r} is positional-only")
            rest[k] = v
        elif k in bound:
            raise TypeError(f"multiple values for {k!r}")
        else:
            bound[k] = v
    if var_kw is not None and rest:                    # 同理:空 **kw 不收录
        bound[var_kw] = rest
    if not partial:
        missing = [n for n, _, hd in slots if not hd and n not in bound]
        if missing:
            raise TypeError(f"missing required argument: {missing}")
    return bound


def _params_of(fn):
    return [(n, p.kind.name, p.default is not inspect.Parameter.empty)
            for n, p in inspect.signature(fn).parameters.items()]


def demo_bind_against_inspect():
    def target(a, b, /, c, d=4, *args, e, f=6, **kw):
        return (a, b, c, d, args, e, f, kw)

    sig = inspect.signature(target)
    params = _params_of(target)
    cases = [
        ((1, 2, 3), {"e": 9}),
        ((1, 2, 3, 5), {"e": 9, "f": 7}),
        ((1, 2, 3, 5, 8), {"e": 9, "zz": 1}),
        ((1, 2), {"c": 3, "e": 9}),
        ((1, 2, 3), {"e": 9, "a": 99}),       # a 是 positional-only → 落进 **kw
    ]
    for a, k in cases:
        mine = my_bind(params, a, k)
        real = dict(sig.bind(*a, **k).arguments)
        assert mine == real, f"{a}{k}: 手算 {mine} != inspect {real}"
    assert my_bind(params, (1, 2), {"e": 9}, partial=True) == {"a": 1, "b": 2, "e": 9}

    for bad in [((1,), {"e": 9}), ((), {"e": 9}), ((1, 2, 3), {})]:
        for bind in (lambda: my_bind(params, *bad),
                     lambda: sig.bind(*bad[0], **bad[1])):
            try:
                bind()
                raise AssertionError(f"应当报错: {bad}")
            except TypeError:
                pass
    ok("手写绑定算法与 inspect.Signature.bind 在 5 组正常用例与 3 组错误用例上口径一致")


def demo_bound_arguments():
    def f(a, b=2, *, c=3):
        return (a, b, c)

    sig = inspect.signature(f)
    ba = sig.bind(1)
    assert dict(ba.arguments) == {"a": 1}, "未给的默认值不出现在 arguments 里"
    ba.apply_defaults()
    assert dict(ba.arguments) == {"a": 1, "b": 2, "c": 3}
    assert sig.bind_partial().arguments == {}, "bind_partial 允许什么都不给"
    try:
        sig.bind()
        raise AssertionError("bind 必须给 a")
    except TypeError:
        pass
    try:
        sig.bind(1, 2, 3)
        raise AssertionError("c 是 keyword-only")
    except TypeError:
        pass
    try:
        sig.parameters["a"].kind = KO
        raise AssertionError("Parameter 应不可变")
    except AttributeError:
        pass
    new = sig.replace(parameters=[p for p in sig.parameters.values()])
    assert str(new) == str(sig), "Signature 不可变,只能 replace 出新对象"
    ok("BoundArguments:默认值需 apply_defaults 才出现;bind_partial 宽松;Signature 不可变")


def main():
    print("1. 参数种类")
    demo_kinds()
    print("2. PEP 570")
    demo_pep570_syntax()
    demo_pep570_corner()
    print("3. PEP 3102")
    demo_pep3102()
    print("4. 绑定算法")
    demo_bind_against_inspect()
    demo_bound_arguments()
    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
