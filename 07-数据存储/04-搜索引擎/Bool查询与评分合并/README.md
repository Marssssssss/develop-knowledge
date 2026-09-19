# bool 查询：occurrence 语义、评分合并与 minimum_should_match

## 一、简介

`bool` 查询是 Elasticsearch 里最常用的复合查询，它**直接映射到 Lucene 的 `BooleanQuery`**。很多线上"为什么这条文档没出来"/"为什么分数是这个数"的问题，本质都在这四件事上：

1. 四类 occurrence（`must` / `should` / `filter` / `must_not`）各自的匹配与计分职责；
2. 分数是怎么合并的（**相加**，不是取最大）；
3. `minimum_should_match` 的默认值和规格串怎么算；
4. 嵌套 `bool` 时，子层 `should` 到底扩不扩结果集。

本 demo 用纯标准库把判定与评分规则跑通（Python 版 **25 条断言实跑全绿**）。

## 二、原理详解

### 2.1 四类 occurrence（官方表格口径）

| occurrence | 是否必须命中 | 是否计分 | 上下文 |
| --- | --- | --- | --- |
| `must` | 是 | **计分** | query context |
| `should` | 否（受 msm 约束） | **计分** | query context |
| `filter` | 是 | **不计分** | filter context（**可被缓存**） |
| `must_not` | 必须**不**命中 | **不计分** | filter context（**可被缓存**） |

由此直接得出三条常用结论：

- 一个文档入选 ⇔ 命中所有 `must` ∩ 所有 `filter`、且**不**命中任何 `must_not`、且命中的 `should` 数 ≥ msm。
- `must_not` 是**一票否决**：本 demo 里 doc3 命中了 `must`，但只要它同时命中 `must_not` 就被排除。
- 只要存在 `must` 或 `filter`，`should` 就**不会把新文档带进结果集**（默认 msm=0，见 2.3）——它只剩加分作用。

### 2.2 评分：more-matches-is-better，求和

官方文档原话：*"The bool query takes a more-matches-is-better approach, so the score from each matching `must` or `should` clause will be added together."*

```text
must:title:es   doc1=1.0  doc2=2.0  doc3=0.5
should:tag:new  doc1=0.3  doc4=0.3
─────────────────────────────────────────────
doc1 = 1.0 + 0.3 = 1.3        ← 两个子句都命中
doc2 = 2.0                    ← 只命中 must
```

三个等价写法的分数差异（官方 "Scoring with bool.filter" 一节）：

| 写法 | `_score` |
| --- | --- |
| `bool.filter` 单用 | 全 **0** |
| `bool.must: match_all` + `bool.filter` | 全 **1.0** |
| `constant_score` 包同样的 filter | 与上一行**完全等效** |

### 2.3 minimum_should_match

**默认值规则**（最容易踩）：

- 有 ≥1 个 `should` 且**没有 `must` 也没有 `filter`** ⇒ 默认 **1**；
- 否则 ⇒ 默认 **0**。

**规格串**（`minimum_should_match` 参数）：

| 类型 | 例子 | 含义 |
| --- | --- | --- |
| 整数 | `3` | 固定 3 条 |
| 负整数 | `-2` | 总数 − 2 |
| 百分比 | `75%` | **向下取整** |
| 负百分比 | `-25%` | 可缺失 25%：总数 − floor(25% × 总数) |
| 组合 | `3<90%` | ≤3 条时全要；>3 条时按 90% |
| 多重组合 | `2<-25% 9<-3` | 分段生效，只对"大于前一段阈值"的数量有效 |

两个反直觉点（官方 NOTE，本 demo 都有断言覆盖）：

```text
4 个子句: 75% ⇒ 3 ,  -25% ⇒ 3      ← 一样
5 个子句: 75% ⇒ 3 ,  -25% ⇒ 4      ← 不一样
```

```text
2<-25% 9<-3  ⇒ n=1,2,3,9,10,12 → 1,2,3,7,7,9
```

