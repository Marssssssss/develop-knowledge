"""GitHub Actions 表达式求值器的**上下文 / 函数库 / 语法分析**部分。

与 gha_expr.py 的分工: gha_expr.py 只负责词法与类型语义(官方转换表), 本文件负责
函数库、``*`` 对象过滤器、递归下降解析与 ``evaluate`` 入口。拆自原 395 行的单文件,
以满足 OPTIMIZATION.md §1.1 的"单源文件 ≤ 300 行"硬约束。

权威依据: docs.github.com «Evaluate expressions in workflows and actions»
"""

from __future__ import annotations

import hashlib
import json

from gha_expr import (
    FUNCS,
    ExprError,
    compare,
    loose_eq,
    parse_number,
    to_string,
    tokenize,
    truthy,
)

# ---------------------------------------------------------------- 上下文与函数

def lookup(ctx: dict, name: str):
    """未知上下文本 demo 返回 None(官方在上文中不存在时会报错, 此处放宽以便自检)。"""
    return ctx.get(name)


def _h(x) -> str:
    return hashlib.sha256(x.encode("utf-8")).hexdigest()


def _hash_files(patterns: list, files: dict) -> str:
    """口径说明: 官方未公开 hashFiles 的字节级算法。本 demo 用
    ``sha256(逐个文件的 sha256 十六进制按路径升序拼接)`` 复现其**形状**
    (相同输入恒等、路径无关顺序、内容变则变), 不保证与 GitHub 逐位一致。
    ``files`` 提供 {路径: 内容} 供离线自检。"""
    import fnmatch
    hits = sorted((p, c) for p, c in files.items()
                  if any(fnmatch.fnmatch(p, pat) for pat in patterns))
    if not hits:
        return ""
    return _h("".join(_h(c) for _, c in hits))


def call(name: str, args: list, ctx: dict):
    if name == "contains":
        s, item = args[0], args[1]
        if isinstance(s, list):
            return any(loose_eq(x, item) for x in s)
        return to_string(item).lower() in to_string(s).lower()   # 官方: 不区分大小写
    if name == "startsWith":
        return to_string(args[0]).lower().startswith(to_string(args[1]).lower())
    if name == "endsWith":
        return to_string(args[0]).lower().endswith(to_string(args[1]).lower())
    if name == "format":
        return _format(to_string(args[0]), args[1:])
    if name == "join":
        arr = args[0] if isinstance(args[0], list) else [args[0]]
        sep = to_string(args[1]) if len(args) > 1 else ","
        return sep.join(to_string(x) for x in arr)
    if name == "toJSON":
        return json.dumps(args[0], ensure_ascii=False, indent=2)
    if name == "fromJSON":
        try:
            return json.loads(to_string(args[0]))
        except ValueError:
            return None
    if name == "hashFiles":
        return _hash_files([to_string(a) for a in args], ctx.get("__files__", {}))
    st = ctx.get("__status__", {})
    return {"success": not st.get("any_failed", False),
            "failure": bool(st.get("any_failed", False)),
            "cancelled": bool(st.get("cancelled", False)),
            "always": True}[name]


def _format(tpl: str, vals: list) -> str:
    out, i, n = [], 0, len(tpl)
    while i < n:
        c = tpl[i]
        if c == "{" and i + 1 < n and tpl[i + 1] == "{":
            out.append("{"); i += 2; continue
        if c == "}" and i + 1 < n and tpl[i + 1] == "}":
            out.append("}"); i += 2; continue
        if c == "{":
            j = tpl.index("}", i)
            k = int(tpl[i + 1:j])
            out.append(to_string(vals[k]) if 0 <= k < len(vals) else "")
            i = j + 1
            continue
        out.append(c); i += 1
    return "".join(out)


# ---------------------------------------------------------------- 语法分析

class _Wild(list):
    """``*`` 对象过滤器的中间结果。继承 ``list`` 是为了能直接喂给
    ``join`` / ``contains`` / ``toJSON``;继承后若再跟 ``.prop`` 或 ``[i]``
    则逐个元素映射(故 ``labels.*.name`` 得到名字数组)。"""


def deref(v, name: str):
    if isinstance(v, _Wild):
        return _Wild(deref(x, name) for x in v)
    if isinstance(v, dict):
        return v.get(name)
    return None


