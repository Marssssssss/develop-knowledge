#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Go 逃逸分析的位置图模型（数据/模型层，对照 escape.go 头部注释）。

被 python/main.py 以 `from escape_graph import *` 使用。
"""
# ---------------------------------------------------------------- 位置图
HEAP = "heap"        # escape.go: b.heapLoc.attrs = attrEscapes|attrPersists|attrMutates|attrCalls
CALLEE = "callee"    # 返回值位置：对调用者而言也是「逃逸」
BLANK = "blank"      # _ 赋值目标


class Location:
    __slots__ = ("name", "kind", "escapes", "is_closure")

    def __init__(self, name, kind="local", is_closure=False):
        self.name = name
        self.kind = kind
        self.escapes = kind in (HEAP, CALLEE)
        self.is_closure = is_closure

    def __repr__(self):
        return "%s(%s)" % (self.name, "heap" if self.escapes else "stack")


class EscapeGraph:
    """位置图 + 反向传播。边 semantics：dst <- src，权重 = 解引用次数。"""

    def __init__(self):
        self.locs = {}
        self.edges = []          # (dst, src, derefs)
        self.param_tags = {}     # func -> {"leaks": {i}, "results": {i}}
        self.notes = []

    def loc(self, name, **kw):
        if name in (HEAP, CALLEE, BLANK):
            kw.setdefault("kind", name)     # 哨兵位置自带语义，不能当普通局部变量
        if name not in self.locs:
            self.locs[name] = Location(name, **kw)
        return self.locs[name]

    def assign(self, dst, src, derefs=0):
        """建模 `dst = src`；derefs = `*` 的次数 - `&` 的次数。"""
        self.edges.append((self.loc(dst), self.loc(src), derefs))

    def store(self, dst, src, addr_of=True):
        """`dst = &src`（最常见：把局部变量地址存进某个位置）。"""
        self.assign(dst, src, -1 if addr_of else 0)

    # -------------------------------------------------- 过程间：parameter tag
    def define_func(self, name, nparams, body_leaks=(), body_results=()):
        """body_leaks：第 i 个参数被存进了堆；body_results：第 i 个参数被作为结果返回。"""
        self.param_tags[name] = {"leaks": set(body_leaks), "results": set(body_results)}

    def call(self, name, args, result_to=None):
        """静态调用点：按被调函数的 tag 决定实参是否逃逸。"""
        tag = self.param_tags[name]
        for i in tag["leaks"]:
            self.loc(args[i]).escapes = True
            self.notes.append("%s 的参数 #%d → 堆（parameter tag: leaks）" % (name, i))
        for i in tag["results"]:
            self.loc(args[i]).escapes = True
            self.notes.append("%s 的参数 #%d → 返回值（parameter tag: result）" % (name, i))
        if result_to is not None and (tag["leaks"] or tag["results"]):
            self.loc(result_to).escapes = True

    def solve(self):
        """不动点：dst 逃逸且 derefs <= 0 → src 逃逸。"""
        changed = True
        while changed:
            changed = False
            for dst, src, derefs in self.edges:
                if dst.escapes and derefs <= 0 and not src.escapes:
                    src.escapes = True
                    changed = True
        return self

    def stack_locals(self):
        return sorted(l.name for l in self.locs.values()
                      if l.kind == "local" and not l.escapes)


# ---------------------------------------------------------------- 固定场景
def scenario_return_local():
    g = EscapeGraph()
    g.store(CALLEE, "x")
    return g.solve()


def scenario_local_only():
    g = EscapeGraph()
    g.assign("y", "x")                # y = x（值拷贝，不取地址）
    g.assign("z", "y")
    return g.solve()


def scenario_stored_into_heap():
    g = EscapeGraph()
    g.store(HEAP, "x")
    return g.solve()


def scenario_deref_no_escape():
    """`heap = *q`：解引用一次，只把 q 指向的内容写出去，q 自身不上堆。"""
    g = EscapeGraph()
    g.assign(HEAP, "q", 1)
    return g.solve()


def scenario_closure():
    g = EscapeGraph()
    g.loc("clo", is_closure=True)
    g.store(HEAP, "clo")              # 闭包本身逃逸 → 闭包记录进堆
    g.store("clo", "captured")        # 捕获变量被存进闭包记录
    return g.solve()


def scenario_interface_box():
    g = EscapeGraph()
    g.store(HEAP, "v")                # 装箱进 interface 后交给堆上的位置
    return g.solve()


def scenario_loop_local():
    """循环体内的 &x 若不上堆，则每轮复用同一槽位（Go 会显式提示该坑）。"""
    g = EscapeGraph()
    g.assign("tmp", "x")              # 只在栈内使用
    return g.solve()


def scenario_param_tag():
    g = EscapeGraph()
    g.define_func("sink", 1, body_leaks=[0])
    g.define_func("identity", 1, body_results=[0])
    g.define_func("pure", 1)
    g.call("sink", ["a"])
    g.call("identity", ["b"])
    g.call("pure", ["c"])
    return g.solve()

def derefs_of(assign):
    """从赋值语句文本算出 escape.go 里的边权：解引用次数 − 取地址次数。

    官方注释给出的五个例子（含 `p = **&**&q` 为 2）都由本函数重算得到。
    """
    rhs = assign.split("=", 1)[1].strip()
    return rhs.count("*") - rhs.count("&")
