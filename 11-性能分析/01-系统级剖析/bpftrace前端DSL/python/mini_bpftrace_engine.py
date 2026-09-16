#!/usr/bin/env python3
"""mini_bpftrace_engine.py — 执行引擎(Unset / Engine)与 printf 格式化。

从 mini_bpftrace.py 拆出,只是为了让单文件落到 300 行以内:
分桶与柱状图渲染在 mini_bpftrace_hist.py,词法/语法在 mini_bpftrace_parse.py,
自检与 SRC 程序在 mini_bpftrace.py。
"""
from __future__ import annotations

import re

from mini_bpftrace_hist import Aggregator, probe_matches

# ---------------------------------------------------------------- 执行引擎
class Unset:
    """读不到的变量。既当 0 用,又当假用(谓词 /@start[tid]/ 靠它挡掉未配对的退出事件)。"""

    def __bool__(self):
        return False

    def __str__(self):
        return "0"


class Engine:
    """事件驱动:按事件顺序喂入,匹配探针 -> 求谓词 -> 执行动作块(每块一个 scratch 作用域)。"""

    def __init__(self, prog):
        self.prog = prog
        self.maps: dict = {}
        self.agg: dict = {}
        self.scopes: list[dict] = [{}]
        self.out: list[str] = []
        self.stopped = False

    # ---- 表达式求值
    def get(self, node, ctx):
        k = node[0]
        if k in ("num", "str"):
            return node[1]
        if k == "var":
            return ctx.get(node[1], 0)
        if k == "field":
            return ctx.get(node[1][1] + "." + node[2], 0)
        if k == "pos":
            return 0
        if k == "scratch":
            for sc in reversed(self.scopes):
                if node[1] in sc:
                    return sc[node[1]]
            return Unset()
        if k == "map":
            key = tuple(self.get(e, ctx) for e in node[2])
            return self.maps.get((node[1], key), Unset())
        if k == "bin":
            return self.binop(node[1], self.get(node[2], ctx), self.get(node[3], ctx))
        if k == "un":
            return (not self.get(node[2], ctx)) if node[1] == "!" else -self.get(node[2], ctx)
        if k == "tern":
            return self.get(node[2], ctx) if self.get(node[1], ctx) else self.get(node[3], ctx)
        raise SyntaxError(f"{node[1]}() 只能出现在聚合赋值右侧或语句位置")

    @staticmethod
    def binop(op, a, b):
        if isinstance(a, Unset):
            a = 0
        if isinstance(b, Unset):
            b = 0
        if op == "==" or op == "!=":
            return (a == b) if op == "==" else (a != b)
        if op == "&&" or op == "||":
            return (bool(a) and bool(b)) if op == "&&" else (bool(a) or bool(b))
        if isinstance(a, str) or isinstance(b, str):
            raise SyntaxError(f"字符串不参与算术运算: {op}")
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a // b if b else 0
        if op == "%":
            return a % b if b else 0
        return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]

    def set_(self, node, value, ctx):
        if node[0] == "scratch":
            self.scopes[-1][node[1]] = value
        elif node[0] == "map":
            self.maps[(node[1], tuple(self.get(e, ctx) for e in node[2]))] = value
        elif node[0] == "var":
            ctx[node[1]] = value
        else:
            raise SyntaxError(f"不可赋值 {node!r}")

    # ---- 语句
    def run_stmt(self, stmt, ctx):
        if stmt[0] == "agg":
            target, fn, args = stmt[1], stmt[2], stmt[3]
            key = (target[1], tuple(self.get(e, ctx) for e in target[2]))
            vals = tuple(self.get(a, ctx) for a in args)
            if any(isinstance(v, Unset) for v in vals):
                return                       # 参数读不到(未配对)时不记账
            if key not in self.agg:
                self.agg[key] = Aggregator(fn, vals)
            self.agg[key].update(vals[0] if vals else None)
            return
        if stmt[0] == "assign":
            self.set_(stmt[1], self.get(stmt[2], ctx), ctx)
            return
        node = stmt[1]
        if node[0] != "call":
            self.out.append(str(self.get(node, ctx)))
            return
        fn, args = node[1], node[2]
        if fn == "printf":
            self.out.append(printf(self.get(args[0], ctx), [self.get(a, ctx) for a in args[1:]]))
        elif fn == "print":
            self.out.append(str(self.get(args[0], ctx)))
        elif fn == "delete":
            # bpftrace 的写法是 delete(@map, key): map 不写方括号,键作为独立实参传入
            m = args[0]
            self.maps.pop((m[1], tuple(self.get(e, ctx) for e in args[1:])), None)
        elif fn == "clear":
            for k in [k for k in self.maps if k[0] == args[0][1]]:
                self.maps.pop(k)
        elif fn == "exit":
            self.stopped = True
        else:
            raise SyntaxError(f"未实现的语句函数 {fn}()")

    # ---- 事件
    def feed(self, probe_name, ctx):
        if self.stopped:
            return
        full = dict(ctx)
        full["probe"] = probe_name
        for specs, predicate, stmts in self.prog:
            if not any(probe_matches(s, probe_name) for s in specs):
                continue
            if predicate is not None and not self.get(predicate, full):
                continue
            self.scopes.append({})
            try:
                for s in stmts:
                    self.run_stmt(s, full)
            finally:
                self.scopes.pop()

    def report(self) -> list[str]:
        lines = []
        for (name, key), agg in sorted(self.agg.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
            lines.append(f"@{name}{key_str(key)}: " + "\n".join(agg.render()))
        for (name, key), v in sorted(self.maps.items(), key=lambda kv: str(kv[0])):
            lines.append(f"@{name}{key_str(key)}: {v}")
        return lines


def key_str(key: tuple) -> str:
    return f"[{', '.join(str(k) for k in key)}]" if key else ""


def printf(fmt: str, vals) -> str:
    out, pos, vi = "", 0, 0
    for m in re.finditer(r"%-?(\d+)?[dsx]", fmt):
        out += fmt[pos:m.start()] + (str(vals[vi]) if m.group(0)[-1] in "ds"
                                     else format(int(vals[vi]), "x"))
        pos, vi = m.end(), vi + 1
    return out + fmt[pos:]