**兜底规则**：算出来的值会被 clamp 到 `[1, 可选子句数]`；而且文档明确写道——"若计算结果表明不需要任何可选子句，`BooleanQuery` 的通常规则仍然生效：**不含 required 子句的 `BooleanQuery` 仍须匹配至少一个 optional 子句**"。本 demo 用显式 `msm="0%"` 验证这条兜底真的会生效。

### 2.4 嵌套 bool

`(user.id=kimchy OR user.id=banon) AND tags=production` 必须写成 `must` 里套一个 `bool.should`。**放在 `must` 之下的 `should` 只提升分数、不新增文档**；顶层 `should` 同理。

## 三、对比：must vs filter 怎么选

| 场景 | 用哪个 | 理由 |
| --- | --- | --- |
| 参与相关性排序的条件 | `must` | 计分 |
| 纯结构化筛选（状态、时间窗、租户 id） | `filter` | 不计分 + **结果可缓存**，重复查询更快 |
| 排除条件 | `must_not` | filter context，同样可缓存 |
| 可选加分项（同义词、标题命中） | `should` | 命中越多分越高 |

## 四、环境

- Python 3.9+（仅标准库）；Go 1.21+；C（C99）。无第三方依赖。

## 五、运行方式

```bash
cd python && python bool_query.py     # 25 条断言，全部实跑通过
cd go     && go run .
cd c      && cc -std=c99 bool_query.c -lm -o a.out && ./a.out
```

> Go 静态检查用 `python _docs/tools/go_sanity.py --spec check=2 <file>`（本 demo 的 `check` 是 2 参签名）。

## 六、关键代码

匹配判定（三类约束 + msm）：

```python
def matches(self, doc):
    for c in self.clauses:
        if c.occur in REQUIRED and doc not in c.hits:   # must / filter
            return False
        if c.occur == MUST_NOT and doc in c.hits:       # 一票否决
            return False
    n_should_hit = sum(1 for c in self.clauses
                       if c.occur == SHOULD and doc in c.hits)
    return n_should_hit >= self.effective_msm()
```

评分（只有 must/should 参与，且是求和）：

```python
SCORING = (MUST, SHOULD)

def score(self, doc):
    if not self.matches(doc):
        return 0.0
    return sum(c.score(doc) for c in self.clauses if c.occur in SCORING)
```

msm 组合语法的核心：取"阈值 < n 的最后一段"。

```python
chosen = None
for p in parts:                      # 例如 ["2<-25%", "9<-3"]
    if "<" in p:
        thr, tail = p.split("<", 1)
        if n_opt > int(thr):
            chosen = tail
    else:
        chosen = p
v = n_opt if chosen is None else _apply_one(chosen, n_opt)
```

## 七、性能边界与注意事项

1. **`filter` 的收益来自缓存**：`must` 与 `should` 官方明确说"结果不会被缓存"，重复查询拿不到加速。把不变的结构化条件从 `must` 挪到 `filter` 是零风险优化。
2. **mv `should` + `must` 混用时极易踩默认 msm=0**：你以为"至少匹配一个 should"，实际一个都不匹配也能进结果集（因为 `must` 已经框住了）。要强制就必须显式写 `minimum_should_match`。
3. **`must_not` 不看分数**：它只做集合剔除，不会因为"负面程度"影响排序。
4. **百分比向下取整**导致小子句数时非常激进：`-25%` 在 4 子句内等价于"全要"。子句数会随业务动态变化时，优先用整数而不是百分比。
5. 官方不建议过度嵌套："nested bool 可能导致复杂且慢的查询，尽量保持扁平"。
6. 本 demo 的子句分数是给定的常量表，不是真实 BM25；重点是**合并规则**而非打分函数本身。

## 八、参考资料（均为本轮实际读过）

1. Boolean query — <https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl-bool-query.html>
2. `minimum_should_match` parameter — <https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl-minimum-should-match.html>
3. `BooleanQuery.java`（bool query → Lucene `BooleanQuery` 的映射）— <https://cdn.jsdelivr.net/gh/apache/lucene@main/lucene/core/src/java/org/apache/lucene/search/BooleanQuery.java>
