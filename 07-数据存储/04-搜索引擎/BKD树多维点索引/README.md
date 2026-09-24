# 697 · BKD 树多维点索引

> 转写自 apache/lucene@main `org.apache.lucene.util.bkd`：`BKDConfig` / `BKDUtil` /
> `BKDWriter` / `BKDReader`。Lucene 用它索引数值（`IntPoint`、`LongPoint`）和地理位置
> （`LatLonPoint`），一个字段里所有「点」共用一棵 **BKD（Balanced k-d）树**。

BKD 树 = **k-d 树的分裂逻辑** + **B+ 树的块布局**：内节点只存「切哪个维度、切在哪儿」，
真正的点按 512 个一块塞进叶子块。它的两个关键性质：

- **树形完全由点数决定**：`numLeaves = ceil(pointCount / maxPointsInLeafNode)`，与数据分布
  无关 —— 所以能一次性（one-pass）自底向上打包，也能在查询时用 nodeID 做 O(1) 定位。
- **只有叶子有点**：内节点的「包围盒」不落盘，查询时靠 `pushBoundsLeft/Right` 沿路径**推**
  出来，省掉大量空间。

---

## 1. BKDConfig：四个参数

| 参数 | 含义 |
| --- | --- |
| `numDims` | 点的维度，1..16 |
| `numIndexDims` | **参与索引**的维度数，1..8，且 ≤ numDims |
| `bytesPerDim` | 每维字节数（1/2/4/8/16） |
| `maxPointsInLeafNode` | 每个叶子块最多装多少点，默认 512 |

`numIndexDims < numDims` 时，多余的维度只存不索引 —— 查询时整块读出来线性过滤。
官方把九种常用组合硬编码进 `DEFAULT_CONFIGS`，命中它们可以走更紧凑的读取路径：

```
(1,1,2) (1,1,4) (1,1,8) (1,1,16)
(2,2,2) (2,2,4) (2,2,8) (2,2,16)
(7,4,4)                          ← LatLonPoint + 额外的 5 个数据维度
```

派生量：`packedBytesLength = numDims * bytesPerDim`（一个点占多少字节）、
`packedIndexBytesLength = numIndexDims * bytesPerDim`（索引里一份 split 值占多少字节）、
`bytesPerDoc = packedBytesLength + 4`（叶子块里还要带 docID）。

## 2. 树的形状：getNumLeftLeafNodes

已知当前子树要放 `numLeaves` 个叶子，**左子树分几个**？源码的做法是把叶子尽量铺成满二叉树：

```java
lastFullLevel     = 31 - Integer.numberOfLeadingZeros(numLeaves);   // floor(log2)
leavesFullLevel   = 1 << lastFullLevel;      // 最后一个满层能放的叶子数
numLeftLeafNodes  = leavesFullLevel / 2;     // 满层的一半归左
numLeftLeafNodes += Math.min(unbalancedLeafNodes, numLeftLeafNodes); // 不满的部分也尽量往左塞
```

两条断言 `numLeft >= numRight` 且 `numLeft <= 2 * numRight` 保证树不会歪得太离谱：
`numLeaves=9` 时左 5 右 4（`8/2 + min(1,4)`），`numLeaves=17` 时左 9 右 8。
切点于是直接算出来：`mid = from + numLeftLeafNodes * maxPointsInLeafNode`。

## 3. 切哪个维度：先「2 倍」规则，再看跨度

```java
for (dim = 0; dim < numIndexDims; dim++)
  if (parentSplits[dim] < maxNumSplits / 2 && min[dim] != max[dim]) return dim;
// 否则挑 max - min 跨度最大的维度
```

**「2 倍」规则是重点**：如果某个维度被切的次数还不到「最多那个维度」的一半、且它在当前
包围盒里取值不全等，就**无条件切它**（哪怕跨度很小）。这条规则保证每个维度都会被切到，
不会出现「维度 1 从头切到尾、维度 2 一次没碰」的病态树 —— 那样的树在只约束维度 2 的
查询上会退化成全表扫描。源码注释写得很直白：*"so that we always split on all dimensions"*。

## 4. 叶子块：公共前缀 / sortedDim / leafCardinality

叶子节点落地前算三样东西（`BKDWriter.build` 的 `numLeaves == 1` 分支）：

1. **每维的公共前缀长度** `commonPrefixLengths[dim]` —— 块内所有点在该维字节串上共享的
   前缀有多长。落盘时这些字节只写一次。
2. **sortedDim** —— 在 `commonPrefixLengths[dim] < bytesPerDim` 的那些维度里，挑**该字节上
   不同取值最少**的那个维度（用 `FixedBitSet(256)` 数），然后按它排序整个块。排序后相邻
   点大概率前缀相同，后面的行程编码（run-length）才好压缩。
3. **leafCardinality** —— 块内**不同点值**的个数（不算 docID）。它决定走 low-cardinality
   编码还是普通编码。

> ⚠️ 两处「看着像 bug 但就是这么写的」细节，转写时照抄了：
> * `usedBytes` 的统计循环是 `for (int i = from + 1; i < to; ++i)` —— **第 from 个点不参与**
>   基数统计。点数很少时 sortedDim 的选择会和「直觉版」不一样。
> * `leafCardinality` 从 1 起算（不是 0），且按**整个点**去重、忽略 docID。

## 5. 索引的前缀编码：一个 vInt 塞三件事

`recursePackIndex` 给每个内节点写：

```
code = (firstDiffByteDelta * (1 + bytesPerDim) + prefix) * numIndexDims + splitDim
```

一个 vInt 同时编码了「split 值和父值**相差多少**」「公共前缀**多长**」「切的**哪个维度**」。
紧跟着写 `bytesPerDim - prefix - 1` 个后缀字节（第 `prefix` 个字节靠 delta 还原，不写）。

