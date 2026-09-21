# 分片与路由键空间(Vitess 风格)

## 简介

分片最难的不是"把数据切开",而是**切开之后还能准确定位**。Vitess 的做法是在 SQL 之下
引入一层**键空间(key space)**:每个分片键先经 Primary Vindex 映射成 8 字节的
`keyspace id`,再按 `[start, end)` 的键范围落到具体分片。于是"这条查询打哪几个分片"
变成了一个纯粹的区间判定问题。

本 demo 按官方《Sharding》文档与 `go/vt/vtgate/vindexes/` 源码逐行转写这层机制,
并用两个可计算的对照实验回答两个工程问题:

1. **Vindex 选错会怎样** —— 同一个自增 id 列,用 `numeric` 会让 10000 行**全部**落在
   一个分片;换成 `reverse_bits` 则精确对半(5000 / 5000)。
2. **resharding 时键动不动** —— 键空间的意义在于**键不动,只有分片边界动**;
   `80-c0` 切成 `80-a0` + `a0-c0` 后,每个 id 的归属由子分片继承,数据搬迁量才是"半个分片"。

## 原理详解

### 1. 键范围:含起点、不含终点

文档定义:键范围是**连续的 keyspace id 区间**,`key >= start && key < end`;
空起点代表最小值、空终点代表"比最大可能值还大"。分片名把起止值写成十六进制用连字符连接:

| 分片名 | 含义 |
| --- | --- |
| `-40` | `[0x00, 0x40)`,起点为空 |
| `40-80` | `[0x40, 0x80)` |
| `80-c0` | `[0x80, 0xc0)` |
| `c0-` | `[0xc0, +∞)`,终点为空 |
| `80-` | `[0x80, +∞)` |

完整分区(partition)必须**首尾相接且覆盖全空间**:必须有一个分片起点为空、一个分片
终点为空,相邻分片的 `上一个.end == 下一个.start`。分片之间**不需要等宽**,文档给的例子
`-80 / 80-c0 / c0-dc00 / dc00-dc80 / dc80-` 也是合法分区。

### 2. 左对齐:右侧的 0 是"无意义且可省略"的

> Vitess always converts sharding keys to a left-justified binary string for computing a
> shard. This left-justification makes the right-most zeroes insignificant and optional.

所以 `0x80`、`0x8000`、`0x8000000000000000` 是**同一个值**。这也是为什么"两分片时
`0x80` 是正中值"——它不是 8 位字节的中点,而是 64 位键空间的中点。demo 里
`canonical()` 把键右补 0 到 8 字节再比较,正是这一条的实现。

### 3. Primary Vindex:列值 → keyspace id

| vindex | `Hash` 实现 | 源码 | `Cost()` | `RangeMap` |
| --- | --- | --- | --- | --- |
| `binary` | 原样返回字节串 `id.ToBytes()` | `binary.go` | 0 | 有 |
| `numeric` | `BigEndian.PutUint64(num)` | `numeric.go` | 0 | 有 |
| `reverse_bits` | `BigEndian(bits.Reverse64(num))` | `reverse_bits.go` | 1 | **无** |
| `hash` | 空密钥 DES 加密大端 8 字节 | `hash.go` | 1 | 无 |

`Cost()` 是官方给 vindex 标注的查询代价(`binary`/`numeric` 为 0,`hash`/`reverse_bits`
为 1)。`RangeMap` 决定 `BETWEEN` 能否被映射成键范围——源码里 `ReverseBits` 的 var 块
**没有** `Sequential`,因此它不支持范围映射,`BETWEEN` 只能退化成 scatter。

`hash` 依赖 DES 分组密码(注释说明:早期用 3DES,空密钥下与 DES 完全等价)。本 demo
不重造 DES,只在 `vindexes.HASH_DOC` 里记录其定义,避免把"我猜的散列"当成官方实现。

### 4. 三种路由形态与 fan-out

| 形态 | 触发条件 | fan-out |
| --- | --- | --- |
| 单分片 | Primary Vindex 是 unique 且给了具体值 | 1 |
| 键范围 | vindex 实现 `RangeMap`(`numeric`/`binary`) | 与区间宽度有关 |
| scatter | 无分片键条件,或区间无法映射 | N |

