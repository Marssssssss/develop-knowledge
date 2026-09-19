# DocValues 列式存储：排序、聚合与 global ordinals

## 一、简介

倒排索引解决的是"**哪个词在哪些文档里**"，但排序、聚合、脚本取字段值要的是反方向："**这篇文档的这个字段是什么**"。这个方向靠的是 **DocValues**——索引期构建的一份**磁盘列存**。

本 demo 用纯标准库回答四件事：

1. 两种访问方向为什么必须各存一份；
2. `doc-value-only`（`index: false`）字段能查但为什么慢；
3. 哪些字段能/不能关掉 doc values；
4. 聚合时"段内 ordinal"是怎么映射到"全局 ordinal"并合并成桶的。

## 二、原理详解

### 2.1 两种方向

```text
倒排索引 (inverted) : term → [doc, doc, ...]      ← 搜索
DocValues (列存)    : doc  → term / 数值           ← 排序 · 聚合 · 脚本
```

官方原话：DocValues 是"在**文档索引时**构建的**磁盘**数据结构……它存的是与 `_source` 相同的值，但是**列式**的，对排序和聚合更高效"。

### 2.2 默认值与例外

| 字段类型 | doc values |
| --- | --- |
| 大多数支持的类型（keyword / long / date / ip / geo_point / boolean） | **默认开启** |
| `text` / `match_only_text` | 支持，但**默认不启用** |
| `annotated_text` | **不支持** |
| `wildcard` | **不允许关闭** |
| columnar index 中的字段 | **不允许关闭** |
| `search_as_you_type` 等 | API 响应里能看到，但配了可能无效或报错 |

**doc-value-only 字段**：`index: false` 而 doc values 仍默认开启 ⇒ 还能查，但"查询性能比索引结构慢得多"。官方推荐的适用场景是很少被过滤的字段，比如指标数据里的 gauge / counter。

本 demo 实测这个代价差异：同一个 term 过滤，走倒排只碰 **3** 个文档，走列存要扫 **5** 个（整列）。

### 2.3 global ordinals：跨段聚合的粘合剂

Lucene 的 `SortedDocValues` 在每个**段内**给 term 编号（ordinal）。但一个索引有多个段，同一个 term 在不同段里的 ord 不同。聚合必须先把它们统一到**全局 ordinal 空间**。`OrdinalMap` 就是干这个的，它的类注释写着：

> Maps per-segment ordinals to/from global ordinal space, using a compact packed-ints representation.
> **NOTE**: this is a costly operation, as it must merge sort all terms, and may require non-trivial RAM once done. It's better to operate in segment-private ordinal space instead when possible.

它内部维护三样东西：

- `globalOrdDeltas`：`globalOrd → (globalOrd − 该 term 在**第一个出现它的段**里的 ord)`；
- `firstSegments`：每个 globalOrd 的"第一个出现的段"；
- 每个段一张 `segmentOrd → globalOrd` 表。

举个具体例子：

```text
段 0 字典: [beijing, shanghai]   段 1 字典: [beijing, shenzhen]   段 2 字典: [shenzhen]
全局字典  : [beijing, shanghai, shenzhen]

段 0: ord 0 → 全局 0 , ord 1 → 全局 1
段 1: ord 0 → 全局 0 , ord 1 → 全局 2   ← shenzhen 在全局是 2,不是 1
```

**packed ints**：全局 ordinal 只需 `ceil(log2(全局 term 数))` 位。3 个 term ⇒ 2 位，相对定长 32 位是 16× 的空间节省。这就是 `acceptableOverheadRatio` 参数要权衡的东西。

### 2.4 聚合的执行形状

先在**段内**按 segment ord 计数（这一步快，纯数组自增），再一次性映射到 global ord 累加成全局桶：

```text
段 0: beijing×2, shanghai×1
段 1: beijing×1, shenzhen×1
段 2: shenzhen×1
─────────────────────────────
全局桶: beijing=3  shanghai=1  shenzhen=2   (总和 6 = 参与聚合的文档数)
```

