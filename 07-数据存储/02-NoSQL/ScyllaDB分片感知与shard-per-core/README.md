# ScyllaDB 分片感知驱动与 shard-per-core

## 一、简介

ScyllaDB 跑在 Seastar 上，每个 CPU 核是一个独立的 shard，各自独占一段内存与一个 reactor；
**请求不该跨核搬运数据**——这就是 shard-per-core 的全部动机。驱动若想把一次读写直接送到
"拥有该 partition 的那个核"，就必须自己能把 partition key 算成 token、再由 token 算出 shard，
否则请求只能落到节点上的**任意**一个核，再由它内部转发一次（多一跳 IPC + 一次跨核队列）。

本 demo 从官方源码里抠出 `token -> shard` 的映射（`dht/token.cc`），并把「是否分片感知」的代价量化。

## 二、原理

### 2.1 token 是 int64，先 unbias 成 uint64

`dht::token` 的数据部分是 `int64_t`，范围 `[-2^63, 2^63-1]`。做分片计算前先把它挪到无符号区间：

```cpp
constexpr uint64_t unbias() const { return uint64_t(_data) + uint64_t(INT64_MIN); }
```

C++ 里这是无符号回绕（`+2^63`），Python 侧要 `& (2^64-1)`，Go 侧 `uint64(x) + uint64(math.MinInt64)` 天然回绕。

### 2.2 主函数：把 token 当 [0,1) 的小数

```cpp
inline unsigned zero_based_shard_of(uint64_t token, unsigned shards, unsigned msb) {
    token <<= msb;                          // uint64 左移，高位直接丢弃
    return (uint128_t(token) * shards) >> 64;
}
```

即 `shard = floor((token / 2^64) * shards)`。**关键在于那句注释：这是 master function，
逆函数必须和它的取整误差对得上**——所以逆函数（2.4）要逐格 `+1` 修正。

`msb`（`sharding_ignore_msb_bits`）把最高的若干位**左移丢弃**，等价于把环等分成 `2^msb` 段后
每段内部重新编号；同一个 token 在不同 msb 下会落到完全不同的 shard（本 demo 实测 msb=0/1/2/4 分别是 5/2/4/0）。

### 2.3 三态分派

```cpp
unsigned shard_of(unsigned shard_count, unsigned msb, const token& t) {
    case before_all_keys: return token::shard_of_minimum_token();  // 恒 0，源码注释 "hardcoded for now"
    case after_all_keys:  return shard_count - 1;
    case key:             return zero_based_shard_of(unbias(t), shard_count, msb);
}
```

`maximum token` 落到**最后一个** shard，`minimum token` 落到 shard 0——两者都不是走哈希路径。

### 2.4 逆函数：每个 shard 的起始 token

```cpp
for (s : iota(0u, shards)) {
    uint64_t token = (uint128_t(s) << 64) / shards;
    token >>= msb;
    while (zero_based_shard_of(token, shards, msb) != s) ++token;   // 修正取整误差
    ret[s] = token;
}
```

`shards == 1` 有特判（直接返回 `{0}`），否则下面的 while 会在"两个不存在的 shard 之间找边界"时迷路。

### 2.5 找下一个 shard 的边界：不回绕

`token_for_next_shard` 只在**目标 shard 严格在当前 shard 之后**时才给出边界，否则返回 `maximum_token()`：

```cpp
n = shard_start[shard];
if (spans > 1 || shard <= s) return maximum_token();   // 溢出
return bias(n);
```

所以 `next_shard()` 从最后一个 shard 出发会拿到 maximum，进而返回 `nullopt`——**调用方必须自己处理回绕**。
`spans > 1` 也一律判溢出（只有 msb>0 的分支才真正支持 spans）。

### 2.6 tablet 迁移期：一次写要落两个 shard

`shard_replica_set` 是 `static_vector<unsigned, 2>`，注释写明「节点内 tablet 迁移期间，本地写要同时
复制给旧 shard 和新 shard」。`shard_for_reads()` 在无处可去时返回 **0**（源码 FIXME 说这其实是协调错误），
而 `shard_for_writes()` 返回**空集合**——读和写的空值语义不同。

### 2.7 fixed_shard_partitioner：把 shard 直接编进 token

给 Raft 元数据表用的分区器，token 布局 `[shard:16][hash:48]`：

```cpp
token_for_shard(shard, hash) = (shard << 48) | (hash & ((1<<48)-1));
shard_of(token)              = token >> 48;
```

顶层位恒 0（省掉 bias 转换），所以 shard 实际只有 15 位可用，`max_shard = 32767`。
sharder 里还有一层 clamp：`std::min(shard, shard_count - 1)`。

## 三、对比

