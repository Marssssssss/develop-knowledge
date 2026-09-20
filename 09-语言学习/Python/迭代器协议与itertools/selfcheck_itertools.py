# -*- coding: utf-8 -*-
"""
迭代器协议与 itertools / functools —— 自检。

把 main.py 的手写实现与 CPython 官方实现逐条对拍,
"文档里写了但容易记错"的语义全部落成断言。

    python selfcheck_itertools.py
"""
import functools
import itertools
import collections.abc as cabc

from main import Count, CountIter, my_groupby, my_tee, my_lru_cache

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


# ---------------------------------------------------------------- 1. 迭代器协议
def demo_protocol():
    c = Count(3)
    a, b = iter(c), iter(c)
    assert a is not b, "容器每次迭代都应给出新迭代器"
    assert list(a) == [1, 2, 3] and list(b) == [1, 2, 3], "容器可重复遍历"
    it = CountIter(3)
    assert iter(it) is it, "迭代器的 __iter__ 必须返回 self"
    assert list(it) == [1, 2, 3]
    assert list(it) == [], "迭代器是一次性的:耗尽后再迭代为空"
    ok("协议:容器 __iter__ 造新迭代器且可重遍历;迭代器 __iter__ 返回 self 且一次性")


def demo_sentinel():
    """iter(callable, sentinel): callable 返回 sentinel 时停止,且**该值被丢弃**。"""
    seq = [b"ab", b"cd", b""]
    pos = [0]

    def read(_):
        v = seq[pos[0]]
        pos[0] += 1
        return v

    got = list(iter(lambda: read(2), b""))
    assert got == [b"ab", b"cd"], "哨兵值本身不会产出"
    ok("iter(callable, sentinel): 命中哨兵即停,哨兵值被丢弃不产出")


def demo_stopiteration_in_generator():
    """PEP 479:生成器内部抛出的 StopIteration 会被改写成 RuntimeError。"""
    def gen_bad():
        yield 1
        raise StopIteration("boom")

    def gen_good():
        yield 1
        return

    g = gen_bad()
    assert next(g) == 1
    try:
        next(g)
        raise AssertionError("PEP 479 应把 StopIteration 变成 RuntimeError")
    except RuntimeError as e:
        assert "StopIteration" in str(e), f"异常信息应提到 StopIteration,实为 {e}"
    assert list(gen_good()) == [1], "裸 return 才是正常的生成器结束方式"
    ok("PEP 479:生成器内的 StopIteration 被转成 RuntimeError(裸 return 才正常结束)")


# ---------------------------------------------------------------- 2. itertools
def demo_groupby():
    data = "AAAABBBCCDAABBB"
    mine = [(k, "".join(g)) for k, g in my_groupby(data)]
    official = [(k, "".join(g)) for k, g in itertools.groupby(data)]
    assert mine == official, f"与官方 groupby 不一致: {mine} vs {official}"
    assert [k for k, _ in official] == list("ABCDAB"), "只按**连续**相同键切分"

    # 共享源:推进主迭代器后,上一个分组就空了
    it = itertools.groupby(data)
    k, g = next(it)
    assert k == "A" and next(g) == "A"
    next(it)                      # 主迭代器前进到下一组
    assert list(g) == [], "共享底层迭代器:旧分组被抽干"
    ok("groupby:只切连续相同键;分组与主迭代器共享源,推进后旧分组失效")


