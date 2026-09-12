# Redis Cluster：16384 哈希槽 + Gossip 协议 + 故障转移

## 简介

Redis Cluster 是 Redis 内置的分布式方案，把数据按 **16384 个哈希槽（hash slot）** 自动分片到多主节点；每个主节点可有 N 个从节点做异步复制 + 故障转移。所有节点通过独立的集群总线（TCP 端口 + 10000）互联，使用 **Gossip 协议** 传播状态，并基于 `configEpoch` 解决脑裂。

**关键概念**
- **Hash Slot**：16384 个逻辑槽位，键通过 `CRC16(key) mod 16384` 映射到一个槽。
- **Hash Tag**：`{user1000}.following` 与 `{user1000}.followers` 的花括号内字符串相同 → 强制同槽，支持多键操作。
- **Gossip**：节点每秒向若干随机节点发 ping，附带自身所知的若干节点信息（节点 ID / IP / 标志），形成最终一致的拓扑视图。
- **Node ID**：160-bit 随机数（SHA-1-like），节点重启后不变；用 ID 寻址而非 IP，避免 IP 漂移导致路由错乱。
- **Config Epoch**：每次故障转移 +1 的全局单调递增版本号，用「最后一次故障转移获胜」规则仲裁脑裂。

**历史背景**：Redis Cluster 正式随 Redis 3.0（2015）发布；gossip 协议、CRC16 XMODEM、160-bit node ID 都由 antirez 团队设计；后续 4.x/5.x/6.x 增加副本迁移、cluster-port 配置、rack-awareness。

## 原理详解

### 哈希槽分配

```text
HASH_SLOT = CRC16(key) mod 16384
```

CRC16 算法为 **XMODEM**（即 CRC-16/ACORN）：宽度 16-bit、多项式 1021（x¹⁶+x¹²+x⁵+1）、初值 0000、不反演输入/输出；"123456789" 的 CRC = `0x31C3`。Redis 只取 CRC16 高 16 位中的 14 位（`& 16383`）覆盖全部 16384 槽。

**为什么是 16384？**（Redis 集群规范原文）
> "The reason why 16384 slots, and not 65536 for example, is that while CRC16 produces 16-bit values, having 65536 slots per node would be useless since it would make the bitmap of slots per node too heavy (16384/8 = 2KB, 65536/8 = 8KB), and the slot resolution is done by just looking at the bits, not by an expensive operation."

### 哈希标签（Hash Tag）

如果键中存在 `{...}` 子串且花括号内有字符，则**只对花括号内内容**计算 CRC16：

```text
{user1000}.following  → CRC16("user1000") = slot 13037
{user1000}.followers  → CRC16("user1000") = slot 13037   ← 同槽

foo{}bar              → CRC16("foo{}bar") (无有效 tag)
foo{{bar}}zap         → CRC16("{bar")
```

这样可以让多键操作（MGET、事务、Lua）作用于同一个槽。

### 节点 ID 与寻址

```text
node-id: 40 字节十六进制 (160-bit SHA-1-like)
例:      97a3a64667477371c4479320d683e4c8db5858b1
```

节点 ID 在启动时由 `/dev/urandom` 生成并持久化到 `cluster-config-file`（nodes.conf）。重启不变。IP/端口可变不影响寻址。

### Gossip 协议

每个节点每秒向若干随机节点发送 ping 包。ping 包结构：

```text
┌─────────────────────────────────────────────────────┐
│ 通用头部 (header)                                     │
│   - 节点 ID (160-bit)                                │
│   - currentEpoch, configEpoch                        │
│   - flags (master/replica/pfail/fail)                │
│   - slot bitmap (我服务的槽)                           │
│   - TCP base port, cluster port                      │
│   - cluster state (down/ok)                          │
│   - master ID (若是 replica)                          │
├─────────────────────────────────────────────────────┤
│ Gossip 部分                                          │
│   - N 个其他节点的信息 (节点 ID, IP, port, flags)       │
│   - N ≈ 集群大小的 1/10                              │
└─────────────────────────────────────────────────────┘
```

接收方根据 gossip 信息更新本地视图；当发现某节点被多数主节点标记为 `PFAIL` 时升级为 `FAIL`，触发副本选举。

### 故障转移（Failover）

```text
┌──────────────────────────────────────────┐
│ 1. 节点 X 被多数主节点标记为 FAIL         │
│ 2. X 的 replica 之一启动选举:            │
│      currentEpoch += 1                   │
│      向所有 master 发 FAILOVER_AUTH_REQUEST│
│ 3. Master 收到请求,在 NODE_TIMEOUT *2   │
│    内只能给该 master 的 replica 投一票   │
│ 4. 获得多数票的 replica 当选:            │
│      configEpoch = max(existing) + 1     │
│      自升级为 master,广播新 config       │
└──────────────────────────────────────────┘
```

**Replica 排名延迟**（避免同时选举）：

```text
DELAY = 500 ms + random(0, 500 ms) + REPLICA_RANK * 1000 ms
```

复制偏移量最新的 replica 排名 0，最先尝试当选。

### MOVED / ASK 重定向

客户端可向任意节点发命令：

| 错误 | 含义 | 客户端动作 |
|---|---|---|
| `-MOVED slot dest` | 该槽永久不在本节点 | 永久路由到 `dest` |
| `-ASK slot dest` | 该槽正在 resharding 临时迁入 | 仅本次路由到 `dest`，刷新后仍按 MOVED 表 |

## 对比 / 选型

