"""Cypher 子集(只读: MATCH/WHERE/RETURN/ORDER BY/LIMIT)递归下降解析器 —— 纯标准库。

权威来源：
  - Neo4j Cypher Refcard 4.0  https://neo4j.com/docs/cypher-refcard/4.0/
  - Neo4j Cheat Sheet 5.x      https://neo4j.com/docs/cypher-cheat-sheet/5/all/
  - Cypher 引擎内幕:词法、解析、规划与执行四步走
    https://blog.csdn.net/gitblog_00115/article/details/141912125

只读子集语法：
  query   := MATCH pattern [WHERE expr] RETURN projections
             [ORDER BY k] [SKIP n] [LIMIT n]
  pattern := node (rel node)*
  node    := '(' [var] [':' Label] ['{' props '}'] ')'
  rel     := '-' '[' [':' TYPE] [props] ']' ('->' | '<-' | '-')?
  props   := key ':' value (',' key ':' value)*

本 demo 输出 AST，并演示一个最小的"单节点 MATCH"执行器。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "PropertyGraphModel", "python"))
from property_graph_demo import PropertyGraph

# ──────── Token 类型 ────────
TOK_KEYWORD = "KEYWORD"; TOK_IDENT = "IDENT"
TOK_STRING = "STRING";   TOK_NUMBER = "NUMBER"
TOK_BOOL = "BOOL";       TOK_NULL = "NULL"
TOK_LPAREN = "LPAREN";   TOK_RPAREN = "RPAREN"
TOK_LBRACKET = "LBRACKET"; TOK_RBRACKET = "RBRACKET"
TOK_LBRACE = "LBRACE";   TOK_RBRACE = "RBRACE"
TOK_COLON = "COLON";     TOK_DOT = "DOT";     TOK_COMMA = "COMMA"
TOK_ARROW_R = "ARROW_R"; TOK_ARROW_L = "ARROW_L"; TOK_DASH = "DASH"
TOK_OP = "OP";           TOK_EOF = "EOF"

KEYWORDS = {"MATCH", "WHERE", "RETURN", "ORDER", "BY", "SKIP", "LIMIT",
            "AND", "OR", "NOT", "TRUE", "FALSE", "NULL", "AS", "DISTINCT", "OPTIONAL"}

# 单字符 token 字典（避免一连串 if-else 占行）
SINGLE = {"(": TOK_LPAREN, ")": TOK_RPAREN, "[": TOK_LBRACKET, "]": TOK_RBRACKET,
          "{": TOK_LBRACE, "}": TOK_RBRACE, ":": TOK_COLON, ".": TOK_DOT, ",": TOK_COMMA,
          "=": TOK_OP}


@dataclass
class Token:
    type: str; value: Any; pos: int


def tokenize(src: str) -> list[Token]:
    """手写词法分析器：单遍扫描，返回 Token 列表。"""
    toks: list[Token] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1; continue
        if c in SINGLE:
            toks.append(Token(SINGLE[c], c, i)); i += 1; continue
        # 箭头 / DASH
        if c == "-":
            if i + 1 < n and src[i + 1] == "-":
                if i + 2 < n and src[i + 2] == ">": toks.append(Token(TOK_ARROW_R, "->", i)); i += 2; continue
                if i + 2 < n and src[i + 2] == "<": toks.append(Token(TOK_ARROW_L, "<-", i)); i += 2; continue
            toks.append(Token(TOK_DASH, "-", i)); i += 1; continue
        # 比较运算符 <> <= >= < >
        if c == "<":
            if i + 1 < n and src[i + 1] == ">": toks.append(Token(TOK_OP, "<>", i)); i += 2; continue
            if i + 1 < n and src[i + 1] == "=": toks.append(Token(TOK_OP, "<=", i)); i += 2; continue
            toks.append(Token(TOK_OP, "<", i)); i += 1; continue
        if c == ">":
            if i + 1 < n and src[i + 1] == "=": toks.append(Token(TOK_OP, ">=", i)); i += 2; continue
            toks.append(Token(TOK_OP, ">", i)); i += 1; continue
        # 字符串字面量
        if c in ("'", '"'):
            quote = c; i += 1; start = i
            while i < n and src[i] != quote: i += 1
            toks.append(Token(TOK_STRING, src[start:i], start - 1)); i += 1; continue
        # 数字（含负数）
        if c.isdigit() or (c == "-" and i + 1 < n and src[i + 1].isdigit()):
            start = i
            if c == "-": i += 1
            while i < n and (src[i].isdigit() or src[i] == "."): i += 1
            txt = src[start:i]
            num = float(txt) if "." in txt else int(txt)
            toks.append(Token(TOK_NUMBER, num, start)); continue
        # 标识符 / 关键字 / 布尔 / NULL
        if c.isalpha() or c == "_":
            start = i
            while i < n and (src[i].isalnum() or src[i] == "_"): i += 1
            word = src[start:i]; up = word.upper()
            if up in ("TRUE", "FALSE"): toks.append(Token(TOK_BOOL, up == "TRUE", start))
            elif up == "NULL": toks.append(Token(TOK_NULL, None, start))
            elif up in KEYWORDS: toks.append(Token(TOK_KEYWORD, up, start))
            else: toks.append(Token(TOK_IDENT, word, start))
            continue
        raise SyntaxError(f"unexpected char {c!r} at pos {i}")
    toks.append(Token(TOK_EOF, None, n))
    return toks


# ──────── AST 节点 ────────
@dataclass
class NodePattern:
    var: Optional[str]; label: Optional[str]; props: dict[str, Any]

@dataclass
class RelPattern:
    var: Optional[str]; type: Optional[str]; direction: str; props: dict[str, Any]

@dataclass
class Pattern:
    nodes: list[NodePattern] = field(default_factory=list)
    rels: list[RelPattern] = field(default_factory=list)

@dataclass
class Projection:
    expr: str; alias: Optional[str] = None; distinct: bool = False

@dataclass
class Query:
    match: Pattern; where: Optional[str] = None
    projections: list[Projection] = field(default_factory=list)
    order_by: Optional[str] = None
    skip: Optional[int] = None; limit: Optional[int] = None


# 递归下降 Parser
class Parser:
    def __init__(self, toks: list[Token]):
        self.toks = toks; self.i = 0

    def peek(self) -> Token: return self.toks[self.i]
    def consume(self) -> Token:
        t = self.toks[self.i]; self.i += 1; return t

    def expect_kw(self, kw: str) -> Token:
        t = self.peek()
        if t.type == TOK_KEYWORD and t.value == kw: return self.consume()
        raise SyntaxError(f"expected keyword {kw}, got {t.value!r} at pos {t.pos}")

    def expect_type(self, *types: str) -> Token:
        t = self.peek()
        if t.type in types: return self.consume()
        raise SyntaxError(f"expected one of {types}, got {t.type}={t.value!r}")

    # query := MATCH pattern [WHERE ...] RETURN ... [ORDER BY k] [SKIP n] [LIMIT n]
    def parse_query(self) -> Query:
        self.expect_kw("MATCH")
        pat = self.parse_pattern()
        where_txt = None
        if self.peek().type == TOK_KEYWORD and self.peek().value == "WHERE":
            self.consume()
            where_txt = self.collect_until("RETURN", "ORDER", "SKIP", "LIMIT", "EOF")
        self.expect_kw("RETURN")
        proj = self.parse_projections()
        order_by, skip_n, limit_n = None, None, None
        if self.peek().type == TOK_KEYWORD and self.peek().value == "ORDER":
            self.consume(); self.expect_kw("BY")
            order_by = self.collect_until("SKIP", "LIMIT", "EOF")
        if self.peek().type == TOK_KEYWORD and self.peek().value == "SKIP":
            self.consume(); skip_n = self.consume().value
        if self.peek().type == TOK_KEYWORD and self.peek().value == "LIMIT":
            self.consume(); limit_n = self.consume().value
        return Query(match=pat, where=where_txt, projections=proj,
                     order_by=order_by, skip=skip_n, limit=limit_n)

    # pattern := node (rel node)*
    def parse_pattern(self) -> Pattern:
        p = Pattern(); nodes = [self.parse_node()]
        while self.peek().type in (TOK_ARROW_R, TOK_ARROW_L, TOK_DASH):
            rel = self.parse_rel(); nxt = self.parse_node()
            nodes.append(nxt); p.rels.append(rel)
        p.nodes = nodes; return p

    # node := '(' [var] [':' Label] ['{' props '}'] ')'
    def parse_node(self) -> NodePattern:
        self.expect_type(TOK_LPAREN)
        var = self.consume().value if self.peek().type == TOK_IDENT else None
        label = None
        if self.peek().type == TOK_COLON:
            self.consume(); label = self.consume().value
        props: dict[str, Any] = {}
        if self.peek().type == TOK_LBRACE: props = self.parse_props()
        self.expect_type(TOK_RPAREN)
        return NodePattern(var=var, label=label, props=props)

    # rel := ('-'|->|<-) ['[' [':' TYPE] [props] ']' (->|<-)?]
    def parse_rel(self) -> RelPattern:
        if self.peek().type == TOK_ARROW_L: self.consume(); direction = "<-"
        elif self.peek().type == TOK_ARROW_R: self.consume(); direction = "->"
        else: self.consume(); direction = "-"
        rel_type = None; props: dict[str, Any] = {}
        if self.peek().type != TOK_LBRACKET:
            return RelPattern(var=None, type=rel_type, direction=direction, props=props)
        self.consume()
        if self.peek().type == TOK_COLON: self.consume(); rel_type = self.consume().value
        if self.peek().type == TOK_LBRACE: props = self.parse_props()
        self.expect_type(TOK_RBRACKET)
        if self.peek().type == TOK_ARROW_R: self.consume(); direction = "->"
        elif self.peek().type == TOK_ARROW_L: self.consume(); direction = "<-"
        return RelPattern(var=None, type=rel_type, direction=direction, props=props)

    def parse_props(self) -> dict[str, Any]:
        self.expect_type(TOK_LBRACE); props: dict[str, Any] = {}
        if self.peek().type != TOK_RBRACE:
            while True:
                k = self.consume().value; self.expect_type(TOK_COLON)
                props[k] = self.parse_value()
                if self.peek().type == TOK_COMMA:
                    self.consume(); continue
                break
        self.expect_type(TOK_RBRACE); return props

    def parse_value(self) -> Any:
        t = self.peek()
        if t.type in (TOK_NUMBER, TOK_STRING, TOK_BOOL, TOK_NULL):
            return self.consume().value
        raise SyntaxError(f"expected value, got {t.type}={t.value!r}")

    def parse_projections(self) -> list[Projection]:
        projs: list[Projection] = []
        STOP = ("ORDER", "SKIP", "LIMIT", "AS")
        while True:
            parts = []
            while self.peek().type not in (TOK_COMMA, TOK_EOF) and not (
                self.peek().type == TOK_KEYWORD and self.peek().value in STOP):
                parts.append(str(self.consume().value))
            expr = "".join(parts).strip()
            alias, distinct = None, False
            if self.peek().type == TOK_KEYWORD and self.peek().value == "AS":
                self.consume(); alias = self.consume().value
            if expr.upper().startswith("DISTINCT "):
                distinct = True; expr = expr[len("DISTINCT "):].strip()
            projs.append(Projection(expr=expr, alias=alias, distinct=distinct))
            if self.peek().type == TOK_COMMA:
                self.consume(); continue
            break
        return projs

    def collect_until(self, *stop_kws: str) -> str:
        parts = []
        while True:
            t = self.peek()
            if t.type == TOK_EOF: break
            if t.type == TOK_KEYWORD and t.value in stop_kws: break
            parts.append(str(t.value)); self.consume()
        return " ".join(parts).strip()


# 单节点 MATCH 最小执行器
def execute_single_node_query(graph: PropertyGraph, q: Query) -> list[dict[str, Any]]:
    """只支持单节点 MATCH：`(p:Label {props}) RETURN p.field AS name [LIMIT n]`。"""
    np = q.match.nodes[0]
    if np.label is None: raise NotImplementedError("must specify a label")
    bindings = [(nid, dict(graph.nodes[nid].props))
                for nid in graph.find_by_label(np.label)
                if all(graph.nodes[nid].props.get(k) == v for k, v in np.props.items())]
    results = []
    for _, props in bindings:
        row = {}
        for p in q.projections:
            expr = p.expr.strip()
            row[p.alias or expr] = props.get(expr.split(".", 1)[1]) if "." in expr else props
        results.append(row)
    if q.limit is not None: results = results[:q.limit]
    return results


# ──────── 演示 ────────
def build_demo_graph() -> PropertyGraph:
    g = PropertyGraph()
    tom = g.create_node(labels={"Person"}, name="Tom Hanks", born=1956)
    forest = g.create_node(labels={"Movie"}, title="Forrest Gump", released=1994)
    g.create_rel(tom, forest, "ACTED_IN", roles=["Forrest"])
    sally = g.create_node(labels={"Person"}, name="Sally Field", born=1946)
    g.create_rel(sally, forest, "ACTED_IN", roles=["Mrs. Gump"])
    return g


def demo_parse(qstr: str) -> Query:
    print(f"\n=== Query: {qstr} ===")
    print(f"Tokens: {[(t.type, t.value) for t in tokenize(qstr)]}")
    q = Parser(tokenize(qstr)).parse_query()
    print(f"AST.match.nodes = {q.match.nodes}")
    print(f"AST.match.rels  = {q.match.rels}")
    print(f"AST.projections = {q.projections}")
    print(f"AST.where = {q.where!r} order_by={q.order_by!r} skip={q.skip} limit={q.limit}")
    return q


if __name__ == "__main__":
    g = build_demo_graph()
    q1 = demo_parse("MATCH (p:Person) WHERE p.born < 1960 RETURN p.name AS name LIMIT 3")
    print(f"\n执行结果: {execute_single_node_query(g, q1)}")
    print("---")
    q2 = demo_parse("MATCH (m:Movie {released: 1994}) RETURN m.title AS title")
    print(f"\n执行结果: {execute_single_node_query(g, q2)}")