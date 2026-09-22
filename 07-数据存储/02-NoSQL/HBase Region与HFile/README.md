# HBase Region 分裂策略与 HFile

## 一、简介

HBase 的一张表按 rowkey 切成若干 region，region 太大就一分为二。真正决定"多大算大"的是
`RegionSplitPolicy`，而默认策略 `IncreasingToUpperBoundRegionSplitPolicy` 的阈值是**随 region 数
三次方增长**的——一张表刚建时 256MB 就分裂，等它涨到 4 个 region 之后就顶到 10GB 天花板了。

本 demo 把这条曲线、阈值的随机抖动、以及 HFile 版本约束做成可计算模型。

## 二、原理

### 2.1 默认策略：initialSize × count³

```java
protected long getSizeToCheck(final int tableRegionsCount) {
    // safety check for 100 to avoid numerical overflow in extreme cases
    return tableRegionsCount == 0 || tableRegionsCount > 100
      ? getDesiredMaxFileSize()
      : Math.min(getDesiredMaxFileSize(),
                 initialSize * tableRegionsCount * tableRegionsCount * tableRegionsCount);
}
```

`initialSize` 的取值顺序（源码）：

1. `hbase.increasing.policy.initial.size` 若 > 0 直接用；
2. 否则 `2 × 表的 memStoreFlushSize`；
3. 再否则 `2 × hbase.hregion.memstore.flush.size`（默认 128MB）→ **256MB**。

官方注释给的例子（flush size 128MB）：

- 两次 flush 后 256MB → 分裂
- 2 个 region → `2^3 × 128MB × 2 = 2048MB`
- 3 个 region → `3^3 × 128MB × 2 = 6912MB`
- 之后一直到不了，因为撞上了 `maxFileSize`

### 2.2 count 只数主副本

```java
tableRegionsCount = (int) hri.stream()
    .filter(r -> r.getRegionInfo().getReplicaId() == RegionInfo.DEFAULT_REPLICA_ID).count();
```

而且数的是**当前 RegionServer 上同表的 region**，不是全集群的——所以同一张表在不同节点上
的分裂时机可以不一样。

### 2.3 阈值带随机抖动（ConstantSize 策略里加的，被默认策略继承）

```java
double jitter = conf.getDouble("hbase.hregion.max.filesize.jitter", 0.25D);
this.jitterRate = (ThreadLocalRandom.current().nextFloat() - 0.5D) * jitter;
long jitterValue = (long) (this.desiredMaxFileSize * this.jitterRate);
if (this.jitterRate > 0 && jitterValue > (Long.MAX_VALUE - this.desiredMaxFileSize)) {
    this.desiredMaxFileSize = Long.MAX_VALUE;      // 溢出保护
} else {
    this.desiredMaxFileSize += jitterValue;
}
```

`jitterRate ∈ [-0.125, +0.125]`（源码注释写 "Default jitter is ~12% +/-"）。
默认 10GB 时，两个 region 的实际阈值可以差到 **2.5GB**——这是刻意让 region 错峰分裂的。

### 2.4 分裂判定是"严格大于"

`isExceedSize(sizeToCheck)`：正好等于阈值不分裂。

### 2.5 其他策略（官方 book 列出的）

`BusyRegionSplitPolicy`、`ConstantSizeRegionSplitPolicy`、`DisabledRegionSplitPolicy`、
`DelimitedKeyPrefixRegionSplitPolicy`、`KeyPrefixRegionSplitPolicy`、`SteppingSplitPolicy`。
其中 `DisabledRegionSplitPolicy` 会**阻止手工分裂**。

### 2.6 HFile 版本

- 默认 `hfile.format.version = 3`（v3 支持 tags，trailer **始终用 protobuf 序列化**）
- 官方 book 明确：HBase 已**不能写**早于 v3 的 HFile；升级前必须确认配置里不是 2，
  否则 RegionServer 起不来；但**读** v2 仍然可以

## 三、对比