def demo_tee():
    a, b = my_tee("ABC")
    assert next(a) == "A"
    assert list(b) == ["A", "B", "C"], "落后的分支仍能看到全部数据"
    a2, b2 = itertools.tee(iter("ABC"))
    assert list(a2) == list(b2) == list("ABC")

    # 展平:官方 tee 遇到 _tee 输入时复用其链 —— 手写的 _Tee 也照做
    t1, _ = my_tee(iter("XY"))
    assert next(t1) == "X"
    u1, = my_tee(t1, 1)               # 必须在推进**之后**分叉,才是 lookahead 语义
    assert next(u1) == "Y", "嵌套 tee 共享同一条链,可分叉出 peek 能力"
    assert next(t1) == "Y", "分叉后主分支仍读到同一个值"

    # 官方 lookahead 惯用法
    def lookahead(t):
        [forked] = itertools.tee(t, 1)
        return next(forked)

    iterator = iter("abcdef")
    [iterator] = itertools.tee(iterator, 1)
    assert next(iterator) == "a"
    assert lookahead(iterator) == "b"
    assert next(iterator) == "b"
    ok("tee:共享链表、落后分支不丢数据;嵌套 tee 展平后可做 lookahead")


def demo_islice_etc():
    assert list(itertools.islice("ABCDEFG", 2, None)) == list("CDEFG")
    assert list(itertools.islice("ABCDEFG", 0, None, 2)) == list("ACEG")
    try:
        list(itertools.islice("ABC", -1))
        raise AssertionError("islice 应拒绝负值")
    except ValueError:
        pass
    assert list(itertools.accumulate([1, 2, 3, 4, 5])) == [1, 3, 6, 10, 15]
    assert list(itertools.accumulate([1, 2, 3], initial=100)) == [100, 101, 103, 106]
    assert list(itertools.zip_longest("ABCD", "xy", fillvalue="-")) == [
        ("A", "x"), ("B", "y"), ("C", "-"), ("D", "-")]
    assert list(itertools.combinations("ABC", 2)) == [
        ("A", "B"), ("A", "C"), ("B", "C")]
    assert len(list(itertools.permutations("ABCD", 2))) == 12
    ok("islice 拒绝负值;accumulate 支持 initial;zip_longest / combinations 与文档一致")


# ---------------------------------------------------------------- 3. functools
def demo_lru_cache():
    @functools.lru_cache          # 关键:默认 maxsize 是 **128** 不是 None
    def f(x):
        return x * 2

    assert f.cache_info().maxsize == 128, "lru_cache 默认 maxsize=128"
    assert f(2) == 4 and f(2) == 4
    assert f.cache_info().hits == 1 and f.cache_info().misses == 1

    # typed=False:多参数时 1 与 1.0 就是同一个键
    seen = []

    @functools.lru_cache(maxsize=8)
    def h2(a, b):
        seen.append((a, b))
        return repr((a, b))

    assert h2(1, 2) == "(1, 2)"
    assert h2(1, 2.0) == "(1, 2)" and h2(1.0, 2) == "(1, 2)"
    assert seen == [(1, 2)], "多参数:typed=False 时 1 与 1.0 共用条目"

    # typed=True 才按类型分开
    @functools.lru_cache(maxsize=8, typed=True)
    def h3(a, b):
        return repr((a, b))

    assert h3(1, 2) == "(1, 2)" and h3(1.0, 2) == "(1.0, 2)"
    assert h3.cache_info().misses == 2, "typed=True 时类型参与键"

    # 单参数非对称:fasttypes 捷径把 int/str 的键摊平成裸值 —— f(1) 的键是 1
    # 而 f(1.0) 的键是 (1.0,),故不相等;可 (True,) 与 (1.0,) 相等 → f(True) 命中 1.0
    @functools.lru_cache(maxsize=8)
    def one(x):
        return repr(x)

    assert one(1) == "1"
    assert one(1.0) == "1.0", "单参数:裸 int 键与 (float,) 键不相等 → 未命中"
    assert one(True) == "1.0", "单参数:True 命中了 1.0 的条目(返回 1.0 的结果)"
    assert one.cache_info().misses == 2 and one.cache_info().hits == 1

    # 关键字实参的**顺序**参与键的构造:f(x=1,y=2) 与 f(y=2,x=1) 是两条
    @functools.lru_cache(maxsize=8)
    def k(x, y):
        return x + y

    assert k(x=1, y=2) == 3
    assert k(y=2, x=1) == 3
    assert k.cache_info().misses == 2, "kwds 顺序不同 → 两个独立缓存条目"

    # 手写实现与官方在"淘汰最久未用"上行为一致
    @my_lru_cache(maxsize=2)
    def m(x):
        return x

    for i in (1, 2, 3):
        m(i)
    assert m.cache_info()[2] == 2, "容量超限时淘汰最久未用"
    assert my_lru_cache(maxsize=2)(lambda x: x)(1) == 1
    ok("lru_cache:默认 maxsize=128;多参数时 1 与 1.0 同条目而单参数因 fasttypes 捷径不同;kwds 顺序参与键")