| 维度 | Redis Cluster | Codis / Twemproxy | Client-side Sharding |
|---|---|---|---|
| 中心代理 | 无 | 有 (proxy) | 无 |
| 协议层 | 原生 RESP | 兼容 RESP + zk/etcd | 客户端自己维护映射 |
| 故障转移 | 内置 replica 自动 | proxy 探测 | 客户端探测 |
| 多键操作 | 仅同槽 | proxy 层支持 | 客户端负责 |
| 运维成本 | 中 | 高 (zk/etcd + proxy) | 低 (需业务侵入) |

## 环境准备

- 操作系统：Linux / macOS / WSL（演示使用纯 Python/Go，CRC16 + 模拟 Gossip 广播，不依赖真实 Redis 节点）
- 语言版本：C11 / Python 3.8+ / Go 1.18+
- 依赖：无第三方依赖

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -pedantic c/redis_cluster.c -o demo
./demo
```

### Python

```bash
python3 python/redis_cluster.py
```

### Go

```bash
cd go && go run redis_cluster.go
```

## 关键代码片段

### C：CRC16/XMODEM 表 + slot 计算

```c
/* 参考 Redis 源码 redis/src/crc16.c (XMODEM variant) */
static const uint16_t crc16tab[256] = { /* …256 项… */ };

uint16_t crc16(const char *buf, int len) {
    uint16_t crc = 0;
    for (int i = 0; i < len; i++)
        crc = (crc << 8) ^ crc16tab[((crc >> 8) ^ buf[i]) & 0xFF];
    return crc;
}

int slot_for_key(const char *key, int keylen) {
    /* 简化:忽略 hash tag,直接对整 key CRC16 */
    return crc16(key, keylen) & 16383;   /* 0..16383 */
}
```

### Python：16384 槽分配 + Gossip 心跳

```python
SLOTS = 16384
EPOCH = 0   # currentEpoch,故障转移 +1

def assign_slots(nodes: list[str]) -> dict[int, str]:
    """把 16384 槽均分给 master 列表。"""
    n = len(nodes)
    per, rem = divmod(SLOTS, n)
    return {s: nodes[i] for i in range(n) for s in range(i*per, (i+1)*per)}

def gossip_tick(self_id, view: dict) -> bytes:
    """生成 ping 包:头部 + N 个 gossip 项"""
    # 选 1/10 的已知节点作为 gossip 项
    gossip_n = max(1, len(view) // 10)
    sample = random.sample(list(view.keys()), gossip_n)
    return pickle.dumps({"node": self_id, "epoch": EPOCH, "gossip": sample})
```

### Go：副本选举 + configEpoch 单调递增

```go
func (r *Replica) TryFailover(masters []*Node) bool {
    r.currentEpoch++                                  // 选举请求 epoch
    votes := 0
    for _, m := range masters {
        if m.VoteFor(r.masterID, r.currentEpoch) {
            votes++
        }
    }
    if votes > len(masters)/2 {                       // 多数票
        r.configEpoch = maxConfigEpoch(masters) + 1   // 全局单调
        r.Promote()
        return true
    }
    return false
}
```

## 性能与边界

- **节点上限**：16384 槽决定理论最大 16384 主节点，规范建议 ≤ 1000。
- **Gossip 带宽**：100 节点集群约 330 ping/s 全网（每节点 99 ping / 30 秒），每节点流量可控。
- **故障检测时间**：`cluster-node-timeout` 默认 15 秒，可调小到 1 秒但会因网络抖动误判。
- **重定向开销**：MOVED 仅在客户端首次访问错节点时返回一次；缓存映射后接近 0 开销。
- **平台差异**：Docker / NAT 需 `--net=host`，否则广播的 IP 不可达。

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
|---|---|---|
| 集群启动报 "All 16384 slots covered" | 启动后未 `CLUSTER ADDSLOTS` | 用 `redis-cli --cluster create` 一次性分配 |
| 客户端报错 CROSSSLOT | 多 key 命令跨了多个槽 | 用 hash tag `{user1000}.profile / .account` |
| 节点间 ping 大量超时 | 集群总线端口 (数据端口 + 10000) 未开放 | 防火墙放行数据端口与总线端口 |
| 副本选举反复失败 | `cluster-node-timeout` 太短 + 网络抖动 | 调大超时；开启 `cluster-slave-validity-factor` |
| Docker 中 node announce IP 是容器内 IP | 默认 announce 用 127.0.0.1 | 设 `cluster-announce-ip` 为宿主机 IP |
| 重启后 configEpoch 回滚 | 误操作 `CLUSTER RESET` | 仅在删全数据时用；生产用 `CLUSTER FORGET` |

## 参考资料（实际阅读过的权威来源）

- [Redis cluster specification (Official, 英文)](https://redis.io/docs/reference/cluster-spec/) — 哈希槽、CRC16、节点 ID、gossip、故障转移原文规范
- [Redis 集群规范 (Official, 中文)](https://redis.ac.cn/docs/latest/operate/oss_and_stack/reference/cluster-spec/) — 同源中文版，含附录 A CRC16 完整参考实现
- [Redis Scalability: Clustering, Sharding, and Hash Slots (Official Learn)](https://redis.io/learn/4-scalability) — 16384 槽的工程解释与对比
- [使用 Redis 集群进行扩缩容 (Official 中文)](https://redis.ac.cn/docs/latest/operate/oss_and_stack/management/scaling/) — 主从模型 / 故障转移 / 配置参数