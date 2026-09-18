"""LogQL 子集的手写递归下降解析器。

设计取舍：

* **上下文决定 `!=` 的含义**。同一个 `!=` 在流选择器里是标签不等，在管线的
  `|` 之后紧跟字符串时是「行不包含」，在标签过滤器里是标签不等。词法层不做
  区分（Loki 的 lexer 同样只切一个 token），由语法层按位置判定。
* **`| json` 后的 `a="b"` 是标签过滤器，不是 json 表达式**。json 表达式只在
  `| json` 紧跟 `IDENT = STR` 时解析；一旦遇到 `|` 或比较运算符就走标签过滤器。
* **只支持一层外层聚合**。`sum by (x) (rate(...))` 与 `sum(rate(...)) by (x)`
  两种写法都接受，但 `sum(sum(rate(...)))` 直接报错，不假装支持完整语法。
"""

from __future__ import annotations

from dataclasses import replace

from logql_ast import (
    Condition,
    JsonParser,
    LabelFilter,
    LabelFormat,
    LineFilter,
    LineFormat,
    LogfmtParser,
    LogQLError,
    LogQuery,
    Matcher,
    MetricQuery,
    PatternParser,
    RegexpParser,
    StreamSelector,
    UnpackParser,
    Unwrap,
    classify_number,
    parse_duration,
)
from logql_syntax import Token, tokenize

__all__ = ["parse_query", "Parser", "AGGREGATORS", "LOG_RANGE_FUNCS", "UNWRAP_RANGE_FUNCS"]

