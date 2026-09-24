# BlockMaxWAND 与动态剪枝（Lucene MaxScoreBulkScorer）

`BooleanQuery` 全是 SHOULD 子句时，Lucene 不再做「求并集再逐篇全量打分」，而是
用 **MaxScoreBulkScorer**：靠索引里预存的 **impacts（freq/norm 的竞争对上界）**
把文档切成一个个窗口，在每个窗口里把子句分成 **essential / non-essential** 两类，
只让 essential 子句驱动候选生成，其余子句只用来「补分 + 竞争过滤」。

本 demo 把这套决策逐行转写成 Python 与 Go，并与朴素 OR 检索对拍。

## 1. 三层结构

| 层 | 大小 | 作用 |
| --- | --- | --- |
| outer window | 由 impacts 的块边界决定（自适应） | 取每个子句的 `maxWindowScore` 上界 |
| inner window | `INNER_WINDOW_SIZE = 1 << 12` | 把命中收集进 bitset，省掉优先队列反复重排 |
| 分句划分 | 每个 outer window 一次 | 决定谁驱动候选、谁只补分 |

```java
static final int INNER_WINDOW_SIZE = 1 << 12;
```

注意 `INNER_WINDOW_SIZE` 是**内层收集窗口**，不是 impacts 的外窗口 —— 两者同名
不同义，读代码时最容易混。

## 2. 分句划分：`partitionScorers`

```java
Arrays.sort(scratch, (s1, s2) -> Double.compare(
    (double) s1.maxWindowScore / Math.max(1L, s1.cost),
    (double) s2.maxWindowScore / Math.max(1L, s2.cost)));
double maxScoreSum = 0;
for (int i = 0; i < allScorers.length; ++i) {
  double newMaxScoreSum = maxScoreSum + w.maxWindowScore;
  float maxScoreSumFloat =
      (float) MathUtil.sumUpperBound(newMaxScoreSum, firstEssentialScorer + 1);
  if (maxScoreSumFloat < scorable.minCompetitiveScore) {
    maxScoreSum = newMaxScoreSum;              // 塞得进预算 -> non-essential
    allScorers[firstEssentialScorer] = w;
    maxScoreSums[firstEssentialScorer] = maxScoreSum;
    firstEssentialScorer++;
  } else {
    allScorers[allScorers.length - 1 - (i - firstEssentialScorer)] = w;  // 尾部倒填
    nextMinCompetitiveScore = Math.min(maxScoreSumFloat, nextMinCompetitiveScore);
  }
}
if (firstEssentialScorer == allScorers.length) return false;  // 整窗没有匹配
```

四个要点：

1. **排序键是 `maxWindowScore / cost` 而不是 `maxWindowScore`**。源码注释解释了
   动机：常见情况下最大分数与文档频率负相关，两者等价；但自定义打分 / 高 boost /
   离谱权重下会分叉。实测：两个上界都是 2.0 的子句，`cost` 为 10 与 1000 时，
   **cost 大的那个先被划成 non-essential**。
2. **essential 子句是从数组尾部倒着填的**，所以「第 `k` 个被判为 essential」落在
   `allScorers[n-1-k]`。转写成下标数组时若不反转就会把子句认错。
3. **`firstEssentialScorer == n` 表示整窗判空**：所有子句的上界之和都进不了预算，
   说明这个窗口里一篇文档都进不了 top-K。
4. **`sumUpperBound` 只在 >2 个值时放大**：`numValues <= 2` 时浮点加法与求和顺序
   无关，直接返回原值；否则乘 `(1 + 2*b)`，`b = (n-1) * 2^-52`。

## 3. 只有一个 essential 时的「必需子句」回退

```java
if (firstEssentialScorer == allScorers.length - 1) {
  firstRequiredScorer = allScorers.length - 1;
  double maxRequiredScore = allScorers[firstEssentialScorer].maxWindowScore;
  while (firstRequiredScorer > 0) {
    double maxPossibleScoreWithoutPreviousClause = maxRequiredScore;
    if (firstRequiredScorer > 1)
      maxPossibleScoreWithoutPreviousClause += maxScoreSums[firstRequiredScorer - 2];
    if ((float) maxPossibleScoreWithoutPreviousClause >= minCompetitiveScore) break;
    --firstRequiredScorer;
    maxRequiredScore += allScorers[firstRequiredScorer].maxWindowScore;
  }
}
```

语义：如果**去掉某个 non-essential 子句后连理论上限都够不着阈值**，那这个子句其实
是必需的 —— 于是 `firstRequiredScorer` 一路往前退，命中的过滤从「加分」升级为
「交集」。源码给的例子：`quick fox`，`maxscore(quick)=maxscore(fox)=1`，
`minCompetitive=1.5`，此时两个子句都变成 required。

