# FST 与前缀补全 suggester（Lucene Completion Suggester）

> 问题：搜索框输入 "cof" 要立刻弹出 "coffee / coffee bean"，还要按权重排序、按文档过滤、去重。
> Lucene 的做法是把所有候选建成一棵 **FST**，前缀查询 = 沿 FST 走到底后做一次受限的 top-N 搜索。
> 本文全部结论来自 `apache/lucene@main` 源码实读。

## 一、为什么是 FST

completion suggester 的索引结构是一个 `FST<Pair<Long, BytesRef>>`：

- **input**：建议词的 UTF-8 字节序列（来自 `ConcatenateGraphFilter` 把整条 token 图拼成一条"串"）
- **output1**：`Long` —— 编码后的**权重**
- **output2**：`BytesRef` —— **payload**，即 `surface + PAYLOAD_SEP + vint(docId)`

之所以用 FST 而不是 HashMap：前缀共享使得整棵结构能压到很小并可 mmap（`OffHeapFSTStore`），
且 `Util.TopNSearcher` 可以在**不遍历全部候选**的前提下取 top-N。

> 建模口径：真实 FST 是带后缀共享的最小化有向无环图，本 demo 用**前缀 trie** 代替。
> 所有被断言的性质（权重编码、payload 布局、topN/queueSize 启发式、去重剪枝、比较器方向）
> 只依赖「路径输出可累加」，与是否最小化无关。

## 二、权重编码：越大越"小"

`NRTSuggester` 里最反直觉的一行：

```java
static long encode(long input) {
  if (input < 0 || input > Integer.MAX_VALUE) {
    throw new UnsupportedOperationException("cannot encode value: " + input);
  }
  return Integer.MAX_VALUE - input;
}
static long decode(long output) { return Integer.MAX_VALUE - output; }
```

`Util.TopNSearcher` 的 `sortComparator` 是 **`Long.compare(o1.output1, o2.output1)`**（升序）。
想让"权重最大"排在最前，就得让它的编码值最小 —— 于是用 `Integer.MAX_VALUE - w` 把序翻过来。

| 权重 w | output1 = encode(w) |
| --- | --- |
| 0 | 2147483647 |
| 10 | 2147483637 |
| 100 | 2147483547 |
| 1000 | 2147482647 |

推论：**权重必须是非负且 ≤ `Integer.MAX_VALUE`**，越界直接 `UnsupportedOperationException`。
这是 suggester 文档里"weight 必须是非负 long 且不能超 int"的由来。

第二个比较器 `ScoringPathComparator` 的写法也值得注意：

```java
int cmp = Float.compare(
    scorer.score((float) decode(second.output1), second.boost),
    scorer.score((float) decode(first.output1),  first.boost));
return (cmp != 0) ? cmp : first.input.get().compareTo(second.input.get());
```

参数是 `(second, first)` —— **反着传**，于是结果是分数降序；同分再按 input（建议词）升序，保证稳定。

## 三、payload 布局：surface + 分隔符 + vint(docId)

```java
static BytesRef make(final BytesRef surface, int docID, int payloadSep) {
  int len = surface.length + MAX_DOC_ID_LEN_WITH_SEP;      // = 6
  ...
  output.writeBytes(surface.bytes, surface.length - surface.offset);
  output.writeByte((byte) payloadSep);
  output.writeVInt(docID);
}
```

- `MAX_DOC_ID_LEN_WITH_SEP = 6`（vint 最多 5 字节 + 1 字节分隔符）
- 切回来用 `parseSurfaceForm`：**找第一个 `payloadSep`**，之前是 surface form，之后 `readVInt` 得 docId
- `payloadSep` 不是硬编码常量，是建索引时写进去的一个 vint
  （`NRTSuggester.load` 里 `int payloadSep = input.readVInt()`）；本 demo 取 `0x1F` 作示例值

vint 是 Lucene 的变长整数：每字节 7 位有效位 + 高位 continuation，最多 5 字节。

| 值 | 字节数 |
| --- | --- |
| 0 / 127 | 1 |
| 128 / 16383 | 2 |
| 16384 | 3 |
| `Integer.MAX_VALUE` | 5 |

第 5 个字节仍有 continuation 位 → 抛 `Invalid vInt (too long)`。

## 四、top-N 与队列容量：两个"拍脑袋"的启发式

`lookup` 开头的三步：

```java
final double liveDocsRatio = calculateLiveDocRatio(scorer.reader.numDocs(), scorer.reader.maxDoc());
if (liveDocsRatio == -1) return;                       // 没有存活文档，直接不做
final List<...> prefixPaths = FSTUtil.intersectPrefixPaths(scorer.automaton, fst);
final int topN = collector.getCountToCollect() * prefixPaths.size();
final int queueSize = getMaxTopNSearcherQueueSize(topN, numDocs, liveDocsRatio, scorer.filtered);
```

三条要点：

1. **`topN` 要乘以相交路径数**。源码注释给的理由：一个 suggestion 若带多个 context，
   FST 里就有多条路径；不乘就会在"match all context"这类查询下**漏掉**同一 suggestion 的其他路径。
   注意 collector 会**提前终止**，所以 topN 只是"最多能收多少"的上界。
2. **`liveDocsRatio = numDocs / maxDocs`**，为 0 时返回 `-1` 表示整个 lookup 直接 return。
   删除越多比率越低，后面队列要相应放大。
