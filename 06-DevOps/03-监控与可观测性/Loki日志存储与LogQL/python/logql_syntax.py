"""LogQL 子集的词法分析器（纯 Python，无第三方依赖）。

覆盖范围（与 README「本节支持的语言子集」一致）：
  - 流选择器 {label="v", ...}，四运算符 = != =~ !~
  - 管线阶段：行过滤 |= != |~ !~、解析器 json/logfmt/regexp/pattern/unpack、
    标签过滤（数值比较 + duration/bytes 单位）、unwrap、line_format、label_format
  - 范围聚合 rate/count_over_time/... 与 sum by(...) 形式的外层聚合

不覆盖：with() 表达式、注释(#)、@ start/end 时间修饰符、offset、多行字符串。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["LogQLError", "Token", "tokenize", "unquote"]

# 词法规则。顺序即优先级，必须保证「长运算符先于其前缀」：
#   |= |~ !~ !=  先于  |          （否则 `|=` 会被切成 `|` + `=`）
#   =~           先于  =
#   >= <= ==     先于  > < =
_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<lbrace>\{)
  | (?P<rbrace>\})
  | (?P<lparen>\()
  | (?P<rparen>\))
  | (?P<lbracket>\[)
  | (?P<rbracket>\])
  | (?P<comma>,)
  | (?P<bar_op>\|~|\|=|!~|!=)
  | (?P<pipe>\|)
  | (?P<re_op>=~)
  | (?P<cmp_op>>=|<=|==|>|<)
  | (?P<eq_op>=)
  | (?P<str>`[^`]*`|"(?:[^"\\]|\\.)*")
  | (?P<number>-?\d+(?:\.\d+)?[a-zA-Zµ]*)
  | (?P<ident>[a-zA-Z_][a-zA-Z0-9_.]*)
    """,
    re.VERBOSE,
)

_KIND_MAP = {name: name.upper() for name in _TOKEN_RE.groupindex}


class LogQLError(ValueError):
    """LogQL 语法错误。消息里带上出错位置，便于定位。"""


@dataclass(frozen=True)
class Token:
    """一个词法单元。

    text  原始文本（含引号）
    value 仅对字符串字面量有意义：去引号并解转义后的内容
    pos   在原查询串中的起始偏移
    """

    kind: str
    text: str
    pos: int
    value: str = ""


_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    '"': '"',
}


def _hex_escape(body: str, start: int, width: int) -> tuple[str, int]:
    digits = body[start : start + width]
    if len(digits) < width or any(c not in "0123456789abcdefABCDEF" for c in digits):
        raise LogQLError(f"\\x/\\u 转义需要 {width} 位十六进制数字")
    return chr(int(digits, 16)), start + width


def unquote(text: str) -> str:
    """去掉引号。

    反引号是「原始字符串」：Loki 文档明确说反引号内的内容不做转义处理
    （这正是文档推荐用反引号写正则的原因）。

    双引号走 Go 的 `strconv.Unquote` 语义：**非法转义序列直接报错**。
    所以 `| regexp "(?P<m>\\w+)"` 是错的（`\\w` 不是合法转义），必须写成
    `"(?P<m>\\\\w+)"` 或用反引号。很多教程把这一步抄漏，导致复制过去就报
    parse error —— 这里刻意保真，不静默吞掉反斜杠。
    """
    if text.startswith("`"):
        return text[1:-1]
    body = text[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(body):
            raise LogQLError("字符串以孤立的反斜杠结尾")
        nxt = body[i + 1]
        if nxt in _ESCAPES:
            out.append(_ESCAPES[nxt])
            i += 2
        elif nxt in "01234567":  # 八进制，最多 3 位
            j = i + 1
            while j < len(body) and j < i + 4 and body[j] in "01234567":
                j += 1
            out.append(chr(int(body[i + 1 : j], 8)))
            i = j
        elif nxt == "x":
            char, i = _hex_escape(body, i + 2, 2)
            out.append(char)
        elif nxt in "uU":
            char, i = _hex_escape(body, i + 2, 4 if nxt == "u" else 8)
            out.append(char)
        else:
            raise LogQLError(
                f"非法的转义序列 \\{nxt}：Go 的 strconv.Unquote 会拒绝它。"
                f"写正则请改用反引号，或把反斜杠写成 \\\\{nxt}"
            )
    return "".join(out)


def tokenize(text: str) -> list[Token]:
    """把查询串切成 Token 列表。空白被丢弃，不产生 Token。"""
    tokens: list[Token] = []
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN_RE.match(text, pos)
        if m is None:
            raise LogQLError(f"无法识别的字符 {text[pos]!r} @ {pos}")
        kind = m.lastgroup
        assert kind is not None
        raw = m.group()
        pos = m.end()
        if kind == "ws":
            continue
        token_kind = _KIND_MAP.get(kind, kind.upper())
        value = unquote(raw) if token_kind == "STR" else ""
        tokens.append(Token(token_kind, raw, m.start(), value))
    return tokens
