# 高亮器与摘要片段生成（Lucene UnifiedHighlighter）

> 问题：命中了一篇文档，怎么从里面挑几句话、加粗关键词、拼成搜索结果页那段摘要？
> Lucene 的做法是**把每个候选片段当成一篇小文档再打一次分**，然后择优、格式化。
> 本文全部结论来自 `apache/lucene@main` 源码实读，不依赖二手博客。

## 一、整体链路

```
字段元信息 ──> OffsetSource 判定 ──> 取偏移流 OffsetsEnum
                                          │
正文 ──> BreakIterator 切句 ──> 按命中位置对齐片段边界
                                          │
                                    Passage（候选片段）
                                          │
                              PassageScorer 打分（mini-BM25）
                                          │
                          容量为 maxPassages 的小顶堆择优
                                          │
                              PassageFormatter 格式化
```

`FieldHighlighter.highlightFieldForDoc` 一行串起来：空正文直接返回 `null`；
切不出 passage 就退化成 `getSummaryPassagesNoHighlight`（取前 N 句当摘要）；
有 passage 才交给 formatter。

## 二、偏移源：先决定「去哪儿拿偏移」

`UnifiedHighlighter.OffsetSource` 五态，**判定顺序就是优先级**（`getOffsetSource`）：

| 条件 | OffsetSource |
| --- | --- |
| `IndexOptions == DOCS_AND_FREQS_AND_POSITIONS_AND_OFFSETS` 且**有**词向量 | `POSTINGS_WITH_TERM_VECTORS` |
| `IndexOptions == DOCS_AND_FREQS_AND_POSITIONS_AND_OFFSETS` 且无词向量 | `POSTINGS` |
| 索引选项不满足、但 `hasTermVectors()` | `TERM_VECTORS` |
| 都没有 | `ANALYSIS`（重新跑一遍分析器） |

然后 `getOptimizedOffsetSource` 还会按**查询形态**再调一次，两处反直觉：

- `POSTINGS` 一旦遇到**需要扫全部 term** 的查询（多词查询 / 短语改写 / 无法识别的查询片段），
  会**降级**成 `ANALYSIS` —— 因为 postings 只按 term 取，扫不动通配符。
- `POSTINGS_WITH_TERM_VECTORS` 若确认用不上词向量，会**升级**成 `POSTINGS`。
- 命中集合为空且无需改写 → `NONE_NEEDED`，连偏移都不取。

> 实践结论：**只建 offsets 不建词向量，遇上前缀/通配符查询仍然会退化到重分析**。
> 想让 unified highlighter 稳定走 postings，得管住查询形态。

## 三、片段边界：从命中「中点」往两侧找句子边界

`highlightOffsetsEnums` 的核心循环（源码口径，逐条对应）：

1. `startOffset == -1` → 直接抛 `field 'x' was indexed without offsets, cannot highlight`。
2. **命中横跨正文末尾**（`start < contentLength && end > contentLength`）→ 整条丢弃，
   既不参与切分也不计分。
3. `start >= passage.getEndOffset()` 时开新片段。初值 `endOffset == -1`，所以第一条命中必然开片段。
4. 用**命中中点**对齐，让片段长度更接近目标：
   ```java
   final int center = start + (end - start) / 2;
   passage.setStartOffset(Math.min(start,
       Math.max(breakIterator.preceding(Math.max(start + 1, center)), lastPassageEnd)));
   lastPassageEnd = Math.max(end,
       Math.min(breakIterator.following(Math.min(end - 1, center)), contentLength));
   ```
   注意 `Math.max(..., lastPassageEnd)`：**片段之间不会重叠**，新片段左边界被上一片段的右边界顶住。
5. 右边界用 `Math.min(..., contentLength)` 夹住，永远不越界。

### LengthGoalBreakIterator：min 与 closest 的分歧

`fragmentAlignment ∈ [0,1]`（越界抛异常）决定命中落在片段里的相对位置；
`isMinimumLength` 决定选断点的方式：

- `createMinLength` → **永不欠冲**：`preceding` 取目标**左侧**的断点，`following` 取目标**右侧**的断点，
  片段只会偏长不会偏短。
- `createClosestToLength` → **取最近**：比较 `afterIdx - targetIdx` 与 `targetIdx - beforeIdx`，
  谁近取谁，但还有个附加条件（`afterIdx < matchStartIndex` / `beforeIdx > matchEndIndex`）——
  选中的断点不能越过命中本身。

实测分歧（边界集 `{0,10,20,30,40,50,60}`，`lengthGoal=10`，`alignment=0.5`）：

| 调用 | minLength | closestToLength |
| --- | --- | --- |
| `preceding(23)` | 10 | **20** |
| `following(5)` | 20 | **10** |

## 四、打分：把片段当小文档的 mini-BM25

`PassageScorer` 默认 `k1=1.2, b=0.75, pivot=87`（源码注释：87 是英语句子典型长度）。
源码自己写了 `TODO: this formula is completely made up`，**数值没有"正确答案"**，
但每一步的舍入是确定的，本 demo 在每个 float 边界都显式 round 到 binary32，便于和 JVM 对拍。

```
score(passage, contentLength) = norm(startOffset) * Σ_terms [ tf(freqInPassage, passageLen)
                                                            * weight(contentLength, freqInDoc) ]
```

- `weight(contentLength, ttf) = (k1+1) * log(1 + (numDocs + 0.5) / (ttf + 0.5))`
  其中 **`numDocs = 1 + contentLength / pivot`** —— 用正文长度近似文档数，这是最"凑"的一步。
- `tf(freq, passageLen) = freq / (freq + k1 * ((1-b) + b * (passageLen / pivot)))`
- `norm(start) = 1 + 1 / log(pivot + start)` —— 越靠前的片段越像摘要。