3. **`getMaxTopNSearcherQueueSize`** 源码注释自称 *"simple heuristics"*，且**明确写了不保证
   admissibility**（`// search admissibility is not guaranteed`）：

   ```
   maxQueueSize  = topN * maxAnalyzedPathsPerOutput
   maxQueueSize  = maxQueueSize / liveDocsRatio
   if (filterEnabled) maxQueueSize += numDocs / 2
   return min(MAX_TOP_N_QUEUE_SIZE, maxQueueSize)      // MAX = 5000
   ```

   `filterEnabled` 时加 `numDocs/2` 是因为被 filter 拒掉的候选会白占队列名额。
   **队列满了以后最差的会被直接挤掉，所以结果可能不是全局 top-N** —— 这是官方承认的取舍。

## 五、去重：在"部分路径"上就剪枝

`doSkipDuplicates` 为真时，`acceptPartialPath` 会先扫刚加进来的 arc output，
一旦看到 `payloadSep` 就说明 surface form 已经完整，于是拿它去 `seenSurfaceForms` 查重，
**还没走完就整条路径丢掉**（注释特意提醒要缓存 `path.payload`，否则是 O(N²)）。
`acceptResult` 里还会再查一次并登记。

本 demo 复刻的语义：同一 surface 有两条路径（不同 docId / context）时，
不去重出 2 条、去重后只剩分数最高的 1 条且 `pruned == True`。

## 六、分析期常量（源码实读）

| 常量 | 值 | 出处 |
| --- | --- | --- |
| `TokenStreamToAutomaton.POS_SEP` | `0x001F` | 词间分隔符 |
| `TokenStreamToAutomaton.HOLE` | `0x001E` | 位置空洞（停用词留下的缺口） |
| `ConcatenateGraphFilter.SEP_LABEL` | = `POS_SEP` | 同左 |
| `ConcatenateGraphFilter.DEFAULT_PRESERVE_SEP` | `true` | 保留分隔符 |
| `ConcatenateGraphFilter.DEFAULT_PRESERVE_POSITION_INCREMENTS` | `true` | 保留位置增量 |
| `ConcatenateGraphFilter.DEFAULT_MAX_GRAPH_EXPANSIONS` | `10000` | = `Operations.DEFAULT_DETERMINIZE_WORK_LIMIT` |

`CompletionTokenStream` 本质是 `ConcatenateGraphFilter` 加一个 payload 属性：
它把输入 token 流**串成一条带 `SEP_LABEL` 的路径**，`toAutomaton()` 再转成自动机与 FST 求交。
`preserveSep=false` 时相邻词会被直接拼在一起（"coffee bean" → "coffeebean"）；
`preservePositionIncrements=false` 时位置空洞不再体现（效果等价于连续两个 `SEP_LABEL`）。

## 七、本 demo 复现的现象

| 现象 | 验证点 |
| --- | --- |
| 权重越大，编码后的 output1 越小 | `encode(100) < encode(10)` |
| 升序取最小 output1 = 取到最大权重 | 三者排序后首项 |
| 越界权重抛异常 | `encode(-1)` / `encode(INT_MAX+1)` |
| vint 边界 | 127→1 B、128→2 B、16384→3 B |
| 超长 vint 报错 | 第 5 字节仍有 continuation |
| payload 只认**第一个**分隔符 | `a\x1fb` → surface = `a` |
| `numDocs=0` 整个 lookup 直接返回 | `calculate_live_doc_ratio(0,100) == -1` |
| 删除一半 → 队列翻倍 | ratio 0.5 vs 1.0 |
| 开 filter → 再加 `numDocs/2` | 50 → 550 |
| 队列封顶 5000 | 极端 ratio 下仍 ≤ 5000 |
| `topN` 乘以路径数 | 1/2/4 条路径 → 3/6/12 |
| 同权重按建议词字典序 | `apple` 先于 `zebra` |
| 去重只留高分那条 | doc=1 留下，doc=9 被剪 |
| boost 只改分数不改排序 | `boost=2` → 200.0 |

## 八、参考资料（实际读过）

- `apache/lucene@main` — `lucene/suggest/src/java/org/apache/lucene/search/suggest/document/NRTSuggester.java`（15279 B）
- 同目录 `NRTSuggesterBuilder.java`（5569 B）/ `CompletionTokenStream.java`（3385 B）/ `CompletionAnalyzer.java`（5566 B）
- 同目录 `CompletionQuery.java` / `PrefixCompletionQuery.java` / `FuzzyCompletionQuery.java` / `SuggestField.java`
- `lucene/analysis/common/src/java/org/apache/lucene/analysis/miscellaneous/ConcatenateGraphFilter.java`（14954 B）
- `lucene/core/src/java/org/apache/lucene/analysis/TokenStreamToAutomaton.java`（`POS_SEP=0x001f`、`HOLE=0x001e`）
- `lucene/core/src/java/org/apache/lucene/util/automaton/Operations.java`（`DEFAULT_DETERMINIZE_WORK_LIMIT=10000`）
- `lucene/core/src/java/org/apache/lucene/util/fst/FST.java`（42522 B）/ `FSTCompiler.java`（44176 B）/ `PositiveIntOutputs.java`

## 九、文件

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `python/suggester.py` | 268 | 权重编码 / vint / payload / 启发式 / trie / TopNSearcher |
| `python/selfcheck_suggester.py` | 213 | 68 条断言，实跑全绿 |
| `python/main.py` | 96 | 演示入口 |
| `go/suggester.go` | 144 | 编码 / vint / payload / 启发式 |
| `go/searcher.go` | 188 | trie + TopNSearcher + 编排 |
| `go/main.go` | 59 | 演示入口 |

> Go 侧无本机工具链，走 `bracket_check` / `go_sanity` / `go_crossref` 三项静态检查，全过。