**negativeDeltas** 是这里最容易读错的地方：

```java
negativeDeltas[splitDim] = true;   recursePackIndex(..., 左子树, ...);
negativeDeltas[splitDim] = false;  recursePackIndex(..., 右子树, ...);
```

**无条件赋值，不是取反**。原因是沿根到叶的任一路径，同一维度上的 split 值一定单调：
往左走只会变小、往右走只会变大。所以带上「我现在在左子树还是右子树」这个符号，delta 就
恒为正 —— 源码紧接着就是 `assert firstDiffByteDelta > 0`。这个不变量在自检
`t_monotone` 里被直接验证。

读侧 `BKDReader.readNodeData` 对称地做：`negativeDeltas[level][parent的splitDim] = isLeft`，
然后 `oldByte + (negativeDeltas ? -delta : delta)` 还原那个字节，剩下的后缀字节从流里读。

## 6. 查询：包围盒下推 + 三态关系

内节点不存包围盒，查询时沿路径推：

```java
pushBoundsLeft () : maxPackedValue[splitDim] = splitValuesStack[level][splitDim]  // 换上界
pushBoundsRight() : minPackedValue[splitDim] = splitValuesStack[level][splitDim]  // 换下界
```

每到一个节点先算 `PointValues.Relation`：

| 关系 | 含义 | 动作 |
| --- | --- | --- |
| `CELL_OUTSIDE_QUERY` | 包围盒与查询盒不交 | 剪掉整个子树 |
| `CELL_INSIDE_QUERY` | 包围盒完全被查询盒包含 | **不用再判点**，整棵子树全部收下 |
| `CELL_CROSSES_QUERY` | 部分相交 | 继续下推，到叶子再逐个判点 |

只有 `CROSSES` 才需要真正读点，这是 BKD 快的主要原因。`INSIDE` 时连叶子块的点都不解析，
只把 docID 段整段喂给 collector。

## 7. SPLITS_BEFORE_EXACT_BOUNDS

```java
numLeaves != totalLeaves && numIndexDims > 2 && sum(parentSplits) % 4 == 0
```

建树时下传的 min/max 是靠「父包围盒换掉 splitDim 那一维」推出来的**上界/下界**，本身是
宽松的（左子树的 max 只是「不超过 splitValue」，未必真的取到）。重算精确包围盒要扫一遍
当前区间所有点，太贵。所以源码只在**非根节点 + 维度 > 2 + 切分次数是 4 的倍数**时才重算
一次，用 1/4 的代价换回大部分精度。低维（≤2）时推出来的界已经够紧，索性永不重算。

---

## 8. 目录与运行

```
python/
  bkd.py              BKDConfig / BKDUtil / 树形与切分维度选择
  bkdtree.py          建树、叶子统计、索引前缀编码、范围查询
  checkutil.py        自检用的断言计数器
  selfcheck_bkd.py    自检：配置 / 前缀 / 树形 / 建树 / 查询（4383 条断言）
  selfcheck_pack.py   自检：索引前缀编码 / split 值单调性（1941 条断言）
  main.py             演示入口
go/
  bkd.go / node.go / pack.go / search.go  （无 Go 工具链，走人工审查 + 静态检查）
```

```bash
cd python && python selfcheck_bkd.py     # 4383 assertions OK
python selfcheck_pack.py                 # 1941 assertions OK
python main.py
```

`main.py` 实测（1000 个二维点，`bytesPerDim=8`，`maxPointsInLeafNode=32`）：

| 项 | 结果 |
| --- | --- |
| 叶子 / 内节点 | 32 / 31 |
| 索引体积 | 朴素 248 字节 → 前缀编码 93 字节，**省 62.5%** |
| 查询 `[200000,400000]²` | 命中 42，访问 4 个叶子、剪掉 6 个子树、扫 128 点（暴力 1000） |
| 查询 `[100000,900000]²` | 命中 591，扫 928 点（几乎全表，因为查询盒太大） |

一维（`numIndexDims=1`，200 点，叶子容量 8）：25 个叶子，命中 5，扫 16 点、剪 4 次。

## 9. 口径声明

- 官方按 **bytesPerDim 字节的无符号字节串**比较（NumericUtils 的编码顺序）。本 demo 直接用
  非负整数代替字节串，无符号比较退化为普通整数比较，`subtract(...)` 就是 `max - min`。
  语义等价，省掉了编码/解码的噪音。
- `MutablePointTreeReaderUtils.partition`（把中位数挪到 `mid`）**未逐行转写**：demo 用
  「按 splitDim 排序 [frm,to)」实现，切点同样取排序后的 `pts[mid][splitDim]`。官方用
  `BKDRadixSelector` 做 O(n) 选择，结果一致，复杂度不同。
- demo 的 `intersect` 只统计命中文档号；官方还要处理 docID 段排序、低基数块的行程编码、
  `isTreeBalanced` 的 nodeID 换算等，这里都省略了。

## 10. 参考资料（实际读过的源码）

- `BKDConfig.java` — https://raw.githubusercontent.com/apache/lucene/main/lucene/core/src/java/org/apache/lucene/util/bkd/BKDConfig.java
- `BKDUtil.java` — https://raw.githubusercontent.com/apache/lucene/main/lucene/core/src/java/org/apache/lucene/util/bkd/BKDUtil.java
- `BKDWriter.java` — https://raw.githubusercontent.com/apache/lucene/main/lucene/core/src/java/org/apache/lucene/util/bkd/BKDWriter.java
- `BKDReader.java` — https://raw.githubusercontent.com/apache/lucene/main/lucene/core/src/java/org/apache/lucene/util/bkd/BKDReader.java
