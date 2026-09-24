# FST 的 TopN 搜索与零输出补全（Lucene `Util.TopNSearcher`）

> 问题：FST 里存了几十万条建议，怎么只取"权重最高的 10 条"而不遍历全部？
> Lucene 的答案是 `Util.TopNSearcher` —— 一个有界优先队列 + 一个叫**零输出补全**的技巧。
> 本文全部结论来自 `apache/lucene@main` 的 `Util.java` 源码实读。

## 一、它是谁的下游

`NRTSuggester.lookup`（见同目录 `../FST与前缀补全suggester/`）就是拿它做搜索的。
那里有一句注释：

```java
// search admissibility is not guaranteed
// see comment on getMaxTopNSearcherQueueSize
```

**出处就在这里**：`TopNSearcher` 的队列深度是 `maxQueueDepth`，满了以后最差的会被挤掉，
被挤掉的候选再也回不来，所以结果可能不是全局 top-N。

## 二、前提：每个非根节点必须有一条 NO_OUTPUT 弧

```java
if (comparator.compare(NO_OUTPUT, path.arc.output()) == 0) { ... }
...
assert foundZero;
```

注意两个细节：

1. 判等用的是 `comparator.compare(NO_OUTPUT, arc.output()) == 0`，**不是 `==`**。
   源码注释专门写了 "tricky: instead of comparing output == 0, we must express it via
   the comparator"。因为 output 可能是自定义类型（`PairOutputs` 等），没有值相等语义。
2. 这条不变式由 **FST 的"输出前推"** 保证：自底向上，每个节点取 `min(arc.output)`，
   从所有出弧里减掉、加到所有入弧上。推完之后每个节点至少有一条零弧。

> **根是例外**：根没有入弧可吸收 `min`，减了会让所有路径整体偏移；而且根也不需要零弧 ——
> `addStartPaths` 会把根的**全部**出弧都入队，"零输出补全"是从根的**目标节点**才开始的。
> 本 demo 的前推实现显式跳过根，并断言"前推不改变任何路径的输出"。

## 三、零输出补全：为什么敢直接往下走

拿到一条队列里的路径后，搜索器做这件事：

> "We take path and find its *0 output completion*, ie, just keep traversing the first arc with
> NO_OUTPUT that we can find, since this must lead to the minimum path that completes from path.arc."

因为每个节点都有一条零弧，沿着它往下走**不会让 output 变大**，于是一路走到底得到的就是
这条路径能延伸出的**最小**输出。这就是不用遍历全部候选也能保证"下一个弹出的就是当前最优"的原因。

遍历过程中，遇到的**非零**输出弧不会被丢弃，而是 `addIfCompetitive` 进队列留待以后。

## 四、addIfCompetitive：只在队列满时才比较

```java
if (queue.size() == maxQueueDepth) {
  FSTPath<T> bottom = queue.last();
  int comp = pathComparator.compare(path, bottom);
  if (comp > 0) return;                    // Doesn't compete
  else if (comp == 0) {
    // Tie break by alpha sort on the input
    ... if (cmp < 0) return;
  }
  // Competes
}
// else ... Queue isn't full yet, so any path we hit competes
```

- **队列没满 → 任何路径都算竞争**，直接入队。这是"宽松期"。
- **队列满了 → 必须赢过当前最差的**（`queue.last()`）才进得来；同分再按 input 字典序比一次。
- 入队后若 `size == maxQueueDepth + 1`，立刻 `pollLast()` 把最差的踢掉。

默认比较器是 `TieBreakByInputComparator`：先比 output，同分比 input 字典序。

## 五、search 主循环的几个开关

```java
while (results.size() < topN) {
  path = queue.pollFirst();
  if (path == null) break;                       // 候选不够了
  if (acceptPartialPath(path) == false) continue; // 被剪枝：不占结果名额，但队列少了一个
  if (path.arc.label() == FST.END_LABEL) {       // 空串！
    path.input.setLength(path.input.length() - 1);
    results.add(new Result<>(path.input.get(), path.output));
    continue;
  }
  if (results.size() == topN - 1 && maxQueueDepth == topN) {
    queue = null;                                // 最后一条：不再入队
  }
  ... 零输出补全 ...
}
return new TopResults<>(rejectCount + topN <= maxQueueDepth, results);
```

三条要点：

1. **`queue = null` 的条件是 `topN - 1` 且 `maxQueueDepth == topN`**：
   只剩最后一个名额、且队列深度恰好等于 topN 时，剩下的路不用再入队了，直接走零输出补全。
   `maxQueueDepth != topN` 时队列**不会**被置空（本 demo 两条路径分别验证）。