三条容易踩的口径：

1. 同一个 term 在片段内出现多次，**只算一个 term**，但 `tf` 用的是片段内的**出现次数**；
   `freqInDoc` 取**第一次遇到**该 term 时记录的值（源码里 `termIndex < 0` 才写入）。
2. `passage.getLength()` 是 `endOffset - startOffset`，和 `getNumMatches()` 无关。
3. `norm` 里 `log(pivot + start)`，`start=0` 时是 `log(87)`；pivot 改小会让 `log` 迅速趋近 0、
   `norm` 爆炸 —— 别把 pivot 设到接近 1。

## 五、择优：容量固定的小顶堆

```java
PriorityQueue<Passage> passageQueue = new PriorityQueue<>(
    Math.min(64, maxPassages + 1),
    (l, r) -> l.getScore() != r.getScore()
        ? Float.compare(l.getScore(), r.getScore())
        : l.getStartOffset() - r.getStartOffset());
```

`maybeAddPassage` 的三分支（本 demo 完整复刻）：

- `startOffset == -1` → 空片段，直接忽略；
- 堆已满且新片段**分数低于堆顶**（当前最差）→ `passage.reset()`，**对象复用**；
- 否则入堆；入堆后若超容 → `poll()` 弹出堆顶并 `reset()` 复用。

所以运行期 `Passage` 实例数 ≤ `maxPassages + 1`，堆容量硬上限 64。
比较器同分时按 `startOffset` 升序，保证输出稳定。
最终 `Arrays.sort(passages, passageSortComparator)` 默认按**偏移量**排（不是按分数），
这样摘要读起来是原文的先后顺序。

## 六、格式化：重叠命中合并与省略号

`DefaultPassageFormatter` 默认 `<b>` / `</b>` / `"... "` / `escape=false`。三个细节：

1. **只在片段之间不相连时插省略号**：`if (!sb.isEmpty() && passage.getStartOffset() != pos)`。
2. **命中之间可能重叠**：向前吞掉所有 `matchStarts[i+1] < end` 的命中，取最大的 `end`，
   避免嵌套标签。实测 `abcd[0-4]` + `cdef[2-6]` → `<b>abcdef</b>gh`。
3. **命中可能越出片段**：`end = Math.min(end, passage.getEndOffset())` 截断
   （分析器产生的 term 有可能跨句）。

`escape=true` 时按 owasp 规则只转义 `& < >` 三个字符。

## 七、本 demo 复现的现象

| 现象 | 验证点 |
| --- | --- |
| `POSTINGS` 遇多词查询降级为 `ANALYSIS` | `get_optimized_offset_source(POSTINGS, True, [...]) == ANALYSIS` |
| 命中为空 → `NONE_NEEDED`，连偏移都不取 | 同上，terms 为空时 |
| minLength 与 closestToLength 选到不同断点 | `preceding(23)`：10 vs 20 |
| 片段不重叠：左边界被上一片段右边界顶住 | 三段 `[0,18) [18,38) [38,54)` |
| 命中横跨正文末尾被整条丢弃 | 只有一条 passage 返回 |
| 没按 offsets 索引 → 抛异常 | 含 `was indexed without offsets` |
| `maxPassages=1` 只留最高分片段 | 留下 `[38,54)` |
| 重叠命中合并成一个加粗区间 | `<b>abcdef</b>gh` |
| 越出片段的命中被截断 | `<b>abcd</b>` |
| 兜底摘要取前 N 句且无命中 | `[(0,18),(18,38)]`，`numMatches()==0` |

## 八、与 FVH / plain highlighter 的差异（口径说明）

- **FVH（FastVectorHighlighter）** 必须**预先存词向量含 positions+offsets**，查询期不再分析；
  优点是快，代价是索引体积。源码注释明确说 `TERM_VECTORS` 分支"无法在此检查词向量是否真的带 offsets，
  没有的话后面会抛异常"。
- **UnifiedHighlighter** 可以退到 `ANALYSIS`（重新分析正文），因此**不强制存词向量**，
  代价是 CPU。这也是 `POSTINGS → ANALYSIS` 降级成立的前提。
- 本文所有数值（k1/b/pivot/maxLength 10000/默认标签）均取自上述源码文件，
  **未做官方未给出的定量推断**。

## 九、参考资料（实际读过）

- `apache/lucene@main` — `lucene/highlighter/src/java/org/apache/lucene/search/uhighlight/UnifiedHighlighter.java`（62493 B）
- 同目录 `PassageScorer.java`（4804 B）/ `Passage.java`（6384 B）/ `FieldHighlighter.java`（8075 B）
- 同目录 `LengthGoalBreakIterator.java`（7412 B）/ `DefaultPassageFormatter.java`（4908 B）
- 同目录 `PostingsOffsetStrategy.java` / `TermVectorOffsetStrategy.java` / `FieldOffsetStrategy.java`
- 同目录 `PhraseHelper.java`（16753 B）/ `SplittingBreakIterator.java`（8312 B）/ `MultiTermHighlighting.java`

## 十、文件

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| `python/passage.py` | 136 | `Passage` + `PassageScorer`（float32 逐点对齐） |
| `python/breakiter.py` | 206 | `BreakIterator` + `LengthGoalBreakIterator` |
| `python/highlighter.py` | 267 | OffsetSource 判定、切分择优、格式化 |
| `python/selfcheck_highlight.py` | 283 | 75 条断言，实跑全绿 |
| `python/main.py` | 121 | 演示入口 |
| `go/*.go` | 129/199/250/69 | 等价 Go 实现（`bracket_check` / `go_sanity` / `go_crossref` 全过） |
