# DynamoDB 自适应容量与热分区

## 一、简介

DynamoDB 的吞吐是按**分区**切分的：每个分区最多 3000 RCU / 1000 WCU，表级预置再多也救不了
"所有流量砸到同一个分区键"这件事。Adaptive capacity 是 AWS 用来缓解这个问题的机制——
它会把冷分区没用掉的容量挪给热分区。本 demo 把「容量单位换算 → 分区上限 → 自适应借用 → 写分片」
这条链路做成可计算的模型，并明确标注哪些是官方原文、哪些是本 demo 选的读法。

## 二、原理

### 2.1 容量单位（官方原文）

- 1 RCU = 每秒 1 次强一致读（item ≤ 4KB），或 2 次最终一致读
- 1 WCU = 每秒 1 次写（item ≤ 1KB）
- 超过单位大小按档**向上取整**

官方给的例子：item 20KB 时，一次强一致读消耗 **5 RCU**，于是单个分区上每秒最多
`3000 / 5 = 600` 次这样的读。

### 2.2 分区上限是硬的

> Every partition in a DynamoDB table is designed to deliver a maximum capacity of
> 3,000 read units per second and 1,000 write units per second.

注意这句话说的是 **every partition**，不是整表。表预置 20000 RCU、但流量全在一个 key 上，
那个 key 所在的分区依然只能吃 3000。

### 2.3 Adaptive capacity：官方只说了一句话

> **Note** Adaptive capacity applies to on-demand mode and provisioned capacity.

官方**没有公布**调度算法。本 demo 选的读法（README 明确标注，非官方）：

1. 每分区先拿到 `表预置 / 分区数` 的均配额，且**无论如何不超过 3000**；
2. 冷分区没用完的部分进池子；
3. 按分区顺序借给还没吃饱的分区，上限仍是 3000。

两条可验证的结论：
- 借用**能**消除"总量够但分配不均"造成的限流；
- 借用**不能**突破单分区硬顶——需求 3500 RCU 时，adaptive 之后仍限流 500。

### 2.4 热分区判据

官方没有给量化判据，只给了定性表格：

| partition key 取值 | 均匀性 |
| --- | --- |
| User ID（用户很多） | Good |
| Status code（取值很少） | Bad |
| 创建日期（按天/小时/分钟取整） | Bad |
| Device ID（各设备访问频率相近） | Good |
| Device ID（有一个设备远热于其他） | Bad |

本 demo 采用的判据（自定，标注口径）：某分区负载 > 均匀分布值 × 1.5 即判热，严格大于才成立。

### 2.5 写分片（官方给了两种做法）

**随机后缀**：给日期键拼一个 `1..200` 的随机数，写被摊到 200 个键上；代价是读一天的数据要
发 **200 次 Query** 再自己归并。

**计算后缀**（官方给了完整算法）：

> a simple calculation would likely suffice, such as the product of the UTF-8 code point
> values for the characters in the order ID, modulo 200, + 1

好处是 `GetItem` 能反算出后缀直接命中；代价同样是读全量要扇出，而且**会碰撞**
（本 demo 里 `order-42` 与 `order-43` 都落到后缀 1）。

## 三、对比

| | 不写分片 | 随机后缀 | 计算后缀 |
| --- | --- | --- | --- |
| 写吞吐 | 受单分区 1000 WCU 限制 | × N | × N |
| 单条 GetItem | 1 次 | 扫全部 N 个后缀 | 1 次（可反算） |
| 读一天全量 | 1 次 Query | N 次 Query + 归并 | N 次 Query + 归并 |
| 确定写入位置 | 天然 | 不可预测 | 可预测 |

## 四、环境

- Python 3.8+（仅标准库）；Go 1.21+（仅标准库）
- 无需 AWS 账号

## 五、运行

```bash
cd python && python selfcheck_capacity.py   # 41 条断言
cd python && python main.py
cd go     && go run .
```

## 六、关键代码

| 文件 | 内容 |
| --- | --- |
| `python/capacity.py` | `wcu_for` / `rcu_for` / `max_ops_per_partition` / `Table.serve` / 写分片后缀 |
| `python/selfcheck_capacity.py` | 41 条断言，含 adaptive 开关成对对比 |
| `python/main.py` | 时间序列表热分区场景 |
| `go/capacity.go` + `go/main.go` | 同模型 Go 转写 |

## 七、性能边界

- 单分区 3000 RCU / 1000 WCU 是设计上限，**不是**可调参数
- item 越大，同一分区能撑的 QPS 越低（`3000 / ceil(size/4096)`），最终一致读可以翻倍
- 写分片的 N 不是越大越好：读扇出 = N，且后缀碰撞会削弱摊平效果
- GSI 也是分区的（`index partitions behave in much the same way as table partitions`），
  热分区问题在 GSI 侧同样会复现

## 八、坑

1. **把表级预置当成救星**：预置翻倍救不了单键热点，因为瓶颈在分区不在表。
2. **`ceil` 是按档不是按比例**：4KB 与 4.001KB 差一整档；20KB 是 5 RCU 而不是 4.88。
3. **最终一致读是半个 RCU**：QPS 上限翻倍，但很多 SDK 默认就是最终一致，算容量时容易漏。
4. **写分片把写问题变成读问题**：N=200 意味着读一天要 200 次 Query，别无脑放大 N。
5. **计算后缀会碰撞**：官方给的码点乘积算法分布并不均匀（本 demo 实测 `order-42`/`order-43` 同为后缀 1），
   生产要用更好的哈希。
6. **Adaptive capacity 不是无限弹性**：本模型的借用受「其他分区的余量」和「单分区硬顶」双重约束，
   两者都过不去就还是限流。
7. **自适应属于服务侧行为**：本模型只是**一种读法**，不要把它当成 AWS 的实际实现去调参。

## 九、参考资料（均为本轮实际读取）

- Amazon DynamoDB Developer Guide《Best practices for designing and using partition keys effectively in DynamoDB》——
  3000 RCU / 1000 WCU、4KB / 1KB 单位、20KB→5 RCU→600 次每秒、adaptive capacity 适用两种容量模式：
  https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-design.html
- 《Partitions and data distribution in DynamoDB》—— 分区分配时机、GSI 也是分区、item collection 自动拆分：
  https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/HowItWorks.Partitions.html
- 《Designing partition keys to distribute your workload in DynamoDB》—— 均匀性对照表（User ID / status code / date / device ID）：
  https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-uniform-load.html
- 《Using write sharding to distribute workloads evenly in your DynamoDB table》—— 随机后缀 1..N、码点乘积 mod 200 + 1、读扇出：
  https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-partition-key-sharding.html