def index(v, i):
    if isinstance(v, _Wild):
        return _Wild(index(x, i) for x in v)
    if isinstance(v, dict) and isinstance(i, str):
        return v.get(i)
    if isinstance(v, list):
        if isinstance(i, (int, float)) and not isinstance(i, bool):
            k = int(i)
            return v[k] if 0 <= k < len(v) else None
        for x in v:
            if isinstance(x, dict):
                for name in ("name", "id", "key"):
                    if name in x and loose_eq(x[name], i):
                        return x
    return None


def wildcard(v):
    """``*`` 星号通配: 把数组包成可继续投影的过滤器。"""
    return _Wild(v if isinstance(v, list) else [])


class Parser:
    def __init__(self, toks, ctx):
        self.t, self.i, self.ctx = toks, 0, ctx

    # -- 工具
    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else ("eof", "")

    def eat(self, kind=None, val=None):
        k, v = self.peek()
        if kind and k != kind:
            raise ExprError("期望 %s, 得到 %s" % (kind, k))
        if val and v != val:
            raise ExprError("期望 %s, 得到 %s" % (val, v))
        self.i += 1
        return k, v

    def parse(self):
        node = self.p_or()
        if self.i != len(self.t):
            raise ExprError("表达式末尾有多余 token: %r" % (self.peek(),))
        return node

    # -- 优先级由低到高
    def p_or(self):
        left = self.p_and()
        while self.peek() == ("op", "||"):
            self.eat("op", "||")
            right = self.p_and()
            left = left if truthy(left) else right      # 返回操作数本身
        return left

    def p_and(self):
        left = self.p_cmp()
        while self.peek() == ("op", "&&"):
            self.eat("op", "&&")
            right = self.p_cmp()
            left = right if truthy(left) else left
        return left

    def p_cmp(self):
        left = self.p_rel()
        while self.peek() in (("op", "=="), ("op", "!=")):
            op = self.eat()[1]
            right = self.p_rel()
            eq = loose_eq(left, right)
            left = eq if op == "==" else not eq
        return left

    def p_rel(self):
        left = self.p_unary()
        while self.peek()[0] == "op" and self.peek()[1] in ("<", "<=", ">", ">="):
            op = self.eat()[1]
            left = compare(op, left, self.p_unary())
        return left

    def p_unary(self):
        if self.peek() == ("op", "!"):
            self.eat("op", "!")
            return not truthy(self.p_unary())
        return self.p_postfix()

    def p_postfix(self):
        v = self.p_primary()
        while True:
            if self.peek() == ("op", "."):
                self.eat("op", ".")
                if self.peek() == ("op", "*"):
                    self.eat("op", "*")
                    v = wildcard(v)
                else:
                    v = deref(v, self.eat("ident")[1])
            elif self.peek() == ("op", "["):
                self.eat("op", "[")
                i = self.p_or()
                self.eat("op", "]")
                v = index(v, i)
            else:
                return v

    def p_primary(self):
        k, val = self.peek()
        if k == "str":
            self.i += 1
            return val
        if k == "num":
            self.i += 1
            return parse_number(val)
        if k in ("true", "false", "null"):
            self.i += 1
            return {"true": True, "false": False, "null": None}[k]
        if (k, val) == ("op", "("):
            self.eat("op", "(")
            v = self.p_or()
            self.eat("op", ")")
            return v
        if k == "ident":
            self.i += 1
            if self.peek() == ("op", "("):
                self.eat("op", "(")
                args = []
                if self.peek() != ("op", ")"):
                    args.append(self.p_or())
                    while self.peek() == ("op", ","):
                        self.eat("op", ",")
                        args.append(self.p_or())
                self.eat("op", ")")
                if val not in FUNCS:
                    raise ExprError("未知函数 %s" % val)
                return call(val, args, self.ctx)
            return lookup(self.ctx, val)
        raise ExprError("意外的 token: %r %r" % (k, val))


def evaluate(src: str, ctx: dict):
    """求值一段表达式(GitHub 里 ``${{ }}`` 内部的那部分, 不含包裹标记)。"""
    return Parser(tokenize(src), ctx).parse()
