"""日志条目模型与管线错误标签。

单独成文件是为了打断循环依赖：解析器实现（logql_parsers）需要在条目上打
错误标签，而执行器（logql_eval）又需要调用解析器。把 Entry 与标签常量
下沉到最底层，两边都只依赖它。
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Entry",
    "ERROR_LABEL",
    "UNWRAP_LABEL",
    "JSON_PARSER_ERR",
    "LOGFMT_PARSER_ERR",
    "REGEXP_PARSER_ERR",
    "PATTERN_PARSER_ERR",
    "SAMPLE_EXTRACTION_ERR",
    "append_error",
]

ERROR_LABEL = "__error__"
UNWRAP_LABEL = "__unwrap__"

# __error__ 的取值。JSONParserErr 在官方文档里出现过（用作过滤示例），
# 其余常量名来自实现；字符串本身属于实现细节，README 已标注。
JSON_PARSER_ERR = "JSONParserErr"
LOGFMT_PARSER_ERR = "LogfmtParserErr"
REGEXP_PARSER_ERR = "RegexpParserErr"
PATTERN_PARSER_ERR = "PatternParserErr"
SAMPLE_EXTRACTION_ERR = "SampleExtractionErr"


@dataclass
class Entry:
    """一条日志。labels 可变——管线阶段会往里加解析出来的标签。"""

    ts_ns: int
    line: str
    labels: dict[str, str] = field(default_factory=dict)
    structured: dict[str, str] = field(default_factory=dict)
    value: float | None = None

    def copy(self) -> "Entry":
        return Entry(self.ts_ns, self.line, dict(self.labels), dict(self.structured), self.value)


def append_error(entry: Entry, err: str) -> None:
    """给 entry 打上错误标签。

    多个错误如何合并属于实现细节（官方只保证「不会丢行」）。这里采用逗号连接
    累积；demo 的断言只覆盖单错误场景，不依赖这个合并顺序。
    """
    existing = entry.labels.get(ERROR_LABEL)
    entry.labels[ERROR_LABEL] = err if existing is None else f"{existing}, {err}"
