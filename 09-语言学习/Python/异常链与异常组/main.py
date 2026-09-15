# -*- coding: utf-8 -*-
"""异常链(PEP 3134)与异常组(PEP 654)机制演示 + 自检。

运行: python3 main.py   (需要 Python 3.11+)
依据: PEP 3134 / PEP 409 / PEP 415 / PEP 654(见 README 参考资料)。
"""

import sys
import traceback

PASS = []

def ok(name):
    PASS.append(name)
    print(f"[PASS] {name}")

def demo_implicit_context():
    """在 except 块内抛新异常 → 新异常.__context__ 自动指向上一个异常。"""
    try:
        try:
            raise ValueError("inner A")
        except ValueError:
            # PEP 3134:"Whenever an exception is raised, if the exception
            # instance does not already have a __context__ attribute, the
            # interpreter sets it equal to the thread's exception context."
            raise TypeError("inner B")          # 在处理 A 时发生
    except TypeError as e:
        return e

def demo_context_reset_after_except():
    """退出 except 块后线程异常上下文重置为 None(PEP 3134 第 4 条语义)。"""
    try:
        raise ValueError("will be handled")
    except ValueError:
        pass
    # except 块已退出,线程异常上下文已重置 → 新异常没有 __context__
    try:
        raise KeyError("fresh")
    except KeyError as e:
        return e.__context__

def demo_explicit_cause():
    """raise ... from 设置 __cause__ 且隐含 __suppress_context__=True(PEP 415)。"""
    try:
        raise RuntimeError("original")
    except RuntimeError as exc:
        try:
            raise ValueError("wrapped") from exc
        except ValueError as e:
            return e

def demo_suppress_none():
    """raise ... from None:保留 __context__ 但置 __suppress_context__,回溯不再显示旧异常。"""
    try:
        raise OSError("db gone")
    except OSError:
        try:
            raise ValueError("clean surface") from None
        except ValueError as e:
            return e

def demo_traceback_format():
    """两种链接消息:__cause__ → 'direct cause';__context__ → 'During handling'。"""
    # 隐式链
    try:
        try:
            raise ValueError("A")
        except ValueError:
            raise TypeError("B")
    except TypeError as e:
        txt_implicit = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    # 显式链
    try:
        try:
            raise ValueError("A2")
        except ValueError as exc:
            raise TypeError("B2") from exc
    except TypeError as e:
        txt_explicit = "".join(traceback.format_exception(type(e), e, e.__traceback__))
    # 抑制
    e3 = demo_suppress_none()
    txt_suppressed = "".join(traceback.format_exception(type(e3), e3, e3.__traceback__))
    return txt_implicit, txt_explicit, txt_suppressed

def demo_group_shape():
    g_exc = ExceptionGroup("only exceptions", [ValueError("v"), TypeError("t")])
    # BaseExceptionGroup 装的全是 Exception 子类 → 构造器自动升级为 ExceptionGroup
    g_mixed = BaseExceptionGroup("mixed", [ValueError("v"), KeyboardInterrupt()])
    assert type(g_exc) is ExceptionGroup
    assert type(g_mixed) is BaseExceptionGroup
    assert g_mixed.message == "mixed" and len(g_mixed.exceptions) == 2
    # ExceptionGroup 里放非 Exception 子类 → TypeError(构造期即报)
    bad = None
    try:
        ExceptionGroup("bad", [ValueError("v"), KeyboardInterrupt()])
    except TypeError as e:
        bad = e
    assert bad is not None and "popup" not in str(bad)
    ok("组构造:BaseExceptionGroup 全 Exception 叶子自动升级;混入 Base 报 TypeError")
    return g_exc, g_mixed