# 外层聚合算子。只列已实现的：topk/bottomk 需要额外计数参数（`topk(5, ...)`），
# sort/sort_desc 只影响返回顺序不改分组，都不在覆盖子集内。
AGGREGATORS = frozenset({"sum", "avg", "max", "min", "count", "stddev", "stdvar"})
# 作用于「日志范围向量」（只数行/字节）
LOG_RANGE_FUNCS = frozenset(
    {"rate", "count_over_time", "bytes_rate", "bytes_over_time", "absent_over_time"}
)
# 作用于「unwrap 后的数值范围向量」。注意 count_over_time / bytes_* 不在其中——
# 官方文档的 unwrap 支持列表里没有它们，这是常见误写。
UNWRAP_RANGE_FUNCS = frozenset(
    "sum_over_time avg_over_time max_over_time min_over_time first_over_time "
    "last_over_time stddev_over_time stdvar_over_time quantile_over_time".split()
)
_MATCH_OPS = ("=", "!=", "=~", "!~")
_NUM_OPS = (">", ">=", "<", "<=")


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.toks = tokens
        self.i = 0

    # ------------------------------------------------------------- 基础操作
    def at(self, kind: str, text: str | None = None) -> bool:
        if self.i >= len(self.toks):
            return False
        tok = self.toks[self.i]
        return tok.kind == kind and (text is None or tok.text == text)

    def word(self) -> str:
        tok = self.peek()
        return tok.text if tok is not None and tok.kind == "IDENT" else ""

    def peek(self) -> Token | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self) -> Token:
        if self.i >= len(self.toks):
            raise LogQLError("查询意外结束")
        tok = self.toks[self.i]
        self.i += 1
        return tok

    def expect(self, kind: str, text: str | None = None) -> Token:
        if not self.at(kind, text):
            got = self.peek()
            raise LogQLError(f"期望 {text or kind},实际得到 {got.text if got else '<EOF>'}")
        return self.next()

    def expect_end(self) -> None:
        if self.i != len(self.toks):
            raise LogQLError(f"查询尾部有多余内容: {self.toks[self.i].text!r}")

    # ------------------------------------------------------------- 顶层
    def parse_top(self):
        if self.at("LBRACE"):
            return self.parse_log_query()
        if self.at("IDENT"):
            if self.word() in AGGREGATORS:
                return self.parse_aggregation()
            return self.parse_range_query()
        got = self.peek()
        raise LogQLError(f"查询必须以 '{{' 或聚合函数开头,实际是 {got.text if got else '<EOF>'}")

    def parse_aggregation(self) -> MetricQuery:
        op = self.next().text
        grouping, by = self.parse_optional_grouping()
        self.expect("LPAREN")
        inner = self.parse_top()
        self.expect("RPAREN")
        if grouping is None:
            grouping, by = self.parse_optional_grouping()
        if not isinstance(inner, MetricQuery):
            raise LogQLError("外层聚合的参数必须是范围聚合(如 rate(...))")
        if inner.outer is not None:
            raise LogQLError("只支持一层外层聚合")
        return replace(inner, outer=op, grouping=grouping, by=by)

    def parse_range_query(self) -> MetricQuery:
        func = self.next().text
        if func not in LOG_RANGE_FUNCS and func not in UNWRAP_RANGE_FUNCS:
            raise LogQLError(f"未知的范围聚合函数: {func}")
        self.expect("LPAREN")
        quantile = None
        if func == "quantile_over_time":
            quantile = float(self.expect("NUMBER").text)
            if not 0.0 <= quantile <= 1.0:
                raise LogQLError("quantile_over_time 的 φ 必须落在 [0,1]")
            self.expect("COMMA")
        query = self.parse_log_query()
        window = self.parse_range()
        self.expect("RPAREN")
        grouping, by = self.parse_optional_grouping()
        return MetricQuery(
            query=query, func=func, window_s=window, quantile=quantile, grouping=grouping, by=by
        )

    def parse_optional_grouping(self) -> tuple[str | None, tuple[str, ...]]:
        if self.at("IDENT") and self.word() in ("by", "without"):
            return self.next().text, self.parse_label_list()
        return None, ()

    def parse_range(self) -> float:
        self.expect("LBRACKET")
        window = parse_duration(self.expect("NUMBER").text)
        self.expect("RBRACKET")
        return window

    def parse_label_list(self) -> tuple[str, ...]:
        self.expect("LPAREN")
        names: list[str] = []
        if self.at("RPAREN"):
            self.next()
            return ()
        while True:
            names.append(self.expect("IDENT").text)
            if self.at("COMMA"):
                self.next()
                continue
            break
        self.expect("RPAREN")
        return tuple(names)

    # ------------------------------------------------------------- 日志查询
    def parse_log_query(self) -> LogQuery:
        selector = self.parse_selector()
        stages: list[object] = []
        while True:
            if self.at("BAR_OP"):
                tok = self.next()
                stages.append(LineFilter(tok.text, self.expect("STR").value))
            elif self.at("PIPE"):
                self.next()
                stages.append(self.parse_stage())
            else:
                break
        return LogQuery(selector, tuple(stages))

    def parse_selector(self) -> StreamSelector:
        self.expect("LBRACE")
        matchers: list[Matcher] = []
        while True:
            label = self.expect("IDENT").text
            op = self.next()
            if op.text not in _MATCH_OPS:
                raise LogQLError(f"流选择器里的非法运算符: {op.text!r}")
            matchers.append(Matcher(label, op.text, self.expect("STR").value))
            if self.at("COMMA"):
                self.next()
                continue
            break
        self.expect("RBRACE")
        return StreamSelector(tuple(matchers))

    def parse_stage(self):
        name = self.expect("IDENT").text
        if name == "json":
            return JsonParser(self.parse_json_expressions())
        if name == "logfmt":
            return LogfmtParser()
        if name == "regexp":
            return RegexpParser(self.expect("STR").value)
        if name == "pattern":
            return PatternParser(self.expect("STR").value)
        if name == "unpack":
            return UnpackParser()
        if name == "unwrap":
            return self.parse_unwrap()
        if name == "line_format":
            return LineFormat(self.expect("STR").value)
        if name == "label_format":
            return LabelFormat(self.parse_assignments())
        return self.parse_label_filter(name)

    def parse_json_expressions(self) -> tuple[tuple[str, str], ...]:
        # 只看「IDENT 紧跟 '='」这一种形态。一旦出现 PIPE 或比较运算符，
        # 就说明后面是标签过滤器而不是 json 表达式。
        exprs: list[tuple[str, str]] = []
        while (
            self.at("IDENT")
            and self.i + 1 < len(self.toks)
            and self.toks[self.i + 1].kind == "EQ_OP"
        ):
            dst = self.next().text
            self.next()  # '='
            exprs.append((dst, self.expect("STR").value))
            if self.at("COMMA"):
                self.next()
                continue
            break
        return tuple(exprs)

    def parse_assignments(self) -> tuple[tuple[str, str], ...]:
        out: list[tuple[str, str]] = []
        while True:
            dst = self.expect("IDENT").text
            self.expect("EQ_OP")
            src = self.next().value if self.at("STR") else self.expect("IDENT").text
            out.append((dst, src))
            if self.at("COMMA"):
                self.next()
                continue
            break
        return tuple(out)

    def parse_unwrap(self) -> Unwrap:
        if self.word() in ("duration", "bytes"):
            conv = self.next().text
            self.expect("LPAREN")
            field = self.expect("IDENT").text
            self.expect("RPAREN")
            return Unwrap(field, conv)
        return Unwrap(self.next().text if self.at("IDENT") else None, None)

    # ------------------------------------------------------------- 标签过滤器
    def parse_label_filter(self, first: str | None) -> LabelFilter:
        groups: list[list[Condition]] = [[self.parse_condition(first)]]
        while True:
            if self.at("IDENT") and self.word() == "or":
                self.next()
                groups.append([self.parse_condition(None)])
            elif (self.at("IDENT") and self.word() == "and") or self.at("COMMA"):
                self.next()
                groups[-1].append(self.parse_condition(None))
            else:
                break
        return LabelFilter(tuple(tuple(g) for g in groups))

    def parse_condition(self, first: str | None) -> Condition:
        label = first if first is not None else self.expect("IDENT").text
        op = self.next().text
        if op == "==":
            op = "="
        if op not in _MATCH_OPS and op not in _NUM_OPS:
            raise LogQLError(f"标签过滤器里的非法运算符: {op!r}")
        value_tok = self.next()
        if value_tok.kind == "STR":
            # 字符串字面量：走字符串/正则比较，不做数值化
            return Condition(label, op, value_tok.value, "string", 0.0)
        if value_tok.kind not in ("NUMBER", "IDENT"):
            raise LogQLError(f"标签过滤器的值非法: {value_tok.text!r}")
        # 数值字面量：classify_number 把 1s / 20MB 归一化成秒 / 字节。
        # `status >= 500` 里的 500 是纯数字，classify 返回 "plain"，这是**合法**的；
        # 不能因为"没带单位"就报错（早期版本这里写错，把最常见的数值比较全拒了）。
        kind, numeric = classify_number(value_tok.text)
        return Condition(label, op, value_tok.text, kind, numeric)


def parse_query(text: str):
    """解析一条 LogQL，返回 LogQuery 或 MetricQuery。"""
    parser = Parser(tokenize(text))
    node = parser.parse_top()
    parser.expect_end()
    return node
