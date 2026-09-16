#!/usr/bin/env python3
"""mini_bpftrace_parse.py — 词法(TOKEN_RX / tokenize)与语法(Parser / parse)。

从 mini_bpftrace.py 拆出,只是为了让单文件落到 300 行以内:
探针说明符展开与分桶在 mini_bpftrace_hist.py,执行引擎在 mini_bpftrace_engine.py。
"""
from __future__ import annotations

import re

from mini_bpftrace_hist import BUILTIN_EVENTS, expand_probe

# ---------------------------------------------------------------- 词法
TOKEN_RX = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<id>[A-Za-z_][A-Za-z_0-9]*)
  | (?P<op>==|!=|<=|>=|&&|\|\||[{}()\[\];,.:?=+\-*/%<>!@$])
""", re.X)


def tokenize(src: str) -> list[tuple[str, str]]:
    toks, pos = [], 0
    while pos < len(src):
        m = TOKEN_RX.match(src, pos)
        if not m:
            raise SyntaxError(f"无法识别的字符 {src[pos]!r} @{pos}")
        pos = m.end()
        if m.lastgroup != "ws":
            toks.append((m.lastgroup, m.group()))
    toks.append(("eof", ""))
    return toks


# ---------------------------------------------------------------- 语法
class Parser:
    """只实现 demo 用得到的子集:表达式 + map 聚合赋值 + 函数调用语句。"""

    LEVELS = [["||"], ["&&"], ["==", "!="], ["<", ">", "<=", ">="], ["+", "-"], ["*", "/", "%"]]

    def __init__(self, toks):
        self.t, self.i = toks, 0

    def peek(self, *vals):
        k, v = self.t[self.i]
        return (k, v) if (k in vals or v in vals) else None

    def take(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, val):
        k, v = self.take()
        if v != val:
            raise SyntaxError(f"期望 {val!r},得到 {v!r}")

    def expr(self):
        return self.ternary()

    def ternary(self):
        c = self.binary(1)
        if self.peek("?"):
            self.take()
            a = self.ternary()
            self.expect(":")
            return ("tern", c, a, self.ternary())
        return c

    def binary(self, lvl):
        if lvl > len(self.LEVELS):
            return self.unary()
        node = self.binary(lvl + 1)
        while True:
            p = self.peek(*self.LEVELS[lvl - 1])
            if not p:
                return node
            self.take()
            node = ("bin", p[1], node, self.binary(lvl + 1))

    def unary(self):
        if self.peek("!", "-"):
            op = self.take()[1]
            return ("un", op, self.unary())
        return self.primary()

    def primary(self):
        k, v = self.take()
        if k == "num":
            return ("num", int(v))
        if k == "str":
            return ("str", v[1:-1].replace('\\n', '\n').replace('\\"', '"'))
        if v == "(":
            e = self.expr()
            self.expect(")")
            return e
        if v == "$":
            k2, v2 = self.take()
            if k2 == "num":
                return ("pos", int(v2))
            if k2 != "id":
                raise SyntaxError(f"$ 后应为名字或序号,得到 {v2!r}")
            return ("scratch", v2)
        if v == "@":
            name = ""
            nxt = self.take()
            if nxt[0] == "id":
                name = nxt[1]
            elif nxt[1] == "[":
                self.i -= 1                      # 无名字 map: @[k] = ...
            else:
                raise SyntaxError(f"@ 后应为 map 名或 [,得到 {nxt[1]!r}")
            keys = []
            if self.peek("["):
                self.take()
                while True:
                    keys.append(self.expr())
                    if not self.peek(","):
                        break
                    self.take()
                self.expect("]")
            return ("map", name, tuple(keys))
        if k == "id":
            if v in BUILTIN_EVENTS:
                return ("str", v.lower())
            if self.peek("("):
                self.take()
                args = []
                if not self.peek(")"):
                    while True:
                        args.append(self.expr())
                        if not self.peek(","):
                            break
                        self.take()
                self.expect(")")
                return ("call", v, tuple(args))
            if self.peek("."):                   # args.filename / ctx.task
                self.take()
                return ("field", ("var", v), self.take()[1])
            return ("var", v)
        raise SyntaxError(f"无法解析的 token {v!r}")


def split_blocks(src: str):
    """按大括号把程序切成 (header, body);header = 'probe[,probe] [/predicate/]'。"""
    blocks, i = [], 0
    while i < len(src):
        if src[i].isspace():
            i += 1
            continue
        j = src.find("{", i)
        if j < 0:
            break
        depth, k = 0, j
        while k < len(src):
            if src[k] == "{":
                depth += 1
            elif src[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        blocks.append((src[i:j].strip(), src[j + 1:k]))
        i = k + 1
    return blocks


def parse_header(header: str):
    """返回 (探针全名列表, 谓词 AST 或 None)。谓词由 header 末尾的一对 / 包裹。"""
    pred = None
    m = re.search(r"/(.+)/\s*$", header)
    if m:
        pred = Parser(tokenize(m.group(1))).expr()
        header = header[:m.start()]
    return [expand_probe(s) for s in header.split(",") if s.strip()], pred


def parse_stmt(p: Parser):
    left = p.expr()
    AGGS = ("count", "sum", "min", "max", "avg", "stats", "hist", "lhist")
    if p.peek("="):
        p.take()
        right = p.expr()
        p.expect(";")
        if right[0] == "call" and right[1] in AGGS:
            return ("agg", left, right[1], right[2])
        return ("assign", left, right)
    p.expect(";")
    return ("call", left)


def parse(src: str):
    prog = []
    for header, body in split_blocks(src):
        specs, predicate = parse_header(header)
        p, stmts = Parser(tokenize(body)), []
        while p.peek("eof") is None:
            stmts.append(parse_stmt(p))
        prog.append((specs, predicate, stmts))
    return prog


