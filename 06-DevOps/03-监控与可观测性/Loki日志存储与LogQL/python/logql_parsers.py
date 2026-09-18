"""各解析器阶段的实现：json / logfmt / regexp / pattern / unpack。

共同约定：**解析失败不丢行，只往 `__error__` 写标签**（官方原文：「Loki
won't filter out those log lines. Instead they are passed into the next stage
of the pipeline with a new system label named error.」）。
"""

from __future__ import annotations

import json
import re

from logql_ast import (
    JsonParser,
    LogfmtParser,
    PatternParser,
    RegexpParser,
    UnpackParser,
    _compile,
)
from logql_entry import (
    JSON_PARSER_ERR,
    LOGFMT_PARSER_ERR,
    PATTERN_PARSER_ERR,
    REGEXP_PARSER_ERR,
    Entry,
    append_error,
)

__all__ = [
    "stringify",
    "flatten",
    "lookup_path",
    "run_json",
    "run_logfmt",
    "run_regexp",
    "pattern_to_regex",
    "run_pattern",
    "run_unpack",
]


def stringify(value: object) -> str:
    """把 JSON 标量转成标签值。布尔按 true/false、null 按 null 字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    return str(value)


def flatten(obj: dict, prefix: str = "") -> dict[str, str]:
    """把嵌套 JSON 摊平，层级之间用 `_` 连接（Loki json 解析器的默认行为）。"""
    out: dict[str, str] = {}
    for key, val in obj.items():
        name = f"{prefix}_{key}" if prefix else str(key)
        if isinstance(val, dict):
            out.update(flatten(val, name))
        else:
            out[name] = stringify(val)
    return out


def lookup_path(obj: dict, path: str) -> str | None:
    """按点分路径取值，供 `| json dst="a.b"` 形式的表达式使用。"""
    cur: object = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return stringify(cur)


def run_json(entry: Entry, stage: JsonParser) -> None:
    try:
        parsed = json.loads(entry.line)
    except (ValueError, TypeError):
        append_error(entry, JSON_PARSER_ERR)
        return
    if not isinstance(parsed, dict):
        append_error(entry, JSON_PARSER_ERR)
        return
    if stage.expressions:
        for dst, path in stage.expressions:
            got = lookup_path(parsed, path)
            if got is None:
                append_error(entry, JSON_PARSER_ERR)
            else:
                entry.labels[dst] = got
    else:
        entry.labels.update(flatten(parsed))


# 允许前导空白：键值对之间用空格分隔，若正则不允许前导 \s*，
# 解析完第一个键之后就会立刻匹配失败，把整行误判成 LogfmtParserErr。
_LOGFMT_RE = re.compile(r'\s*([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"\\]|\\.)*"|\S*)')


def run_logfmt(entry: Entry, stage: LogfmtParser) -> None:
    line = entry.line
    pos = 0
    found = 0
    while pos < len(line):
        m = _LOGFMT_RE.match(line, pos)
        if m is None:
            if line[pos:].strip():
                append_error(entry, LOGFMT_PARSER_ERR)
                return
            break
        key, raw = m.group(1), m.group(2)
        if raw.startswith('"'):
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if raw != "" or stage.keep_empty:
            entry.labels[key] = raw
        found += 1
        pos = m.end()
    if found == 0 and line.strip():
        append_error(entry, LOGFMT_PARSER_ERR)


def run_regexp(entry: Entry, stage: RegexpParser) -> None:
    # 命名组的合法性已在 RegexpParser 构造期校验过，这里只管取值。
    # 用 search（非锚定）对齐 Go regexp 的默认行为；断言只覆盖「行首即为模式」
    # 的场景，这样锚定与非锚定结果一致，不把未验证的口径写进期望值。
    m = _compile(stage.pattern).search(entry.line)
    if m is None:
        append_error(entry, REGEXP_PARSER_ERR)
        return
    for name, value in m.groupdict().items():
        if value is not None:
            entry.labels[name] = value


_PATTERN_TOKEN = re.compile(r"<([A-Za-z_][A-Za-z0-9_]*)>")


def pattern_to_regex(pattern: str) -> str:
    """把 Loki pattern 语法转成正则。`<_>` 丢弃，`<name>` 捕获。

    命名组用非贪婪 `.+?`；整体加 `^...$` 锚定，保证末尾的捕获组能吃掉余下
    全部内容（官方示例里最后一个占位符就是用来吞掉剩余部分的）。
    """
    parts: list[str] = []
    pos = 0
    for m in _PATTERN_TOKEN.finditer(pattern):
        parts.append(re.escape(pattern[pos : m.start()]))
        name = m.group(1)
        parts.append(f"(?P<{name}>.+?)" if name != "_" else ".+?")
        pos = m.end()
    parts.append(re.escape(pattern[pos:]))
    return "^" + "".join(parts) + "$"


def run_pattern(entry: Entry, stage: PatternParser) -> None:
    m = _compile(pattern_to_regex(stage.pattern)).match(entry.line)
    if m is None:
        append_error(entry, PATTERN_PARSER_ERR)
        return
    entry.labels.update(m.groupdict())


def run_unpack(entry: Entry, _stage: UnpackParser) -> None:
    entry.labels.update(entry.structured)
    entry.structured.clear()
