# MongoDB 分片集群：chunk 分裂、均衡阈值与迁移

## 一、简介

MongoDB 的分片集群把集合切成一段段的 range（chunk），由 config server 上的 balancer 决定
哪段放在哪个 shard。这套机制有两个反直觉的地方：

1. **均衡不是"尽量均匀"，而是"差到 3 倍 range size 才动手"**——默认 128MB 的 range size 下，
   两个 shard 差 383MB 也叫均衡；
2. **chunk 分裂不出来就搬不动**——jumbo chunk 会直接卡住均衡器。

本 demo 把这两件事连同 range 迁移的 7 个步骤做成可计算的模型。

## 二、原理

### 2.1 均衡阈值：3 × range size（官方原文）

> A collection is considered balanced if the difference in data between shards (for that collection)
> is less than three times the configured range size for the collection. For the default range size of
> `128MB`, two shards must have a data size difference for a given collection of at least `384MB`
> for a migration to occur.

所以"均衡"是一个**有宽度的区间**，不是一个点。默认配置下，差 383MB 不会有任何动作。

### 2.2 并发度：floor(n/2)

> MongoDB can perform parallel data migrations, but a shard can participate in at most one migration
> at a time. For a sharded cluster with *n* shards, MongoDB can perform at most *n/2* (rounded down)
> simultaneous migrations.

一次迁移要占源和目标两个 shard，所以上限是 floor(n/2)，不是 n-1。

### 2.3 range 迁移的 7 步

1. balancer 向源分片下发 `moveRange`
2. 源分片**继续**承接该 range 的写（迁移期间写都打在源上）
3. 目标分片补齐源端有而自己没有的索引
4. 目标分片逐文档拉取
5. 拉取完成后做一次增量同步，补上迁移期间发生的变化
6. 源分片连 config 库更新这段 range 的新位置
   —— 这一步前后**都要求多数派确认**，且更新元数据前会短暂暂停该集合的读写
7. 源分片删掉自己的副本（要等这段 range 上没有打开的游标）

### 2.4 删除阶段是异步的

> the balancer does not wait for the current migration's delete phase to complete before starting the
> next range migration

也就是说第 6 步完成就算迁移成功，第 7 步排队做。这让"加新分片/初始导入"这种重度不均衡场景能快速卸货，
代价是删除阶段会持续产生 cache 与 I/O 压力（官方明确提示了这点）。

### 2.5 jumbo chunk

> A chunk becomes *jumbo* when it exceeds the configured chunk size and cannot be split automatically.

分两种：

- **divisible**（含多个唯一 shard key 值）→ 可以 `sh.splitAt()` 手工劈开；
- **indivisible**（单个唯一 shard key 值）→ 劈不动，只能 `refineCollectionShardKey` 加后缀字段，
  或者整表 `reshardCollection`。

典型的成因就是**单调 shard key**（按天/按时间戳做 key）：一天一个值，这个值的所有文档构成一个 chunk，
超过 128MB 就变 jumbo，且 indivisible。

## 三、对比

| | 分裂 | 迁移 | jumbo 之后 |
| --- | --- | --- | --- |
| 触发条件 | chunk > range size | shard 间差 ≥ 3 × range size | 均衡器跳过该 chunk |
| 粒度 | 二分（块数是 2 的幂） | 整个 range | 不可分 |
| 并发 | 各分片独立 | floor(n/2) | — |
| 代价 | 元数据变更 | 带宽 + 删除阶段的 I/O | 数据分布永久倾斜 |

## 四、环境

- Python 3.8+（仅标准库）；Go 1.21+（仅标准库）
- 无需 MongoDB

## 五、运行

```bash
cd python && python selfcheck_chunking.py   # 43 条断言
cd python && python main.py
cd go     && go run .
```

## 六、关键代码

| 文件 | 内容 |
| --- | --- |
| `python/chunking.py` | 阈值判定 / 并发度 / 二分分裂 / `Chunk.evaluate` / `Migration` 状态机 / `balance_round` |
| `python/selfcheck_chunking.py` | 43 条断言，含 384MB 边界的成对用例 |
| `python/main.py` | 六个场景的演示输出 |
| `go/chunking.go` + `go/main.go` | 同模型 Go 转写 |

## 七、性能边界

- 阈值=3×range size 意味着**均衡是迟滞的**：调小 range size 会让均衡更积极但元数据更多
- 分裂是二分的，块数恒为 2 的幂；一个 5GB 的 chunk 要连劈 6 次才都落到 128MB 以下
- 迁移期间目标分片要**重建源端有而自己没有的索引**——索引越多，迁移越慢
- 删除阶段是异步的，重度不均衡时会有大量孤儿文档清理同时跑，cache/IO 压力大
- MongoDB 8.0+ 可以 `reshardCollection` 到**相同** shard key 来重分布数据，官方说这比 range migration 快得多且不需要 range cleanup

## 八、坑

1. **把"均衡"当成"相等"**：差 383MB 官方就叫均衡，别拿 chunk 数去衡量倾斜。
2. **单调 shard key 是万恶之源**：它同时造成写热点和 indivisible jumbo chunk，后者均衡器救不了。
3. **jumbo 标记不会自动解除**：手工 split 之后还要确认 balancer 重新跑起来。
4. **并发上限是 floor(n/2) 不是 n-1**：因为一次迁移占两个 shard。
5. **异步删除 ≠ 已删除**：`_waitForDelete: true` 会跳过 `orphanCleanupDelaySecs` 延迟，
   但也可能终止 secondary 上的长读（8.2 起 `terminateSecondaryReadsOnOrphanCleanup` 控制）。
6. **均衡窗口是相对 config server primary 的本地时区**：跨时区部署极易配错。
7. **本模型的 `balance_round` 是按固定数据量搬的近似**：官方挑的是具体 chunk，不是固定块大小，
   别拿这里的迁移次数当真实预测。

## 九、参考资料（均为本轮实际读取）

- MongoDB Manual《Sharded Cluster Balancer》—— 3×range size 阈值、floor(n/2) 并发、7 步迁移流程、
  异步删除、`_secondaryThrottle`、8.2 的 `terminateSecondaryReadsOnOrphanCleanup`：
  https://www.mongodb.com/docs/manual/core/sharding-balancer-administration.md
- 《Troubleshoot Stuck Chunk Migrations》—— jumbo 定义、divisible/indivisible 的处置、
  低基数与单调 key 的危害、balancer 状态检查命令：
  https://www.mongodb.com/docs/manual/troubleshooting/chunk-migrations-stuck.md
- MongoDB 文档 LLM 索引（用于定位上述页面真实 URL）：
  https://www.mongodb.com/docs/llms.txt · https://www.mongodb.com/docs/manual/manual-8-llms.txt