| | 分片感知驱动 | 非分片感知驱动 |
| --- | --- | --- |
| 连接建立 | 每个 shard 一条连接，按 token 选 | 每个节点一条/若干条，随机落核 |
| 命中 owner shard 的概率 | 1 | 1 / shard 数 |
| 额外代价 | 无 | 一次跨核转发（本 demo 模型里 8 shard 时约 87.5% 的请求要转发） |
| 前提 | 驱动要实现 murmur3 + `shard_of` | 无 |

## 四、环境

- Python 3.8+（仅标准库）；Go 1.21+（仅标准库，用到 `math/big` 做 128 位乘法）
- 无需安装 ScyllaDB

## 五、运行

```bash
cd python && python selfcheck_sharding.py   # 49 条断言
cd python && python main.py                 # 路由代价与映射演示
cd go     && go run .                       # 同一模型的 Go 实现
```

## 六、关键代码

| 文件 | 内容 |
| --- | --- |
| `python/sharding.py` | Token 三态 + `zero_based_shard_of` + `init_zero_based_shard_start` + static_sharder + fixed_shard |
| `python/selfcheck_sharding.py` | 49 条断言（含 shards=3 的 +1 修正手算值） |
| `python/main.py` | 分片感知 vs 非分片感知的转发率模型 |
| `go/sharding.go` | 同逻辑 Go 转写，128 位乘法走 `math/big` |
| `go/main.go` | 同一代价模型 |

## 七、性能边界

- shard 数 = CPU 核数（Seastar 每核一 shard）；核数越多，**非分片感知的命中率越低**（`1/nproc`）
- `zero_based_shard_of` 是 O(1) 整数运算，但 `init_zero_based_shard_start` 里的 while 修正最坏要跑很多轮——仅在 sharder 构造时执行一次
- `msb > 0` 时每段只有 `2^(64-msb)` 个 token 参与编号，分段数 = `2^msb`
- 本 demo 的路由模型是**确定性近似**：token 源用 blake2b/FNV 代替 murmur3，节点选择用均匀 vnode 切片，
  **不代表真实集群的转发率**，只用来说明 `1/shards` 这个量级

## 八、坑

1. **`token_for_next_shard` 不回绕**：`shard <= s` 时返回 maximum，不是返回下一轮的边界。想回绕要自己再算一遍。
2. **逆函数必须逐格修正**：`(s << 64) / shards` 除法向下取整，在 shards 不整除时会落到前一个 shard
   （shards=3 时 `2^64/3` 的 floor 乘回去是 `2^64-1`，仍属于 shard 0，必须 +1）。
3. **minimum / maximum token 不走哈希**：`shard_of` 对这两个哨兵是硬编码的 0 和 `shard_count-1`，
   拿普通 key 的直觉去推会错。
4. **Go 没有 uint128**：`(uint128_t(token) * shards) >> 64` 必须用 `math/big`，直接 `uint64 * uint64` 会溢出。
5. **C++ 的 `token <<= msb` 是 uint64 左移**：高位是被**丢弃**而不是保留，语义上是"忽略最高 msb 位"，不是缩放。
6. **`fixed_shard` 的 shard 只有 15 位**：顶层位留给符号位规避 bias，最大 32767；且 sharder 还会 clamp 到实际 shard 数。
7. **读写的空值语义不同**：读是 0，写是空集合，照抄其中一个会漏判"本节点不拥有该 token"。

## 九、参考资料（均为本轮实际读取）

- `scylladb/scylladb@master` `dht/token.cc` — `zero_based_shard_of` / `init_zero_based_shard_start` / `shard_of` / `token_for_next_shard`：
  https://github.com/scylladb/scylladb/blob/master/dht/token.cc
- `dht/token.hh` — `unbias()` / `bias()` / `shard_of_minimum_token()`：
  https://github.com/scylladb/scylladb/blob/master/dht/token.hh
- `dht/token-sharding.hh` — `cpu_sharding_algorithm_name()` 返回 `"biased-token-round-robin"`、`shard_replica_set` 容量 2：
  https://github.com/scylladb/scylladb/blob/master/dht/token-sharding.hh
- `dht/i_partitioner.cc` — `static_sharder::shard_of` / `next_shard` / `selective_token_range_sharder`：
  https://github.com/scylladb/scylladb/blob/master/dht/i_partitioner.cc
- `dht/fixed_shard.cc` 与 `dht/fixed_shard.hh` — `[shard:16][hash:48]` 编码、clamp 规则：
  https://github.com/scylladb/scylladb/blob/master/dht/fixed_shard.cc ·
  https://github.com/scylladb/scylladb/blob/master/dht/fixed_shard.hh
- `dht/murmur3_partitioner.cc` — `get_token` 取 `hash3_x64_128` 的 `hash[0]`；与 Cassandra 的「空 key 返回 minimum_token」不兼容：
  https://github.com/scylladb/scylladb/blob/master/dht/murmur3_partitioner.cc