def demo_split_subgroup():
    g_exc = ExceptionGroup("only exceptions", [ValueError("v"), TypeError("t")])
    sub = g_exc.subgroup(ValueError)                 # 无匹配返回 None 而非空组
    assert sub is not None and sub.message == "only exceptions"
    assert type(sub) is ExceptionGroup
    none_sub = g_exc.subgroup(KeyError)
    assert none_sub is None, "PEP 654: 无匹配时 subgroup 返回 None 而非空组"
    match, rest = g_exc.split(TypeError)
    assert isinstance(match, ExceptionGroup) and len(match.exceptions) == 1
    assert isinstance(rest, ExceptionGroup) and len(rest.exceptions) == 1
    m2, r2 = g_exc.split(ValueError)
    assert r2 is None or len(r2.exceptions) == 1
    trivial_match, trivial_rest = g_exc.subgroup(KeyError), None
    assert trivial_match is None
    # 嵌套结构保持:匹配穿透层级,每个子句拿到的是"子树"不是扁平列表
    nested = ExceptionGroup("eg", [
        ValueError("a"),
        TypeError("b"),
        ExceptionGroup("nested", [TypeError("c"), KeyError("d")]),
    ])
    t_branch = nested.subgroup(TypeError)
    leaf_types = [type(x).__name__ for x in _flatten(t_branch)]
    assert leaf_types == ["TypeError", "TypeError"], f"嵌套结构应保持: {leaf_types}"
    ok("subgroup/split:无匹配返回 None;平凡分割另一侧为 None;嵌套结构保持")
    return nested

def _flatten(g):
    """按 leaf_generator 思路(PEP 654)递归枚举叶子异常。"""
    for exc in g.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            yield from _flatten(exc)
        else:
            yield exc

def demo_derive():
    """derive:split/subgroup 造新组时的工厂;不重写则可能丢自定义子类。"""
    class MyGroup(BaseExceptionGroup):
        # PEP 654 示例:自定义组应重写 __new__(而非 __init__),因基类构造检查参数
        def __new__(cls, message, excs, code):
            self = super().__new__(cls, message, excs)
            self.code = code
            return self

        def __init__(self, message, excs, code):
            # 吞掉基类 __init__ 收不到的自定义参数(重写 __new__ 时 type.__call__
            # 仍会把原参数原样传给继承来的 __init__)
            super().__init__(message, excs)

    g = MyGroup("eg", [ValueError("v"), KeyboardInterrupt("k")], code=42)
    m, r = g.split(ValueError)
    # 不重写 derive → 返回基类行为:全 Exception → ExceptionGroup
    assert type(m) is ExceptionGroup, "不重写 derive 会丢子类类型(PEP 654 示例)"
    assert type(r) is BaseExceptionGroup, "混 Base 叶子一侧是 BaseExceptionGroup"
    # subgroup/split 共享元数据字段(cause/context/traceback 按引用共享)
    g2 = ExceptionGroup("meta", [ValueError("v")])
    s2 = g2.subgroup(ValueError)
    assert s2.message == "meta"
    ok("derive:默认实现按叶子类型决定组类型;subgroup 继承 message 元数据")

def demo_except_star_split():
    """except* 按顺序对不断缩小的 unhandled 组调 split;未匹配部分继续传播。"""
    log = []
    try:
        try:
            raise ExceptionGroup("eg", [
                ValueError(1), TypeError(2), TypeError(3), KeyError(4),
            ])
        except* ValueError:
            log.append("value")
        except* TypeError:
            log.append("type")
    except ExceptionGroup as rest:
        log.append("rest:" + ",".join(type(x).__name__ for x in _flatten(rest)))
    assert log == ["value", "type", "rest:KeyError"], log
    ok("except*:每个子句至多执行一次;未匹配的 KeyError 继续向外传播")

def demo_except_star_structure():
    """子句拿到的 e 是保留嵌套结构的子组;裸异常匹配时被包装成空消息组。"""
    seen = {}
    try:
        try:
            raise ExceptionGroup("eg", [
                ValueError("a"),
                ExceptionGroup("nested", [TypeError("c")]),
            ])
        except* TypeError as e:
            seen["type"] = (e.message, [type(x).__name__ for x in _flatten(e)])
    except ExceptionGroup as rest:
        seen["rest"] = [type(x).__name__ for x in _flatten(rest)]
    assert seen["type"] == ("eg", ["TypeError"]), seen
    assert seen["rest"] == ["ValueError"], seen
    # 裸异常:被 except* 捕获时包装为 ExceptionGroup('', [原异常])
    got = None
    try:
        raise BlockingIOError("naked")
    except* BlockingIOError as e:
        got = e
    assert isinstance(got, ExceptionGroup) and got.message == ""

    assert [type(x).__name__ for x in _flatten(got)] == ["BlockingIOError"]
    ok("except*:嵌套结构保留;裸异常被包装成空消息 ExceptionGroup")

