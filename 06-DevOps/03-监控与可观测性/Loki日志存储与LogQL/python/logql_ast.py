"""LogQL AST 与流选择器匹配语义。

三条容易踩的语义，集中在 matcher 部分实现并注释：

1) **缺失标签 ≡ 空字符串**。与 Prometheus 一致：`{env!="prod"}` 会命中
   「根本没有 env 标签」的流。由此还推出一条反直觉结论：`{env!~".+"}` 也会
   命中缺失标签的流（空串不匹配 `.+`，取反即成立）。
2) **流选择器的 `=~` / `~!` 是完全锚定的**（等价 Python re.fullmatch），
   `{app=~"api"}` 不会命中 `api-server`。
3) **行过滤器的 `|~` / `!~` 不是锚定的**（等价 re.search），`|~ "err"` 会命中
   任何含 err 的行。同一个字母 `~`、同为正则，锚定行为相反 —— 这是 LogQL
   最常被写错的一处，见 README「常见坑」。

单位换算在 logql_units 里，此处 re-export 以保持调用方契约。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

# 异常类**只在 logql_syntax 里定义一份**，这里 re-export。
# 早期版本两个文件各自 class LogQLError(ValueError)，于是词法层抛的异常
# 与语法层抛的异常不是同一个类型，调用方 `except LogQLError` 抓不到。
from logql_syntax import LogQLError  # noqa: F401
from logql_units import (  # noqa: F401
    BYTE_UNITS,
    DURATION_UNITS,
    classify_number,
    parse_bytes,
    parse_duration,
)

__all__ = [
    "BYTE_UNITS",
    "DURATION_UNITS",
    "classify_number",
    "parse_bytes",
    "parse_duration",
    "Matcher",
    "StreamSelector",
    "LineFilter",
    "JsonParser",
    "LogfmtParser",
    "RegexpParser",
    "PatternParser",
    "UnpackParser",
    "LabelFilter",
    "Condition",
    "Unwrap",
    "LineFormat",
    "LabelFormat",
    "PIPELINE_KINDS",
    "LogQuery",
    "MetricQuery",
]


@lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern[str]:
    """带缓存的编译。

    放在模块级而不是 Matcher 内部：Matcher 是 frozen dataclass，在
    `__post_init__` 里给实例属性赋值会抛 FrozenInstanceError。
    """
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise LogQLError(f"非法正则 {pattern!r}: {exc}") from exc


@dataclass(frozen=True)
class Matcher:
    """流选择器里的一个标签匹配条件。"""

    label: str
    op: str  #  =  !=  =~  !~
    value: str

    def __post_init__(self) -> None:
        if self.op not in ("=", "!=", "=~", "!~"):
            raise LogQLError(f"非法匹配运算符: {self.op!r}")
        if self.op in ("=~", "!~"):
            _compile(self.value)  # 提前暴露非法正则，而不是等到匹配时才炸

    def matches(self, labels: dict[str, str]) -> bool:
        candidate = labels.get(self.label, "")  # 缺失标签按空串处理
        if self.op == "=":
            return candidate == self.value
        if self.op == "!=":
            return candidate != self.value
        # 完全锚定：官方文档明确「regex matchers are fully anchored」
        hit = _compile(self.value).fullmatch(candidate) is not None
        return hit if self.op == "=~" else not hit


@dataclass(frozen=True)
class StreamSelector:
    """`{a="1", b=~"x.*"}` —— 所有 matcher 之间是 AND。"""

    matchers: tuple[Matcher, ...]

    def __post_init__(self) -> None:
        if not self.matchers:
            raise LogQLError("流选择器至少要有一个 matcher")

    def matches(self, labels: dict[str, str]) -> bool:
        return all(m.matches(labels) for m in self.matchers)

    def __str__(self) -> str:
        inner = ", ".join(f'{m.label}{m.op}"{m.value}"' for m in self.matchers)
        return "{" + inner + "}"


@dataclass(frozen=True)
class LineFilter:
    """行过滤：|= 包含 / != 不包含 / |~ 正则命中（非锚定）/ !~ 正则不命中。"""

    op: str
    value: str

    def __post_init__(self) -> None:
        if self.op not in ("|=", "!=", "|~", "!~"):
            raise LogQLError(f"非法行过滤运算符: {self.op!r}")

    def accepts(self, line: str) -> bool:
        if self.op == "|=":
            return self.value in line
        if self.op == "!=":
            return self.value not in line
        hit = re.search(self.value, line) is not None  # 注意：search 而非 fullmatch
        return hit if self.op == "|~" else not hit


@dataclass(frozen=True)
class JsonParser:
    """| json [dst='path', ...]；表达式为空表示把整个对象摊平。"""

    expressions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class LogfmtParser:
    strict: bool = False
    keep_empty: bool = False


@dataclass(frozen=True)
class RegexpParser:
    """| regexp "..."。所有捕获组都必须命名 —— 匿名组无法变成标签。

    校验放在构造期（解析时）而不是执行期：写错的查询应当在解析阶段就报错，
    而不是等真的读到日志才发现。
    """

    pattern: str

    def __post_init__(self) -> None:
        compiled = _compile(self.pattern)
        if compiled.groups == 0:
            raise LogQLError("regexp 解析器至少要有一个命名捕获组")
        # compiled.groups 是全部捕获组数，len(groupindex) 只有命名组；
        # 两者不等说明存在匿名组。
        if compiled.groups != len(compiled.groupindex):
            raise LogQLError("regexp 解析器的所有捕获组都必须命名")


@dataclass(frozen=True)
class PatternParser:
    pattern: str


@dataclass(frozen=True)
class UnpackParser:
    """| unpack —— 把 Loki 的 structured metadata 提升为标签。"""


@dataclass(frozen=True)
class Condition:
    """单个标签比较。

    value_kind ∈ string|plain|duration|bytes
      string   —— 右侧是带引号的字面量，走字符串/正则比较
      plain    —— 右侧是纯数字（status >= 500）
      duration —— 右侧带时长单位（duration > 1s），numeric 已归一化为秒
      bytes    —— 右侧带字节单位（bytes > 20MB），numeric 已归一化为字节
    """

    label: str
    op: str
    value: str
    value_kind: str = "string"
    numeric: float = 0.0


@dataclass(frozen=True)
class LabelFilter:
    """标签过滤阶段。

    or_groups 外层是 OR、内层是 AND，对应 `a="1" and b>2 or c="3"` 的优先级
    （and 高于 or）；逗号与 and 等价。写成 `| json | a="1" | b>2` 是多阶段
    串联，同样等价于 AND。
    """

    or_groups: tuple[tuple[Condition, ...], ...]

    def __post_init__(self) -> None:
        if not self.or_groups:
            raise LogQLError("空标签过滤")


@dataclass(frozen=True)
class Unwrap:
    """| unwrap [duration(|bytes(]field[))] —— 把标签转成数值样本。"""

    field: str | None = None
    converter: str | None = None  # None | "duration" | "bytes"


@dataclass(frozen=True)
class LineFormat:
    template: str


@dataclass(frozen=True)
class LabelFormat:
    assignments: tuple[tuple[str, str], ...] = field(default_factory=tuple)


PIPELINE_KINDS = (
    LineFilter,
    JsonParser,
    LogfmtParser,
    RegexpParser,
    PatternParser,
    UnpackParser,
    LabelFilter,
    Unwrap,
    LineFormat,
    LabelFormat,
)


@dataclass(frozen=True)
class LogQuery:
    selector: StreamSelector
    stages: tuple[object, ...] = ()

    def __str__(self) -> str:
        return str(self.selector)


@dataclass(frozen=True)
class MetricQuery:
    """范围聚合，必要时带一层外层聚合。

    func      内层函数（rate / count_over_time / quantile_over_time / ...）
    window_s  范围窗口秒数
    quantile  quantile_over_time 的 φ；其余函数为 None
    outer     外层聚合算子（sum/avg/max/min/count/stddev/stdvar）
    grouping  'by' | 'without' | None
    by        分组标签
    """

    query: LogQuery
    func: str
    window_s: float
    quantile: float | None = None
    outer: str | None = None
    grouping: str | None = None
    by: tuple[str, ...] = ()