2. **`acceptPartialPath` 返回 false 时不消耗结果名额**，只是 `continue`。
   被剪枝的路径不会出现在结果里，但队列深度被消耗了 → 更容易丢候选。
3. **`isComplete = rejectCount + topN <= maxQueueDepth`** —— 被 `acceptResult` 拒掉的数量
   也算进"队列消耗"。这是 `NRTSuggester` 敢把队列放大 `numDocs/2` 的原因（过滤会拒掉很多）。

## 六、空串：`allowEmptyString`

```java
while (true) {
  if (allowEmptyString || path.arc.label() != FST.END_LABEL) {
    addIfCompetitive(path);
  }
  ...
}
```

根上的 `END_LABEL` 弧代表"空字符串"这条结果，**只有 `allowEmptyString=true` 才会入队**。
实测（FST 含 `""→4` 与 `[7]→9`）：

| allowEmptyString | 结果 |
| --- | --- |
| `false` | `((7,), 9)` |
| `true` | `((), 4), ((7,), 9)` |

## 七、实测：队列深度与 admissibility

FST 含 `([1,2],5) ([1,3],7) ([9],2)`，`topN=3`：

| maxQueueDepth | 收到条数 | isComplete |
| --- | --- | --- |
| 1 | **1**（丢了 2 条） | False |
| 2 | 3 | **False**（`0+3 <= 2` 不成立） |
| 3 | 3 | True |
| 50 | 3 | True |

注意 depth=2 这一行：**结果条数是对的，但 `isComplete` 仍然是 false**。
也就是说 Lucene 自己也不认为这个结果可信 —— 这正是 `NRTSuggester` 要把队列放大
`topN × maxAnalyzedPathsPerOutput ÷ liveDocsRatio` 的理由。

## 八、本 demo 复现的现象

| 现象 | 验证点 |
| --- | --- |
| 输出前推不改变任何路径的 output | 前推前后枚举全部路径比对 |
| 前推后每个**非根**节点都有零弧 | `check_zero_arc_invariant` |
| 根**不要求**零弧 | 根无零弧时不变式仍成立 |
| 零弧不变式被破坏 → 断言 | 非根节点只有非零弧时报错 |
| 队列没满时不比较 | depth 足够时全部入队 |
| 队列满时挤掉最差 | depth=1 丢候选 |
| `isComplete = rejectCount + topN <= depth` | depth=2 收满但仍 False |
| 同分按 input 字典序 | `(1,),(2,),(3,)` |
| 空串只在 `allowEmptyString` 时入队 | 见表 |
| `queue = null` 只在 `depth == topN` 时 | 两条路径分别验证 |
| `acceptPartialPath` 拒绝不占结果名额 | 剪枝后收不满 topN |
| `acceptResult` 拒绝计入 `rejectCount` | 影响 `isComplete` |
| 大样本与暴力枚举一致 | 12 条路径输出完全相同 |

## 九、建模口径与限制

- FST 的**字节编码**与**后缀共享**不还原，用显式节点/弧结构代替；
  被断言的性质只依赖"路径输出可累加"与"零弧不变式"。
- 输出类型固定为整数（`PositiveIntOutputs` 的简化），`comparator` 即整数比较；
  真实场景里 `NRTSuggester` 用的是 `PairOutputs<Long, BytesRef>`。
- `Util.TopNSearcher` 里那些 `TODO`（弧按权重排序、nibble 输入类型）未实现。

## 十、参考资料（实际读过）

- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/util/fst/Util.java`（30741 B）：
  `TopNSearcher`（构造 / `addIfCompetitive` / `addStartPaths` / `search`）、
  `TieBreakByInputComparator`、`FSTPath`、`TopResults`
- 同目录 `FST.java`（42522 B）/ `FSTCompiler.java`（44176 B）/ `PositiveIntOutputs.java`（3104 B）
- 关联：`lucene/suggest/.../NRTSuggester.java`（本 demo 解释其 admissibility 注释的出处）

## 十一、文件

`python/fstsearch.py`(258) FST 结构与输出前推 + TopNSearcher 全篇 ·
`python/selfcheck_fsttopn.py`(196) **40 条断言实跑全绿** · `python/main.py`(118) ·
`go/fst.go`(195) · `go/topn.go`(248) · `go/main.go`(136)。

> Go 侧无本机工具链，走 `bracket_check` / `go_sanity` / `go_crossref` 三项静态检查，全过。
