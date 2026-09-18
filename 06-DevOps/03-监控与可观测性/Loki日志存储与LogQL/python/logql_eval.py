"""管线执行：串联所有阶段，决定一条日志是被丢弃、修改还是打上错误标签。

核心论点：**管线错误不会丢行，而是写进 `__error__` 标签**（官方原文：
「Loki won't filter out those log lines. Instead they are passed into the next
stage of the pipeline with a new system label named error.」按实现用
`__error__`，见 README 分歧说明）。

由此推出两个必须一起理解、否则必然踩坑的结论：

* 想丢掉错误行，必须显式写 `| __error__ = ""`。
* **指标查询不允许携带错误**：范围聚合前只要还残留任何 `__error__`，整条
  查询直接报错，而不是返回 0 或部分结果。这正是官方在 `unwrap` 之后强制
  推荐 `| __error__ = ""` 的原因。
"""

from __future__ import annotations

import re

from logql_ast import (
    JsonParser,
    LabelFilter,
    LabelFormat,
    LineFilter,
    LineFormat,
    LogfmtParser,
    LogQLError,
    PatternParser,
    RegexpParser,
    UnpackParser,
    Unwrap,
    _compile,
    parse_bytes,
    parse_duration,
)
from logql_entry import (  # noqa: F401  —— 统一从本模块 re-export，保持调用方契约
    ERROR_LABEL,
    JSON_PARSER_ERR,
    LOGFMT_PARSER_ERR,
    PATTERN_PARSER_ERR,
    REGEXP_PARSER_ERR,
    SAMPLE_EXTRACTION_ERR,
    UNWRAP_LABEL,
    Entry,
    append_error,
)
from logql_parsers import (
    run_json,
    run_logfmt,
    run_pattern,
    run_regexp,
    run_unpack,
)

__all__ = [
    "Entry",
    "ERROR_LABEL",
    "UNWRAP_LABEL",
    "JSON_PARSER_ERR",
    "LOGFMT_PARSER_ERR",
    "REGEXP_PARSER_ERR",
    "PATTERN_PARSER_ERR",
    "SAMPLE_EXTRACTION_ERR",
    "METRIC_QUERY_ERR",
    "append_error",
    "render_template",
    "run_pipeline",
]

METRIC_QUERY_ERR = "metric query contains errors"

_UNWRAP_HELPERS = {"duration": parse_duration, "bytes": parse_bytes}
_TEMPLATE_RE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")


class _NumericError(Exception):
    """内部信号：数值标签比较失败，转成 __error__ 而不是丢行。"""


def render_template(template: str, labels: dict[str, str]) -> str:
    """极简模板渲染：`{{.label}}` 与 `{{.label | fn arg}}`。

    支持 fn ∈ upper / lower / trim / trunc N / replace "a" "b"。
    不支持 `{{if ...}}` 等控制结构——README 已列为未覆盖子集。
    """

    def _sub(m: re.Match) -> str:
        pieces = [p.strip() for p in m.group(1).split("|")]
        head = pieces[0]
        if not head.startswith("."):
            raise LogQLError(f"不支持的模板表达式: {head!r}")
        text = labels.get(head[1:], "")
        for fn_expr in pieces[1:]:
            parts = fn_expr.split(None, 1)
            fn = parts[0]
            arg = parts[1].strip() if len(parts) > 1 else ""
            if fn == "upper":
                text = text.upper()
            elif fn == "lower":
                text = text.lower()
            elif fn == "trim":
                text = text.strip()
            elif fn == "trunc":
                text = text[: int(arg)]
            elif fn == "replace":
                args = re.findall(r'"([^"]*)"', arg)
                if len(args) != 2:
                    raise LogQLError("replace 需要两个字符串参数")
                text = text.replace(args[0], args[1])
            else:
                raise LogQLError(f"不支持的模板函数: {fn!r}")
        return text

    return _TEMPLATE_RE.sub(_sub, template)


def _cond_true(cond, labels: dict[str, str]) -> bool:
    raw = labels.get(cond.label, "")
    if cond.op in ("=~", "!~"):
        hit = _compile(cond.value).search(raw) is not None
        return hit if cond.op == "=~" else not hit
    if cond.value_kind == "string":
        return raw == cond.value if cond.op == "=" else raw != cond.value
    try:
        got = float(raw)
    except ValueError:
        # 数值比较拿不到数字 —— 官方口径是「不丢行、打错误标签」，所以这里
        # 抛信号让调用方把行放过去，而不是把它过滤掉。
        raise _NumericError(f"{cond.label} 的值 {raw!r} 无法转成数字") from None
    if cond.op == "=":
        return got == cond.numeric
    if cond.op == "!=":
        return got != cond.numeric
    if cond.op == ">":
        return got > cond.numeric
    if cond.op == ">=":
        return got >= cond.numeric
    if cond.op == "<":
        return got < cond.numeric
    return got <= cond.numeric


def _run_label_filter(entry: Entry, stage: LabelFilter) -> bool:
    """返回是否保留该行。"""
    for group in stage.or_groups:
        try:
            if all(_cond_true(c, entry.labels) for c in group):
                return True
        except _NumericError:
            append_error(entry, SAMPLE_EXTRACTION_ERR)
            return True
    return False


def _run_unwrap(entry: Entry, stage: Unwrap) -> None:
    if stage.field is None or stage.field not in entry.labels:
        append_error(entry, SAMPLE_EXTRACTION_ERR)
        return
    raw = entry.labels[stage.field]
    try:
        entry.value = _UNWRAP_HELPERS[stage.converter](raw) if stage.converter else float(raw)
    except (ValueError, LogQLError):
        append_error(entry, SAMPLE_EXTRACTION_ERR)
        return
    # **成功 unwrap 后必须把该标签从标签集里删掉。**
    # 解析器产出的标签会成为指标序列标识的一部分；若 unwrap 出来的标签继续
    # 留在标识里，每个不同取值都会裂出一条独立序列、每条序列只有 1 个样本，
    # avg / stddev / quantile 全部退化成恒等运算。反过来说，
    # `| json | unwrap duration` 之所以能算分位数，正是因为该标签被消费掉了。
    del entry.labels[stage.field]


def run_pipeline(entry: Entry, stages: tuple) -> Entry | None:
    """按顺序跑完管线；返回 None 表示该行被过滤掉。"""
    for stage in stages:
        if isinstance(stage, LineFilter):
            if not stage.accepts(entry.line):
                return None
        elif isinstance(stage, JsonParser):
            run_json(entry, stage)
        elif isinstance(stage, LogfmtParser):
            run_logfmt(entry, stage)
        elif isinstance(stage, RegexpParser):
            run_regexp(entry, stage)
        elif isinstance(stage, PatternParser):
            run_pattern(entry, stage)
        elif isinstance(stage, UnpackParser):
            run_unpack(entry, stage)
        elif isinstance(stage, LabelFilter):
            if not _run_label_filter(entry, stage):
                return None
        elif isinstance(stage, Unwrap):
            _run_unwrap(entry, stage)
        elif isinstance(stage, LineFormat):
            entry.line = render_template(stage.template, entry.labels)
        elif isinstance(stage, LabelFormat):
            for dst, src in stage.assignments:
                if "{{" in src:
                    entry.labels[dst] = render_template(src, entry.labels)
                elif src in entry.labels:
                    entry.labels[dst] = entry.labels[src]
        else:  # pragma: no cover
            raise LogQLError(f"未知管线阶段: {stage!r}")
    return entry