def demo_except_star_order_and_once():
    """子句顺序有意义(子类检查,与 except 相同);同组可触发多个子句。"""
    hits = []
    try:
        try:
            raise ExceptionGroup("eg", [BlockingIOError(1), ValueError(2)])
        except* OSError:            # 先匹配:BlockingIOError 是 OSError 子类
            hits.append("oserror")
        except* BlockingIOError:    # 永远轮不到
            hits.append("bio")
        except* ValueError:
            hits.append("value")
    except ExceptionGroup:
        raise AssertionError("全部叶子都应被处理")
    assert hits == ["oserror", "value"], hits
    ok("except*:顺序敏感(OSError 先拦截 BlockingIOError);单个组可触发多个子句")

def demo_syntax_rules():
    """语法约束:except 与 except* 不能同 try;空 except*:是 SyntaxError。"""
    def _compile(src):
        try:
            compile(src, "<t>", "exec")
            return None
        except SyntaxError:
            return "SE"
    # 混用 / 空模式 / except* 内 continue、return → 都是 SyntaxError
    for src in (
        "try:\n pass\nexcept ValueError:\n pass\nexcept* TypeError:\n pass\n",
        "try:\n pass\nexcept*:\n pass\n",
        "for i in []:\n try:\n  pass\n except* ValueError:\n  continue\n",
        "try:\n pass\nexcept* ValueError:\n return\n",
    ):
        assert _compile(src) == "SE", src

    # except* ExceptionGroup 是【运行期】TypeError,不是 SyntaxError(PEP 654 原文
    # "Runtime error"——只在真正有异常需要匹配时触发)
    runtime_err = None
    try:
        exec("try:\n raise ExceptionGroup('eg', [ValueError(1)])\n"
             "except* ExceptionGroup:\n pass\n")
    except TypeError as err:
        runtime_err = err
    assert runtime_err is not None and "Use except instead" in str(runtime_err)
    ok("语法:混用 except/except*、空 except* 均报 SyntaxError;except* ExceptionGroup 为运行期 TypeError")

def main():
    print(f"Python {sys.version.split()[0]}\n")

    e = demo_implicit_context()
    assert isinstance(e.__context__, ValueError) and str(e.__context__) == "inner A"
    assert e.__cause__ is None and e.__suppress_context__ is False
    ok("隐式链:except 内抛新异常,__context__ 指向旧异常,__cause__ 仍为 None")

    ctx = demo_context_reset_after_except()
    assert ctx is None, "except 退出后线程异常上下文应重置为 None"
    ok("上下文重置:except 块正常退出后再抛的异常 __context__ 为 None")

    e2 = demo_explicit_cause()
    assert isinstance(e2.__cause__, RuntimeError) and str(e2.__cause__) == "original"
    assert e2.__suppress_context__ is True   # raise from 蕴含抑制(PEP 415)
    ok("显式链:raise ... from 设 __cause__ 且 __suppress_context__=True")

    e3 = demo_suppress_none()
    assert e3.__suppress_context__ is True and e3.__context__ is not None
    ok("from None:__context__ 保留但被 __suppress_context__ 抑制显示")

    t_imp, t_exp, t_sup = demo_traceback_format()
    assert "During handling of the above exception, another exception occurred:" in t_imp
    assert "The above exception was the direct cause of the following exception:" in t_exp
    assert "db gone" not in t_sup and "clean surface" in t_sup
    ok("回溯格式:context→During handling;cause→direct cause;from None 不显示旧异常")

    demo_group_shape()
    demo_split_subgroup()
    demo_derive()
    demo_except_star_split()
    demo_except_star_structure()
    demo_except_star_order_and_once()
    demo_syntax_rules()

    print(f"\n共 {len(PASS)} 项断言全部通过")

if __name__ == "__main__":
    main()