fan-out 就是分片代价:单分片查询可以把 `LIMIT`、聚合、连接下推;scatter 必须在 VTGate
汇总,且**最慢的那个分片决定整体延迟**。

### 5. Resharding

文档原话:重新分片期间 Vitess 在新分片上复制、校验并持续追增量,**旧分片继续服务**,
切换只有几秒的只读窗口。键空间设计让这件事变简单:因为 `keyspace id` 只由分片键决定,
切分边界不影响任何一行的归属值。

## 对比:三种分片形态

| 维度 | Vitess(中间件) | Citus(扩展) | TiDB(原生分布式) |
| --- | --- | --- | --- |
| 切分单位 | keyspace + vindex(`-80`/`80-`) | 分布列哈希出的 shard | Region(按范围自动分裂) |
| 跨片连接 | VTGate 汇总,或靠 vindex 让关联表同键 | 共置(colocate_with)后下推 | 计算层下推到 TiKV |
| 小表广播 | 未分片 keyspace | reference table | 广播表 |
| 再平衡 | 手动 resharding(VReplication) | shard rebalancer | Region 自动调度 |

> 资料来源:Citus 文档 `sharding/data_modeling.html`(共置与 reference table)、
> TiDB 文档 `tidb-storage.md`(Region 与自动分裂)。三者对照只做定性说明,
> 本 demo 的可执行部分只覆盖 Vitess。

## 环境与运行

- Python 3(仅标准库)、Go 1.22(仅标准库)

```bash
python python/main.py              # 四个场景的演示输出
python python/selfcheck_sharding.py # 73 项断言
cd go && go run .                   # 同一套模型的 Go 转写
```

## 关键代码

| 文件 | 作用 |
| --- | --- |
| `python/keyranges.py` | `canonical` 左对齐、`KeyRange` 半开区间、`validate_partition`、`generate_shard_ranges`、`split_shard` |
| `python/vindexes.py` | `binary` / `numeric` / `reverse_bits` 的 `Hash` 与 `unhash` |
| `python/router.py` | `route_equal` / `route_range` / `route_unconstrained` / `reshard` |
| `go/keyrange.go` `go/vindex.go` `go/router.go` | 同上逻辑的 Go 转写(端点用 `math/big` 以容纳 2^64) |

## 性能边界与注意事项

- **fan-out 是乘法不是加法**:4 分片 scatter 一次查询要付 4 次解析 + 4 次执行 + 一次汇总;
  单分片查询可以下推的算子越多,差距越大。
- **`numeric` 不是哈希**:它是位模式映射,连续主键会全挤在键空间一端。需要均匀分布时用
  `hash`(真散列)或 `reverse_bits`(打散低位)。
- **别让区间查询落在非 Sequential 的 vindex 上**:`reverse_bits` 没有 `RangeMap`,
  `BETWEEN` 会静默退化成全分片扫描。
- **分片数按 2 的幂等分**:文档示例全按二进制位切分;非幂次在 2^64 空间上不整除,
  demo 直接报错而不是编造官方行为。
- **分区完整性要自己校验**:文档要求"必须组成完整分区",但分片名本身只是字符串;
  demo 的 `validate_partition` 会在缺端点 / 有洞 / 重叠时报错。

## 参考资料

- Vitess 官方文档《Sharding》—— <https://vitess.io/docs/reference/features/sharding/>
  (键范围语义、左对齐、`0x80` 中点、分片命名、分区完整性、resharding)
- `vitessio/vitess` `go/vt/vtgate/vindexes/numeric.go` · `binary.go` · `reverse_bits.go` · `hash.go`
  (经 `cdn.jsdelivr.net/gh/vitessio/vitess@main/...` 实读;`Cost()`、`RangeMap`、DES 注释)
- Citus 文档《Data Modeling》—— <https://docs.citusdata.com/en/stable/sharding/data_modeling.html>
- `pingcap/docs` `tidb-storage.md`(Region / 自动分裂)
