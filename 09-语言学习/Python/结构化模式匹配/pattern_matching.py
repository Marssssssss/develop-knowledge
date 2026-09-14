#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Python 结构化模式匹配(structural pattern matching,PEP 634)教学 demo。

  §1 match/case 基本语义(首个命中 + guard 短路 + 绑定活到块外)
  §2 序列模式(定长/变长、str/bytes 被排除)  §3 映射模式(**rest、重复键、__missing__)
  §4 类模式(isinstance + 属性查找 + __match_args__)  §5 硬规则(compile() 观测)
  §6 综合:用嵌套模式写事件报文校验器

规格来源:PEP 634 与 docs.python.org/3/reference/compound_stmts.html#match。
运行:python pattern_matching.py
"""

import enum
import sys
from collections import namedtuple, deque
from dataclasses import dataclass, field


Port = enum.IntEnum("Port", {"HTTP": 80, "HTTPS": 443})   # 值模式需"点分名字"


def check_syntax(src, label):
    """PEP 634 的若干规则由编译器强制,统一用 compile() 观测真实报错。"""
    try:
        compile(src, "<pattern>", "exec")
        print(f"  [通过]   {label}")
    except SyntaxError as exc:
        print(f"  [报错]   {label}\n             -> SyntaxError: {exc.msg}")


# ---------------------------------------------------------------------------
# §1 基本语义
# ---------------------------------------------------------------------------
def describe(value):
    """第一个"模式成功且 guard 为真"的 case 胜出。PEP 634 原文:
    "the first case block whose patterns succeeds matching it *and* whose guard
    condition (if present) is 'truthy'"。
    """
    match value:
        case 0:
            return "literal 0"
        case 1 if value == 1:
            return "literal 1(guard 为真)"
        case str() | bytes() as text:
            return f"text {text!r}"
        case _:
            return "wildcard"


def demo_basic():
    print("=" * 72)
    print("[1] 基本语义:首个命中、guard 短路、绑定活到 match 之外")
    print("=" * 72)
    for v in (0, 1, "hello", 3.5):
        print(f"  describe({v!r}) -> {describe(v)}")
    match True:                     # 数字字面量模式用 == 比较,而 True == 1
        case 1:
            hit = "case 1 先命中(因为 True == 1)"
        case True:
            hit = "case True 命中(它用 is 比较)"
    print(f"  match True -> {hit}")
    match [1, 2, 3]:
        case [first, *_]:
            matched = first         # 绑定在 match 之外依然可见
    print(f"  绑定可越界使用:first = {matched}(原文:Name bindings made during a"
          " successful pattern match outlive the executed block)")
    print("  失败匹配的中间绑定属未指定行为,代码不应依赖")


# ---------------------------------------------------------------------------
# §2 序列模式
# ---------------------------------------------------------------------------
def seq_shape(value):
    match value:
        case []:
            return "empty"
        case [x]:
            return f"single({x})"
        case [a, b]:
            return f"pair({a},{b})"
        case [head, *tail]:
            return f"head({head}) + tail[{len(tail)}]"
        case _:
            return "not a sequence pattern"


def demo_sequence():
    print("\n" + "=" * 72)
    print("[2] 序列模式:list/tuple 等价,str/bytes 被排除,星号至多一个")
    print("=" * 72)
    samples = [[], [1], (1, 2), list(range(6)), deque([1, 2, 3]), "abc", b"ab",
               range(3), memoryview(b"xy")]
    for v in samples:
        label = f"memoryview({bytes(v)!r})" if isinstance(v, memoryview) else repr(v)
        print(f"  seq_shape({label:<22}) -> {seq_shape(v)}")
    print("  PEP 634:带 Py_TPFLAGS_SEQUENCE 的标准库类为 array/deque/list/memoryview/"
          "range/tuple;'Although str, bytes, and bytearray are usually considered"
          " sequences, they ... do not match sequence patterns.'")
    print("  定长模式要求 len(subject) == 子模式数;变长模式要求 len >= 非星号子模式数,"
          "长度经内置 len() 取得。")


# ---------------------------------------------------------------------------
# §3 映射模式
# ---------------------------------------------------------------------------
class CountingDict(dict):
    """记录 __missing__ 调用次数:映射模式用 get() 两参数形式,不会触发 __missing__。"""

    missing_calls = 0

    def __missing__(self, key):
        CountingDict.missing_calls += 1


def route(config):
    match config:
        case {"scheme": "https", "host": host, "port": port}:
            return f"https://{host}:{port}"
        case {"host": host, **rest} if rest:
            return f"{host} + 额外键 {sorted(rest)}"
        case {"host": host}:
            return f"http://{host}(默认端口)"
        case {}:
            return "空配置"
        case _:
            return "非映射"


def demo_mapping():
    print("\n" + "=" * 72)
    print("[3] 映射模式:键用 == 匹配、值用 get() 取、**rest 收集剩余键")
    print("=" * 72)
    cases = [{"scheme": "https", "host": "a.com", "port": 443},
             {"host": "b.com"}, {"host": "c.com", "trace": True, "retry": 3}, {}]
    for cfg in cases:
        print(f"  route({cfg!s:<48}) -> {route(cfg)}")
    CountingDict.missing_calls = 0
    print(f"  route(CountingDict(host='d.com')) -> {route(CountingDict(host='d.com'))};"
          f"__missing__ 调用次数 = {CountingDict.missing_calls}")
    print("  PEP 634:'Key-value pairs are matched using the two-argument form of the"
          " subject's get() method.' —— defaultdict 的 __missing__ 因此不会被触发。")
    match {"host": "e.com", "port": 443}:              # 值模式:必须是点分名字
        case {"host": h, "port": Port.HTTPS}:
            print(f"  值模式 Port.HTTPS 命中:host={h}(值模式用 == 比较)")
    HTTP_PORT = 9999
    match {"host": "f.com", "port": 8080}:             # 裸名字 = 捕获模式,必定成功
        case {"host": h, "port": HTTP_PORT}:
            print(f"  裸名字被当成捕获模式:HTTP_PORT 由 9999 被改写为 {HTTP_PORT}")


# ---------------------------------------------------------------------------
# §4 类模式与 __match_args__
# ---------------------------------------------------------------------------
@dataclass
class Point:
    x: int
    y: int


@dataclass
class Circle:
    center: Point
    radius: float
    meta: dict = field(default_factory=dict, init=False)   # init=False 不进 __match_args__


Geo = namedtuple("Geo", "lon lat")


class Fahrenheit:
    """自定义 __match_args__:位置子模式映射到属性名,可指向 property。"""

    __match_args__ = ("value",)

    def __init__(self, value):
        self.value = value


def demo_class():
    print("\n" + "=" * 72)
    print("[4] 类模式:isinstance 前置 + 属性查找 + __match_args__")
    print("=" * 72)
    print(f"  dataclass Point.__match_args__ = {Point.__match_args__};"
          f"含 init=False 字段的 Circle = {Circle.__match_args__}")
    print(f"  namedtuple 自动生成 Geo.__match_args__ = {Geo.__match_args__}")
    for obj in (Point(0, 0), Point(3, 4), Circle(Point(0, 0), 2.0), Geo(12.3, 45.6)):
        match obj:
            case Point(0, 0):
                print(f"  {obj!r:<30} -> 原点(位置子模式值用 == 比较)")
            case Point(x, y) if x == y:
                print(f"  {obj!r:<30} -> 对角线上的点")
            case Point(x=0, y=y):
                print(f"  {obj!r:<30} -> x=0, y={y}")
            case Point(x=x, y=y):
                print(f"  {obj!r:<30} -> 普通点 ({x}, {y})")
            case Circle(center=Point(x, y), radius=r):
                print(f"  {obj!r:<30} -> 圆心({x},{y}) 半径 {r}")
            case Geo(lon=lon, lat=lat):
                print(f"  {obj!r:<30} -> 经纬度 {lon},{lat}")
    match Fahrenheit(70):
        case Fahrenheit(v) if v > 60:                  # 经 __match_args__ 绑定 .value
            print(f"  自定义 __match_args__ 生效:Fahrenheit(v) 绑定到 .value = {v}")
    match 5:
        case int(n):                                   # 内建 int 允许单个位置子模式
            print(f"  内建 int 的单位置子模式:case int(n) -> n = {n!r}(匹配整个 subject)")


# ---------------------------------------------------------------------------
# §5 硬规则
# ---------------------------------------------------------------------------
def check_runtime(fn, label):
    try:
        fn()
        print(f"  [通过]   {label}")
    except TypeError as exc:
        print(f"  [运行时] {label}\n             -> TypeError: {exc}")


def demo_rules():
    print("\n" + "=" * 72)
    print("[5] 由语法/运行时强制的规则(编译期规则用 compile() 观测)")
    print("=" * 72)
    check_syntax("match 1:\n case _:\n  pass\n case 2:\n  pass\n", "不可反驳模式必须最后(PEP 634:至多一个且必须最后)")
    check_syntax("match 1:\n case 1 | y:\n  pass\n case 2:\n  pass\n", "OR 模式各分支必须绑定相同的名字集合")
    check_syntax("match 1:\n case [x, x]:\n  pass\n", "同一模式内同一名字只能绑定一次")
    check_syntax("match 1:\n case 1 as _:\n  pass\n case 2:\n  pass\n", "AS 模式右值不得是 _(它绑定不了名字)")
    check_syntax("match {}:\n case {**rest, 'a': 1}:\n  pass\n", "映射模式中 **rest 必须在最后")
    check_syntax("match {}:\n case {'a': 1, 'a': 2}:\n  pass\n", "映射模式不得含重复的字面量键")

    def too_many_positional():
        match Point(1, 2):
            case Point(a, b, c):
                pass

    def bad_match_args():
        Bad = type("Bad", (), {"__match_args__": ["a"]})   # 规范要求必须是元组
        match Bad():
            case Bad(a):
                pass

    check_runtime(too_many_positional, "位置子模式多于 __match_args__ 长度")
    check_runtime(bad_match_args, "__match_args__ 不是元组")
    print("  PEP 634:匹配唯一的副作用是名字绑定;调用哪些方法/几次属未定义,"
          "user code should not rely on it。")


# ---------------------------------------------------------------------------
# §6 综合
# ---------------------------------------------------------------------------
def validate_event(event):
    """用一层模式同时表达类型、形状与取值域,替代等价的 isinstance + 下标 + try/except。"""
    match event:
        case {"type": "click", "pos": (int(x), int(y))} if x >= 0 and y >= 0:
            return f"click at ({x}, {y})"
        case {"type": "click", "pos": _}:
            return "click 坐标非法(要求非负整数对)"
        case {"type": "key", "code": str(code), "mods": [*mods]}:
            return f"key {code} + modifiers {mods}"
        case {"type": "resize", "size": {"w": w, "h": h}} if w > 0 and h > 0:
            return f"resize to {w}x{h}"
        case {"type": str(kind), **rest}:
            return f"未支持的事件 {kind},附带 {sorted(rest)}"
        case _:
            return "非法报文"


def demo_comprehensive():
    print("\n" + "=" * 72)
    print("[6] 综合:事件报文校验(类/序列/映射 模式嵌套 + guard)")
    print("=" * 72)
    events = [{"type": "click", "pos": (10, 20)}, {"type": "click", "pos": (-1, 5)},
              {"type": "key", "code": "KeyA", "mods": ["ctrl", "shift"]},
              {"type": "resize", "size": {"w": 800, "h": 600}},
              {"type": "wheel", "delta": -3}, "not-a-dict"]
    for ev in events:
        print(f"  {str(ev):<52} -> {validate_event(ev)}")


def main():
    print(f"Python {sys.version.split()[0]}")
    demo_basic()
    demo_sequence()
    demo_mapping()
    demo_class()
    demo_rules()
    demo_comprehensive()
    print("全部章节执行完毕。")


if __name__ == "__main__":
    main()