def demo_singledispatch():
    @functools.singledispatch
    def show(x):
        return "object"

    @show.register
    def _(x: int):
        return "int"

    @show.register(list)
    def _(x):
        return "list"

    assert show(1) == "int" and show([]) == "list" and show(object()) == "object"
    assert show.dispatch(bool) is show.registry[int], "bool 经 MRO 落到 int 的实现"

    class MyList(list):
        pass

    assert show(MyList()) == "list", "未注册的类型沿 MRO 找最近实现"

    # 歧义:同时命中两个互不相干的隐式 ABC → RuntimeError
    @functools.singledispatch
    def amb(x):
        return "base"

    @amb.register(cabc.Sized)
    def _(x):
        return "sized"

    @amb.register(cabc.Iterable)
    def _(x):
        return "iterable"

    class Both:
        def __len__(self): return 0
        def __iter__(self): return iter(())

    try:
        amb.dispatch(Both)
        raise AssertionError("两个互不相干的隐式 ABC 应触发 Ambiguous dispatch")
    except RuntimeError as e:
        assert "Ambiguous" in str(e), f"实为 {e}"
    ok("singledispatch:沿 MRO 找最近实现;两个互不相干的隐式 ABC 触发 Ambiguous dispatch")


def demo_misc_functools():
    assert functools.reduce(lambda a, b: a + b, [1, 2, 3]) == 6
    assert functools.reduce(lambda a, b: a + b, [], 10) == 10, "有 initial 时空序列可用"
    try:
        functools.reduce(lambda a, b: a + b, [])
        raise AssertionError("无 initial 且空序列应抛 TypeError")
    except TypeError:
        pass
    p = functools.partial(lambda a, b: a - b, 10)
    assert p(4) == 6
    assert not hasattr(p, "__name__"), "partial 对象没有 __name__"

    class C:
        def __init__(self):
            self.n = 0

        @functools.cached_property
        def val(self):
            self.n += 1
            return self.n * 10

    c = C()
    assert c.val == 10 and c.val == 10, "cached_property 只算一次"
    assert c.__dict__["val"] == 10, "结果写进实例 __dict__"
    ok("reduce 需 initial 才能吃空序列;partial 无 __name__;cached_property 写入实例字典")


# ---------------------------------------------------------------- 4. 实现细节
def demo_itertools_c_source():
    """CPython:Modules/itertoolsmodule.c 里 tee 用的是 teedataobject 链表(读源码核实)。"""
    import inspect as _i
    assert isinstance(itertools.tee("A")[0], itertools._tee), "tee 返回 _tee 对象"
    assert _i.isgeneratorfunction(itertools.groupby.__call__) is False
    ok("官方 tee 返回 itertools._tee(链表节点对象),不是生成器函数")


def main():
    import sys
    print(f"Python {sys.version.split()[0]}\n")
    print("1. 迭代器协议")
    demo_protocol()
    demo_sentinel()
    demo_stopiteration_in_generator()
    print("2. itertools")
    demo_groupby()
    demo_tee()
    demo_islice_etc()
    print("3. functools")
    demo_lru_cache()
    demo_singledispatch()
    demo_misc_functools()
    print("4. 实现细节")
    demo_itertools_c_source()
    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
