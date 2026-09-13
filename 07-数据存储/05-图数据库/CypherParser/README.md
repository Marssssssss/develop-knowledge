# Cypher 子集递归下降解析器

## 简介

**Cypher** 是 Neo4j 的声明式图查询语言，特点是"ASCII-art 模式匹配"：
`(:Person {name:'Tom'})-[:ACTED_IN]->(:Movie)`。
本 demo 实现一个**只读子集**（MATCH / WHERE / RETURN / ORDER BY / SKIP / LIMIT）的递归下降解析器，覆盖：
- 节点模式 `(var:Label {prop: value, ...})`
- 关系模式 `-[:TYPE {props}]->` / `<-` / `-`
- 简单 WHERE 谓词（`<`、`>`、`=`、`<>` 等）
- 投影 `RETURN n.name AS name`

两阶段流水线：**tokenize → AST**，演示 parser 的最小链路（与官方 Cypher 引擎 `cypher-parser.c` 的 `parse_clause` 思路一致）。

## 原理详解

### 1. Token 类型（与 Neo4j 词法层对齐）
参考 Neo4j Cypher Refcard 4.0 与官方文档：

| 类别 | Token 示例 |
|---|---|
| 关键字 | MATCH, WHERE, RETURN, ORDER, BY, SKIP, LIMIT |
| 关系标记 | `-` / `->` / `<-` |
| 模式分隔 | `(`, `)`, `[`, `]`, `{`, `}`, `:`, `,` |
| 比较 | `=`, `<>`, `<`, `>`, `<=`, `>=` |
| 字面量 | STRING, NUMBER, TRUE, FALSE, NULL |

### 2. AST
```
Query {
    match:    Pattern
    where:    Optional[str]   # 简化为原始字符串
    projections: [Projection]
    order_by: Optional[str]
    skip, limit: Optional[int]
    distinct: bool
}
```

### 3. 递归下降 production
```
query     := MATCH pattern [WHERE ...] RETURN projections [ORDER BY k]
             [SKIP n] [LIMIT n]
pattern   := node (rel node)*
node      := '(' [var] [':' Label] ['{' props '}'] ')'
rel       := ('-' | '->' | '<-') ['[' [':' TYPE] [props] ']'] ('->' | '<-' | '-')?
props     := key ':' value (',' key ':' value)*
```

### 4. 执行层（最小模拟）
本 demo 在 `execute_single_node_query` 中给 AST 绑回 `PropertyGraph`：
1. 扫 label（Neo4j 用 label index；本 demo 线性扫）
2. 过滤节点 props
3. 投影出 `n.name` 等字段
4. 应用 LIMIT

> 参考 neo4j-contrib 的 Cypher Recursive-Descent Parser 实现（cypherlite-query crate）：`parse_match_clause → parse_pattern → parse_node_pattern → parse_map_literal` 同款递归。

## 对比

| 解析器实现 | 适用 |
|---|---|
| 手写递归下降（本 demo） | 子集、定制场景 |
| Parboiled / ANTLR | Neo4j 自身曾用 Parboiled（Java PEG 框架） |
| Pratt 解析 | 表达式层（Cypher 表达式含算符优先级） |

## 环境准备

- Python ≥ 3.8（依赖同目录 `property_graph_demo.py`）
- Go ≥ 1.21

## 运行方式

```bash
# Python
cd python && python cypher_parser_demo.py

# Go
cd go && go run cypher_parser_demo.go
```

## 关键代码片段

Python 递归下降 `parse_node`：
```python
def parse_node(self) -> NodePattern:
    self.expect_type(TOK_LPAREN)
    var = None
    if self.peek().type == TOK_IDENT:
        var = self.consume().value
    label = None
    if self.peek().type == TOK_COLON:
        self.consume()
        label = self.consume().value
    props = {}
    if self.peek().type == TOK_LBRACE:
        props = self.parse_props()
    self.expect_type(TOK_RPAREN)
    return NodePattern(var=var, label=label, props=props)
```

Go 同款：
```go
func (p *Parser) ParseNode() (NodePattern, error) {
    if _, err := p.expect(tLParen); err != nil { return NodePattern{}, err }
    np := NodePattern{Props: map[string]any{}}
    if p.peek().Type == tIdent { np.Var = p.consume().Value }
    if p.peek().Type == tColon { p.consume(); np.Label = p.consume().Value }
    if p.peek().Type == tLBrace { props, _ := p.ParseProps(); np.Props = props }
    if _, err := p.expect(tRParen); err != nil { return np, err }
    return np, nil
}
```

## 性能与边界

- **词法**：O(n) 单遍扫描，n 为查询长度。
- **解析**：O(n) 递归下降，无回溯（除 WHERE 子句的 `collect_until`）。
- **子集限制**：仅支持单节点 MATCH + 单关系；不支持 OPTIONAL MATCH、MERGE、WITH、CALL、SET、DELETE、聚合函数、列表推导。
- **错误处理**：缺关键字/缺括号会 `raise SyntaxError`，并指明 pos 偏移。

## 注意事项与常见坑

- **方向标记优先级**：Neo4j 中 `-` 是"无方向关系"，但允许 `[r:TYPE]` 同时带方向后缀 `->` / `<-`，解析时需要支持 `-[r:TYPE]->` 与 `-->` / `<--` 两种风格。
- **变量大小写**：Cypher 关键字不区分大小写（`MATCH` = `match`），但变量区分（`m` ≠ `M`）。
- **节点 props 简写**：`(n:Label {name: 'Tom'})` 中 props 必须为字面量；变量引用（如 `(n:Label {name: $x})`）在本 demo 不支持。
- **递归深度封顶**：参考 CSDN 文章《Cypher 引擎内幕》提到真实引擎会用 `CYPHER_MAX_PARSE_DEPTH=256` 防止恶意嵌套括号耗尽栈空间。

## 参考资料

- [Neo4j Cypher Refcard 4.0](https://neo4j.com/docs/cypher-refcard/4.0/) — MATCH / WHERE / RETURN / WITH / OPTIONAL MATCH 完整语法卡片
- [Neo4j Cheat Sheet 5.x](https://neo4j.com/docs/cypher-cheat-sheet/5/all/) — 全 Cypher 子句速查
- [Cypher 引擎内幕:词法、解析、规划与执行四步走](https://blog.csdn.net/gitblog_00115/article/details/141912125) — 递归下降 + max_depth 256 + 模式 outward/inbound/any 三方向
- [DeepWiki - Cypher Parser and AST Generation](https://deepwiki.com/agentflare-ai/sqlite-graph/4.2-parser-and-ast-generation) — CypherLexer + CypherParser 完整 Token / AST 节点列表