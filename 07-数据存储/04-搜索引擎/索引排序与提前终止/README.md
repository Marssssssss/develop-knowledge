# 索引排序与提前终止（Lucene Sort / SortField / IndexWriterConfig.setIndexSort）

> 问题：搜索结果按"价格升序"排时，Lucene 能不能不等扫完全部命中就停？
> 能 —— 前提是**索引本身**就是按价格存的。本文讲这条链路的判定入口：
> 哪些字段允许做索引排序，以及每个段上"真正生效的主排序字段"是怎么算出来的。
> 全部结论来自 `apache/lucene@main` 源码实读。

## 一、哪些 SortField 能做索引排序

```java
public IndexSorter getIndexSorter() {
  switch (type) {
    case STRING: ... case INT: ... case LONG: ... case DOUBLE: ... case FLOAT: ...
    case CUSTOM: case DOC: case REWRITEABLE: case STRING_VAL: case SCORE:
    default: return null;
  }
}
```

**只有 5 种类型有 IndexSorter**：`STRING / INT / LONG / DOUBLE / FLOAT`。
文档给的理由是"如果这个 SortField 用的是分数或其他**依赖查询**的值，就应该返回 null"。

推论：**`Sort.INDEXORDER`（DOC）和 `Sort.RELEVANCE`（SCORE）都不能拿来做索引排序** ——
按 docid 或按分数排序，索引阶段无从得知。

## 二、setIndexSort：有一个字段不行就整体拒绝

```java
public IndexWriterConfig setIndexSort(Sort sort) {
  for (SortField sortField : sort.getSort()) {
    if (sortField.getIndexSorter() == null) {
      throw new IllegalArgumentException("Cannot sort index with sort field " + sortField);
    }
  }
  this.indexSort = sort;
  this.indexSortFields =
      Arrays.stream(sort.getSort()).map(SortField::getField).collect(Collectors.toSet());
  return this;
}
```

两个要点：

1. **校验在前、赋值在后**，所以被拒绝时 `indexSort` 保持原值，**不会部分生效**。
   `[INT, SCORE]` 这种混合排序会被整体拒绝，而不是"只按 INT 排"。
2. 赋值后额外维护 `indexSortFields`（字段名集合），供后续判断某字段是否参与索引排序。

## 三、Sort 与 SortField 的语义细节

| 事实 | 源码 |
| --- | --- |
| `new Sort()` = `[FIELD_SCORE]` | `public Sort() { this(SortField.FIELD_SCORE); }` |
| `Sort.RELEVANCE = new Sort()` | 同上 |
| `Sort.INDEXORDER = new Sort(SortField.FIELD_DOC)` | 按索引顺序 |
| **显式传空数组**抛异常 | `There must be at least 1 sort field` |
| `needsScores()` 只看 `type == SCORE` | 任一字段是 SCORE 就要算分 |
| `rewrite()` 有变化才返回**新对象** | 否则返回 `this` |
| `hashCode = 0x45aaf665 + Arrays.hashCode(fields)` | 固定偏移 |

> **Python 侧的坑**：`Sort(*fields)` 无法区分"无参调用"和"显式传空数组"——
> 两者都会命中 `len(fields) == 0`。而 Java 里这是两条不同路径（前者给 `[FIELD_SCORE]`、
> 后者抛异常）。本 demo 额外提供 `Sort.from_array([])` 来如实表达后者。

`SortField` 自己的两条校验（`validateField`）：

- `field == null` 且 `type ∉ {SCORE, DOC}` → `field can only be null when type is SCORE or DOC`
- `type == STRING` 且 `missingValue ∉ {null, STRING_FIRST, STRING_LAST}` → 报缺失值非法

## 四、getPrimarySortField：段上真正生效的主排序字段

这是"本次查询能不能靠索引排序提前终止"的判定入口：

```java
public static SortField getPrimarySortField(LeafReader reader) {
  Sort sort = reader.getMetaData().sort();
  if (sort == null) return null;
  for (SortField sf : sort.getSort()) {
    String field = sf.getField();
    if (field == null) return sf;                                  // ① 自定义字段，无从判断
    if (reader.getFieldInfos().fieldInfo(field) == null) continue; // ② 本段无值，排序无效
    DocValuesSkipper skipper = reader.getDocValuesSkipper(field);
    if (skipper != null
        && skipper.docCount() == reader.maxDoc()
        && skipper.minValue() == skipper.maxValue()) continue;     // ③ 全段同值，no-op
    return sf;
  }
  return null;
}
```

三条跳过规则，逐条对应源码注释：

1. `field == null` → **直接返回**（"Custom field that we don't know anything about,
   so return it as primary"）。注意这里是**返回**而不是跳过。
