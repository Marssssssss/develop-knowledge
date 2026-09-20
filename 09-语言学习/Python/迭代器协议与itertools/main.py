# -*- coding: utf-8 -*-
"""
Python · 迭代器协议与 itertools / functools —— 手写实现(模型层)

自检脚本见 selfcheck_itertools.py:
  python selfcheck_itertools.py

参考(实读):
  - https://docs.python.org/3/library/itertools.html
  - https://docs.python.org/3/library/functools.html
  - https://docs.python.org/3/howto/functional.html
  - https://peps.python.org/pep-0479/      (生成器内的 StopIteration)
  - CPython 源码 Lib/functools.py(_make_key / lru_cache / _find_impl)
"""


# ---------------------------------------------------------------- 迭代器协议
class Count:
    """可迭代对象(container):__iter__ 每次都返回一个**新的**迭代器。"""

    def __init__(self, n):
        self.n = n

    def __iter__(self):
        return CountIter(self.n)


class CountIter:
    """迭代器(iterator):__iter__ 返回 self,__next__ 负责推进。"""

    def __init__(self, n):
        self.n = n
        self.i = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.i >= self.n:
            raise StopIteration
        self.i += 1
        return self.i


# ---------------------------------------------------------------- itertools
def my_groupby(iterable, key=None):
    """手写 groupby:共享底层迭代器,推进后旧分组失效。"""
    keyfunc = (lambda x: x) if key is None else key
    it = iter(iterable)
    exhausted = False
    state = {}

    def _grouper(target):
        nonlocal exhausted
        yield state["value"]
        for v in it:
            state["value"], state["key"] = v, keyfunc(v)
            if state["key"] != target:
                return
            yield v
        exhausted = True

    try:
        state["value"] = next(it)
    except StopIteration:
        return
    state["key"] = keyfunc(state["value"])
    while not exhausted:
        target = state["key"]
        grp = _grouper(target)
        yield target, grp
        if state["key"] == target:
            for _ in grp:
                pass


class _Tee:
    """共享链表的一个游标。注意必须定义在模块层:若写在 my_tee 内部,
    每次调用都会新建一个类对象,嵌套 tee 的 isinstance 判断就会恒为 False。"""

    def __init__(self, src, link):
        self.src, self.link = src, link

    def __iter__(self):
        return self

    def __next__(self):
        link = self.link
        if link[1] is None:
            link[0] = next(self.src)
            link[1] = [None, None]
        value, self.link = link      # self.link 前进到 link[1],不是 link[0]
        return value


def my_tee(iterable, n=2):
    """手写 tee:所有分支共享一条单向链表,落后的分支不会丢数据。"""
    it = iter(iterable)
    if isinstance(it, _Tee):            # 展平:tee(tee(x)) 复用上游链
        src, link = it.src, it.link
    else:
        src, link = it, [None, None]
    return tuple(_Tee(src, link) for _ in range(n))


# ---------------------------------------------------------------- functools
def my_lru_cache(maxsize=128, typed=False):
    """手写 LRU:用 dict 的插入序当访问序,命中即移到末尾。"""
    def deco(fn):
        cache = {}
        stat = {"hits": 0, "misses": 0}

        def wrapper(*args, **kwds):
            key = args + (tuple(sorted(kwds.items())),) if kwds else args
            if typed:
                key = (key, tuple(type(v) for v in args))
            if key in cache:
                stat["hits"] += 1
                v = cache.pop(key)
                cache[key] = v
                return v
            stat["misses"] += 1
            v = fn(*args, **kwds)
            cache[key] = v
            if maxsize is not None and len(cache) > maxsize:
                del cache[next(iter(cache))]     # 淘汰最久未用
            return v

        wrapper.cache_clear = cache.clear
        wrapper.cache_info = lambda: (stat["hits"], stat["misses"], len(cache))
        return wrapper
    return deco
