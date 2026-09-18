"""PromQL 子集解析器：选择器 / 范围选择器 / offset / @ / 子查询 / 函数调用。

语法与约束严格照官方 "Querying basics"：

* ``offset`` 与 ``@`` 必须**紧跟**选择器。``sum(x) offset 5m`` 非法、
  ``sum(x offset 5m)`` 合法；``@`` 同理。两者可交换书写顺序，**结果相同**
  （offset 相对于 ``@`` 指定的时刻生效）。
* ``@`` 接受 Unix 时间戳浮点值，也接受特殊值 ``start()`` / ``end()``：
  区间查询里解析为查询区间起止；即时查询里两者都解析为求值时刻。
* 负 offset（如 ``offset -1w``）允许查询**看向求值时刻之后**的数据。
* 子查询 ``<instant_query>[<range>:<resolution>]`` 返回范围向量；
  ``resolution`` 省略时取**全局求值间隔**。
* 正则 matcher **完全锚定**：``env=~"foo"`` 等价于 ``env=~"^foo$"``。
* 选择器必须给出指标名，或至少一个**不匹配空值**的 matcher。
"""

import re

from promql_store import Matcher, selector_is_legal

UNITS = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 7 * 86400.0,
    "y": 365 * 86400.0,
}

_DUR_RE = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h|d|w|y)")
_IDENT_RE = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_FUNCS = ("rate", "increase", "delta", "irate", "idelta", "resets", "changes")


class PromQLError(ValueError):
    pass


def parse_duration(text):
    """解析 PromQL 时长字面量，如 ``5m`` / ``1h30m`` / ``250ms``。返回秒数。"""
    text = text.strip()
    total, pos = 0.0, 0
    for m in _DUR_RE.finditer(text):
        if m.start() != pos:
            raise PromQLError("非法时长字面量: %r" % text)
        total += float(m.group(1)) * UNITS[m.group(2)]
        pos = m.end()
    if pos == 0 or pos != len(text):
        raise PromQLError("非法时长字面量: %r" % text)
    return total


def _match_brace(text, start, open_ch, close_ch):
    """返回与 text[start] 处的 open_ch 配对的 close_ch 的下标。"""
    if text[start] != open_ch:
        raise PromQLError("期望 %r，实得 %r" % (open_ch, text[start:start + 1]))
    depth = 0
    for i in range(start, len(text)):
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i
    raise PromQLError("括号不配对: %r" % text[start:])


def _split_top_level(text, sep=","):
    """按顶层分隔符切分（跳过引号与括号内）。"""
    parts, buf, depth, quote = [], [], 0, None
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'`":
            quote = ch
            buf.append(ch)
            continue
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p for p in parts if p.strip()]


def parse_matchers(body):
    """解析 ``{job="a",env=~"b|c"}`` 的花括号内部。"""
    out = []
    for item in _split_top_level(body):
        m = re.match(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!~|!=|=)\s*(.+?)\s*$', item)
        if not m:
            raise PromQLError("非法 matcher: %r" % item)
        label, op, raw = m.group(1), m.group(2), m.group(3)
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'`":
            value = raw[1:-1]
        else:
            raise PromQLError("matcher 的值必须加引号: %r" % item)
        out.append(Matcher(label, op, value))
    return out


class Expr:
    """解析结果：一个选择器（可带函数、范围、修饰符、子查询）。"""

    __slots__ = ("func", "name", "matchers", "range_s", "offset_s", "at",
                 "sub_range", "sub_res")

    def __init__(self):
        self.func = None
        self.name = ""
        self.matchers = []
        self.range_s = None
        self.offset_s = 0.0
        self.at = None
        self.sub_range = None
        self.sub_res = None


def parse(text):
    ex = Expr()
    text = text.strip()

    # 函数调用：只允许后接**子查询后缀**，不允许其它尾随内容
    # （故 `rate(x[5m]) offset 5m` 非法，`rate(x[5m] offset 5m)` 合法）
    sub_tail = None
    m = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", text)
    if m and m.group(1) in _FUNCS:
        ex.func = m.group(1)
        end = _match_brace(text, m.end() - 1, "(", ")")
        sub_tail = text[end + 1:].strip()
        text = text[m.end():end].strip()

    # 子查询后缀：[range:resolution] 或 [range:]（resolution 省略取全局求值间隔）
    m = re.search(r"\[\s*([0-9a-z]+)\s*:\s*([0-9a-z]*)\s*\]\s*$", text)
    if m:
        ex.sub_range = parse_duration(m.group(1))
        ex.sub_res = parse_duration(m.group(2)) if m.group(2) else None
        text = text[:m.start()].strip()

    if sub_tail:
        m2 = re.fullmatch(r"\[\s*([0-9a-z]+)\s*:\s*([0-9a-z]*)\s*\]", sub_tail)
        if not m2:
            raise PromQLError("函数调用后只允许子查询后缀，实得: %r" % sub_tail)
        ex.sub_range = parse_duration(m2.group(1))
        ex.sub_res = parse_duration(m2.group(2)) if m2.group(2) else None

    # 修饰符：offset / @（可重复，顺序无关）
    while True:
        m = re.search(r"\s+offset\s+(-?[0-9a-z.]+)\s*$", text)
        if m:
            ex.offset_s = parse_duration(m.group(1).lstrip("-"))
            if m.group(1).startswith("-"):
                ex.offset_s = -ex.offset_s
            text = text[:m.start()].strip()
            continue
        m = re.search(r"\s*@\s*(start\(\)|end\(\)|-?[0-9.]+)\s*$", text)
        if m:
            raw = m.group(1)
            ex.at = raw if raw in ("start()", "end()") else float(raw)
            text = text[:m.start()].strip()
            continue
        break

    # 选择器：指标名 + 可选 matcher + 可选范围
    m = _IDENT_RE.match(text)
    if m:
        ex.name = m.group(0)
        text = text[m.end():].strip()
    if text.startswith("{"):
        end = _match_brace(text, 0, "{", "}")
        ex.matchers = parse_matchers(text[1:end])
        text = text[end + 1:].strip()
    if text.startswith("["):
        end = _match_brace(text, 0, "[", "]")
        if ":" in text[:end]:
            raise PromQLError("子查询必须跟在完整表达式之后")
        ex.range_s = parse_duration(text[1:end])
        text = text[end + 1:].strip()
    if text:
        raise PromQLError("无法解析剩余内容: %r" % text)
    if not selector_is_legal(ex.name, ex.matchers):
        raise PromQLError("向量选择器必须给出指标名或至少一个不匹配空值的 matcher")
    if ex.func in ("rate", "increase", "delta", "irate", "idelta") and ex.range_s is None:
        raise PromQLError("%s 需要范围向量参数" % ex.func)
    return ex