2. 字段在本段没有值（`fieldInfo == null`）→ 跳过，"sorting by it has no effect"。
3. 有 `DocValuesSkipper`、**且覆盖全部文档**（`docCount == maxDoc`）、**且 `min == max`**
   → 全段同一个值，按它排序是 no-op → 跳过。
   源码注释特意提到还要检查 denseness：**缺失值会隐式引入第二个排序分组**，
   所以只有 `docCount == maxDoc`（稠密）时才敢下"同值"的结论。

全部字段都被跳过 → 返回 `null`，即**这个段上没有可用的主排序字段**。

判定结果（排序 `[a:INT, b:STRING]`，`maxDoc=100`）：

| 段的情况 | 主排序字段 |
| --- | --- |
| a、b 都有值 | `a` |
| a 无值 | `b` |
| a、b 都无值 | `None` |
| a 全段同值（skipper 100/100, min=max=7） | `b` |
| a 值有差异（min=1, max=9） | `a` |
| skipper 只覆盖 50/100 | `a`（不敢跳） |
| a、b 都被跳过 | `None` |
| 段无索引排序 | `None` |

## 五、几个相关默认值

| 常量 | 值 |
| --- | --- |
| `IndexWriterConfig.DEFAULT_MAX_FULL_FLUSH_MERGE_WAIT_MILLIS` | `500` |
| `IndexWriterConfig.DEFAULT_RAM_BUFFER_SIZE_MB` | `16.0` |
| `IndexWriterConfig.DEFAULT_MAX_BUFFERED_DOCS` | `-1`（`DISABLE_AUTO_FLUSH`） |

`setMaxFullFlushMergeWaitMillis` 的文档给了三条注意：

- 设为 **0 表示关闭** full flush 时的合并；
- 超时后**不中断**合并，只是"基于已合并的段先提交"；
- 用 `SerialMergeScheduler` 且配了非零超时时，会**一直等到合并结束**，超时不生效。

## 六、本 demo 复现的现象

| 现象 | 验证点 |
| --- | --- |
| 只有 5 种类型能做索引排序 | `STRING/INT/LONG/DOUBLE/FLOAT` 为真，其余为假 |
| SCORE 与 DOC 都不能做索引排序 | `setIndexSort(INDEXORDER/RELEVANCE)` 抛异常 |
| 混合排序整体拒绝，不部分生效 | `[INT, SCORE]` 被拒后 `indexSort` 仍为 `None` |
| `field=None` 只允许 SCORE/DOC | 传 STRING 抛 `field can only be null` |
| STRING 的缺失值白名单 | 非法值抛 `STRING_FIRST or STRING_LAST` |
| 无参与显式空数组是两条路径 | `Sort()` → `[FIELD_SCORE]`；`from_array([])` 抛异常 |
| `rewrite` 无变化返回自身 | `s.rewrite() is s` |
| `needsScores` 只看 SCORE | 混入 SCORE 即为真 |
| 字段无值 → 跳过 | 主排序字段退到 `b` |
| 全段同值且稠密 → 跳过 | skipper `100/100, min=max` |
| skipper 未覆盖全段 → **不敢跳** | `50/100` 仍返回 `a` |
| `field=None` 直接返回而非跳过 | 自定义字段视为主排序字段 |
| 全被跳过 → `None` | 该段无可用主排序字段 |
| full-flush 合并设为 0 即关闭 | `full_flush_merge_enabled` |

## 七、建模口径与限制

- `IndexSorter` 的**实际排序行为**（怎么重排 docid、怎么写出段头）**不还原**；
  本 demo 只断言"哪些类型允许"这个可判定开关，以及 `getPrimarySortField` 的跳过规则。
- 「查询排序 == 索引排序前缀 ⇒ 可提前终止」这条**结论本身**本轮未取到
  `EarlyTerminatingSortingCollector` 源码（该文件在 `main` 分支已不存在），
  因此**未做定量断言**；本文只覆盖它的前置判定入口。

## 八、参考资料（实际读过）

- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/search/SortField.java`（23074 B）
- `lucene/core/src/java/org/apache/lucene/search/Sort.java`（5973 B）
- `lucene/core/src/java/org/apache/lucene/index/IndexWriterConfig.java`（23634 B）

## 九、文件

`python/sortmodel.py`(230) SortField/Sort/setIndexSort/getPrimarySortField ·
`python/selfcheck_sort.py`(183) **66 条断言实跑全绿** · `python/main.py`(84) ·
`go/sortmodel.go`(291) · `go/main.go`(79)。

> Go 侧无本机工具链，走 `bracket_check` / `go_sanity` / `go_crossref` 三项静态检查，全过。
