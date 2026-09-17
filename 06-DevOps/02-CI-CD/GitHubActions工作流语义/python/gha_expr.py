"""GitHub Actions 表达式求值器(可执行语义模型)。

权威依据: docs.github.com «Evaluate expressions in workflows and actions»
- 字面量: boolean / null / number / string。字符串**必须**用单引号;字面单引号写成 ``''``;
  用双引号包裹会报错。
- 假值集合: ``false`` / ``0`` / ``-0`` / ``""`` / ``''`` / ``null``;其余(含数组/对象)为真值。
- 字符串比较**忽略大小写**;``==``/``!=`` 是**宽松相等**:类型不同则一律转数字 ——
  null→0、true→1、false→0、字符串按"合法 JSON 数字格式"解析(否则 NaN,空串→0)、数组/对象→NaN。
- 关系运算(``< <= > >=``)任一操作数为 NaN 时结果**恒为 false**。
- 对象与数组**只有同一实例**才相等。
- ``&&`` / ``||`` 返回**操作数本身**而非布尔值(故 ``a || b`` 可作默认值,如
  ``github.head_ref || github.run_id``)。
"""

from __future__ import annotations

import hashlib
import json
import math
import re

_NUM = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|\d+\.\d+(?:[eE][-+]?\d+)?|\d+(?:[eE][-+]?\d+)?|\.\d+)")
_PUNCT = "()[].,!<>*"
_2CH_OPS = ("&&", "||", "==", "!=", "<=", ">=")
FUNCS = ("contains", "startsWith", "endsWith", "format", "join", "toJSON", "fromJSON",
         "hashFiles", "success", "always", "cancelled", "failure")


class ExprError(ValueError):
    """表达式词法/语法错误 —— 在 GitHub 上等同于工作流校验失败。"""


# ---------------------------------------------------------------- 词法

def tokenize(src: str) -> list:
    toks, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
            continue
        if c == "'":
            j, buf = i + 1, []
            while True:
                if j >= n:
                    raise ExprError("字符串未闭合")
                if src[j] == "'":
                    if j + 1 < n and src[j + 1] == "'":   # '' -> 字面单引号
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(src[j])
                j += 1
            toks.append(("str", "".join(buf)))
            i = j + 1
            continue
        if c == '"':
            raise ExprError("字符串必须用单引号;双引号会抛错(官方明确)")
        if c.isdigit() or (c == "-" and i + 1 < n and (src[i + 1].isdigit() or src[i + 1] == ".")):
            m = _NUM.match(src, i)
            if not m:
                raise ExprError("非法数字字面量")
            toks.append(("num", m.group(0)))
            i = m.end()
            continue
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            w = src[i:j]
            toks.append((w, w) if w in ("true", "false", "null") else ("ident", w))
            i = j
            continue
        for op in _2CH_OPS:
            if src.startswith(op, i):
                toks.append(("op", op))
                i += 2
                break
        else:
            if c in _PUNCT:
                toks.append(("op", c))
                i += 1
            else:
                raise ExprError("非法字符 %r" % c)
    return toks


# ---------------------------------------------------------------- 类型语义

def to_number(v) -> float:
    """官方宽松相等的类型转换表。"""
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        if v.strip() == "":
            return 0.0                      # 官方: 空串返回 0
        try:
            n = json.loads(v)
        except ValueError:
            return math.nan
        if isinstance(n, bool) or not isinstance(n, (int, float)):
            return math.nan
        return float(n)
    return math.nan                         # 数组 / 对象 -> NaN


def truthy(v) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v != ""
    return True


def to_string(v) -> str:
    """官方"转字符串"表:null→''、bool→'true'/'false'、数字→十进制(大数用指数)、
    数组/对象**不转换**。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v) if v != int(v) else str(int(v))
    if isinstance(v, str):
        return v
    return ""                               # 数组/对象不转换


def loose_eq(a, b) -> bool:
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()       # 官方: 字符串比较忽略大小写
    if isinstance(a, (list, dict)) or isinstance(b, (list, dict)):
        return a is b                       # 官方: 只有同一实例才相等(此处退化为身份比较)
    if isinstance(a, bool) and isinstance(b, bool):
        return a is b
    return to_number(a) == to_number(b)


def compare(op: str, a, b) -> bool:
    x, y = to_number(a), to_number(b)
    if math.isnan(x) or math.isnan(y):
        return False                        # 官方: NaN 参与关系运算恒 false
    return {"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op]


def parse_number(s: str):
    if s[:2].lower() in ("0x", "-0"):
        neg = s.startswith("-")
        body = s[1:] if neg else s
        if body[:2].lower() == "0x":
            v = int(body, 16)
            return -v if neg else v
    if re.search(r"[.eE]", s):
        return float(s)
    return int(s)


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
