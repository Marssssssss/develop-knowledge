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