### 2.5 `multi_value: false`（columnar index）

columnar 索引下可以把字段限制成至多一个值。写入多值时**默认直接拒绝**（reject），可以用 `on_failure` 改成其它行为。

## 三、对比：什么时候该动 doc values

| 场景 | 建议 |
| --- | --- |
| 字段只用于搜索、从不排序/聚合/脚本 | 关掉 `doc_values`，省磁盘 |
| 字段只偶尔用于过滤（gauge / counter） | `index: false` 留 doc-value-only |
| 字段要排序/聚合/在 painless 里读 | 保持默认开启 |
| `text` 字段要聚合 | 官方口径是给 `text` 加 `keyword` 子字段（text 默认没有 doc values） |
| 高基数 keyword 聚合变慢/占内存 | 关注 global ordinals 的构建代价（见下） |

## 四、环境

- Python 3.9+（仅标准库）；Go 1.21+；C（C99）。无第三方依赖。

## 五、运行方式

```bash
cd python && python doc_values.py     # 18 条断言，全部实跑通过
cd go     && go run .
cd c      && cc -std=c99 doc_values.c -lm -o a.out && ./a.out
```

> Go 静态检查用 `python _docs/tools/go_sanity.py --spec check=2 <file>`（`check` 为 2 参签名）。

## 六、关键代码

两种访问方向的代价差：

```python
def filter_by_term(inv, dv, term, indexed):
    if indexed:
        return inv.get(term, []), len(inv.get(term, []))   # 只碰命中的文档
    hits = [d for d, t in sorted(dv.items()) if t == term]
    return hits, len(dv)                                    # 必须扫整列
```

global ordinal 的正反映射：

```python
for g, t in enumerate(self.global_terms):
    fs = next(i for i, s in enumerate(self.seg_terms) if t in s)   # firstSegment
    seg_ord = self.seg_terms[fs].index(t)
    self.first_segment[g] = fs
    self.global_ord_deltas[g] = g - seg_ord                        # delta 编码

def global_to_seg(self, g):
    return g - self.global_ord_deltas[g]
```

聚合：段内计数 → 映射累加：

```python
for seg, docs in enumerate(seg_docs):
    local = {}
    for term in docs:
        o = omap.seg_terms[seg].index(term)
        local[o] = local.get(o, 0) + 1
    for o, c in local.items():
        buckets[omap.seg_to_global(seg, o)] += c
```

## 七、性能边界与注意事项

1. **global ordinals 很贵**：官方注释明说要"归并排序全部 term"且"构建完后需要可观的 RAM"。高基数 keyword 字段上的聚合慢/吃内存，多半是它。
2. **能用段内 ordinal 就别上全局**——这是官方给出的优化方向（`It's better to operate in segment-private ordinal space instead when possible`）。
3. **doc values 关掉等于放弃排序/聚合能力**，`text` 尤其注意：它默认就没有。
4. **doc-value-only 不是免费的查询优化**：能查，但过滤要扫整列，只适合"极少用于过滤"的字段。
5. **wildcard 字段与 columnar index 内的字段不能关 doc values**，配置会失败。
6. 本 demo 的"packed bits"只算 `ceil(log2(n))`，没有建模 Lucene packed ints 的分块与 `acceptableOverheadRatio` 的取舍，实际位宽会略高以换取更快的解码。

## 八、参考资料（均为本轮实际读过）

1. `doc_values` — <https://www.elastic.co/guide/en/elasticsearch/reference/current/doc-values.html>
2. `OrdinalMap.java`（段内 ord ↔ 全局 ord、packed ints、代价说明、`globalOrdDeltas` / `firstSegments`）— <https://cdn.jsdelivr.net/gh/apache/lucene@main/lucene/core/src/java/org/apache/lucene/index/OrdinalMap.java>