实测：`maxWindowScore = [1, 1, 3]`、`minCompetitive = 2.5` 时 `firstRequired = 2`
（去掉上一项后 1+3=4 ≥ 2.5，不再往前退）；而 `[1, 1, 1]`、`minCompetitive = 2.5`
时会一路退到 **0**（三个子句缺一不可）。

## 4. 竞争分过滤：`minRequiredScore` 用的是 **float** ulp

```java
static double minRequiredScore(double maxRemainingScore, float minCompetitiveScore, int numScorers) {
  double minRequiredScore = minCompetitiveScore - maxRemainingScore;
  // note: we want the float ulp in order to converge faster, not the double ulp
  double subtraction = Math.ulp(minCompetitiveScore);
  while (minRequiredScore > 0
      && (float) MathUtil.sumUpperBound(minRequiredScore + maxRemainingScore, numScorers)
          >= minCompetitiveScore) {
    minRequiredScore -= subtraction;
  }
  return minRequiredScore;
}
```

**这是本 demo 最容易踩的坑**：`Math.ulp(float)` 返回的量级是 `~2.4e-7`，而 Python
的 `math.ulp(float(x))` 给的是 **double** 的 ulp（`~4.4e-16`） —— 差 9 个数量级，
会让循环空转十万次也退不出。必须按 IEEE-754 binary32 的位模式加一来取。

## 5. 外窗口：自适应 `minWindowSize`

```java
if (allScorers.length - firstWindowLead > 1) {
  long threshold = numOuterWindows * 32L * allScorers.length;
  if (numCandidates < threshold) minWindowSize = Math.min(minWindowSize << 1, INNER_WINDOW_SIZE);
  else minWindowSize = 1;
  windowMax = Math.max(windowMax, MathUtil.unsignedMin(Integer.MAX_VALUE, windowMin + minWindowSize));
}
```

追求的是「每子句每个外窗口平均至少 32 个候选」，好把「算上界」的开销摊掉。
注意 **`allScorers.length - firstWindowLead > 1` 这个前置条件**：只剩一个 lead
（子句数 = lead 下标）时压根不启用自适应，窗口大小恒等于 impacts 块边界。

## 6. 两处「看起来能省、省了就错」

- **`updateMaxWindowScores` 里的 `scorer.doc < windowMax` 判据不能省**：子句在窗口
  里一篇都不命中时上界必须是 0。省掉之后，已经走到表尾的子句会拿
  `globalMaxScore`（= `score(Float.MAX_VALUE, 1L)`）当上界，窗口撑成永不推进的死区间。
- **`advanceShallow` 不能省**：`MaxScoreCache.getLevel` 是从**当前块**往后找层的。
  不做 shallow advance 就取上界，拿到的是「后面某一块」的最大值，把窗口前半段漏掉，
  从而剪掉本不该剪的文档 —— 本 demo 在修正这一步之前，top-K 结果与朴素检索不一致。

## 7. 实测

四子句（文档频率 1200 / 800 / 500 / 300，idf 3.0 / 2.0 / 1.0 / 0.5，4000 篇文档，
top-10）：

| seed | 朴素全量评分 | BlockMaxWAND 候选 | 全量评分 | 占比 | top-10 一致 |
| --- | --- | --- | --- | --- | --- |
| 11 | 2196 | 1135 | 299 | 13.6% | 是 |
| 12 | 2191 | 1130 | 311 | 14.2% | 是 |
| 13 | 2162 | 1222 | 323 | 14.9% | 是 |

## 8. 运行

```bash
python python/selfcheck_wand.py   # 81 条断言
python python/main.py             # 演示 + 对拍
go run ./go                       # Go 侧（分句划分 + 竞争过滤 + 端到端）
```

## 9. 参考资料（本轮实际读过的源文件）

- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/search/MaxScoreBulkScorer.java`（23 616 B）
- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/search/MaxScoreCache.java`（5 540 B）
- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/search/WANDScorer.java`（24 901 B）
- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/search/ScorerUtil.java`（8 497 B）
- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/util/MathUtil.java`（`sumUpperBound` / `sumRelativeErrorBound`）
- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/codecs/CompetitiveImpactAccumulator.java`（5 806 B）
- `apache/lucene@main` — `lucene/core/src/java/org/apache/lucene/search/ImpactsDISI.java`（3 892 B）

> 口径：本 demo 的相似度只取 BM25 的 tf 部分 `idf * freq / (freq + k1*((1-b) + b*norm))`
> （k1=1.2、b=0.75），idf 按子句直接给定；impacts 的竞争对由
> `CompetitiveImpactAccumulator.getCompetitiveFreqNormPairs` 的规则生成（每个 norm
> 只留最大 freq，再按 norm 升序只保留 freq 严格递增者）。这套口径只影响具体数值，
> 不影响上面所有结构性结论。