| 策略 | 阈值 | 适用场景 |
| --- | --- | --- |
| `IncreasingToUpperBound`（默认） | min(maxFileSize, initialSize × count³) | 通用；小表早分裂、大表稳 |
| `ConstantSize` | maxFileSize（带抖动） | 想要稳定的大 region |
| `Disabled` | 永不自动分裂 | 全手工控制 |
| `KeyPrefix` / `DelimitedKeyPrefix` | 按 rowkey 前缀分组决定 | 避免拆散同一前缀的行 |
| `Stepping` | 与 IncreasingToUpperBound 类似但步进不同 | 有别的增长曲线需求 |
| `Busy` | 按 region 繁忙程度 | 热点 region 优先拆 |

## 四、环境

- Python 3.8+（仅标准库）；Go 1.21+（仅标准库）
- 无需 HBase

## 五、运行

```bash
cd python && python selfcheck_regions.py   # 43 条断言
cd python && python main.py
cd go     && go run .
```

## 六、关键代码

| 文件 | 内容 |
| --- | --- |
| `python/regions.py` | `jitter_rate` / `constant_size_threshold` / `IncreasingToUpperBoundSplitPolicy` / `split_sequence` / HFile 版本 |
| `python/selfcheck_regions.py` | 43 条断言，含官方注释里的 2048MB / 6912MB 两个值 |
| `python/main.py` | 阈值曲线、抖动、策略对比 |
| `go/regions.go` + `go/main.go` | 同模型 Go 转写（三次方用 int64 连乘，有溢出风险处先比较） |

## 七、性能边界

- 阈值是三次方增长的，**表越大越不爱分裂**：这对"早期快速摊开"有利，但也可能让单个 region 长期偏大
- `count > 100` 直接退回 maxFileSize，源码注释写明是为了**避免数值溢出**
  （`initialSize × 100³` 已经远超 10GB）
- `regionSplitLimit`（默认 1000）官方说**不是硬上限**，只是给 RegionServer 的一个参考
- 抖动让同一时刻不会所有 region 一起分裂，但代价是阈值不确定（±12.5%）
- HFile v3 的 trailer 用 protobuf，比 v2 的自定义序列化更规范，但不再兼容写入 v2

## 八、坑

1. **以为阈值是固定的 10GB**：默认策略下新表 256MB 就分裂，两者差 40 倍。
2. **忘了抖动的符号**：`jitterRate` 是 `(random - 0.5) × jitter`，可能是负的，阈值可能**变小**。
3. **把 regionSplitLimit 当硬限制**：官方明说是 guideline。
4. **count 是本节点的、且只数主副本**：拿全集群 region 数去推阈值会偏大。
5. **三次方用 int 连乘会溢出**：源码在 count>100 时提前退回；Go 侧要先比较再相乘。
6. **升级前没检查 hfile.format.version**：配成 2 会直接导致 RegionServer 启动失败。
7. **本模型的 `split_sequence` 是"抽象轮次"**：真实的分裂是并发的、按 region 各自大小触发的，
   不是整齐的逐轮 +1。

## 九、参考资料（均为本轮实际读取）

- `apache/hbase@master` `hbase-server/.../regionserver/IncreasingToUpperBoundRegionSplitPolicy.java` ——
  `initialSize` 三级取值、`getSizeToCheck` 的三次方与 count>100 防溢出、官方注释里的 2048MB / 6912MB 例子：
  https://github.com/apache/hbase/blob/master/hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/IncreasingToUpperBoundRegionSplitPolicy.java
- `.../regionserver/ConstantSizeRegionSplitPolicy.java` —— `maxFileSize` 取值、
  `hbase.hregion.max.filesize.jitter` 默认 0.25、`jitterRate` 公式与 Long.MAX_VALUE 溢出保护：
  https://github.com/apache/hbase/blob/master/hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/ConstantSizeRegionSplitPolicy.java
- Apache HBase Reference Guide（`hbase.apache.org/book.html`）——
  `hbase.hregion.max.filesize` 默认 10737418240、`regionSplitLimit` 默认 1000、
  可用分裂策略清单、Appendix G: HFile format（v1/v2/v3 差异、v3 trailer 用 protobuf、
  "can no longer write HFile versions earlier than the default of version 3"）：
  https://hbase.apache.org/book.html
- `.../regionserver/HStore.java` —— Store 侧的 flush/compaction 组织（辅助理解 region 与 store 的关系）：
  https://github.com/apache/hbase/blob/master/hbase-server/src/main/java/org/apache/hadoop/hbase/regionserver/HStore.java
