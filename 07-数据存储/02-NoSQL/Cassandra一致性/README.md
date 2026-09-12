# Cassandra 一致性级别 — Tunable Consistency 完整实现

> 权威来源:
> - DataStax [Cassandra 0.7 archived docs Consistency](https://docs.datastax.com/en/archived/cassandra/0.7/docs/consistency/index.html)
> - DataStax [0.8 archived docs About Client Requests](https://docs.datastax.com/en/archived/cassandra/0.8/docs/cluster_architecture/about_client_requests.html)
> - [Reintech blog: Best Practices for Consistency Tuning in Cassandra](https://reintech.io/blog/consistency-tuning-best-practices-cassandra) — cassandra.yaml 默认值(hinted_handoff / read_repair_chance / gc_grace_seconds)
> - let's build solutions [How Cassandra Works: Wide-Column Storage, Gossip Protocol, and Tunable Consistency](https://letsbuildsolutions.com/blog/system-design/how-cassandra-works-wide-column-storage-gossip-protocol-and-tunable-consistency/) — Merkle 树 + LOCAL_QUORUM 推荐

## 简介

Cassandra 把"对一条写/读操作需要多少副本确认"做成**每次调用可选**的旋钮,让应用在一致性与可用性间显式取舍。

关键概念(每条 1 句):
- **一致性级别(CL)**:读写时声明需要多少副本 ack 才算成功
- **复制因子(RF)**:一份数据存到几个副本(常 3)
- **强一致性公式**:`W + R > RF` ⇔ 至少一个读副本必含最新写
- **LOCAL_QUORUM**:多 DC 部署时的"本地多数派",避免跨 DC RTT
- **Read Repair**:读时检查多副本并补写(消除短期不一致)
- **Anti-Entropy Repair (Merkle tree)**:后台异步,修长期间累积的副本分歧
- **Lightweight Transaction (LWT)**:基于 Paxos 的 4 阶段 prepare/commit,4-5x 慢于普通写
- **Hinted Handoff**:目标副本 down 时,转交给下一个健康节点并打 hint,目标恢复后投递

## 原理详解

### 1. 一致性级别 8 种(DataStax 官方分类表)

| CL | 写语义 | 读语义 |
| --- | --- | --- |
| `ANY` | 至少 1 副本确认(含 hint-only)| 不用于读 |
| `ONE` | 1 副本确认 | 取最快返回的副本数据 |
| `QUORUM` | `⌊RF/2⌋+1 = 2` 副本(RF=3) | 最近时间戳的数据,2 副本收到返回 |
| `LOCAL_QUORUM` | 本 DC 内 quorum(默认推荐) | 本 DC 内多数 |
| `EACH_QUORUM` | 每个 DC 都达到 quorum | 每个 DC 都 quorum |
| `ALL` | 全部 RF 副本;否则失败 | 全部 RF 副本;否则失败 |
| `TWO` / `THREE` | RF > 3 时专用 | RF > 3 时专用 |

### 2. 强一致性公式

```
W + R > RF   ⇔   强一致(linearizable quorum)
W + R ≤ RF   ⇔   最终一致,读可能 stale
```

举例(RF=3):
- QUORUM 写 + QUORUM 读 = 2+2 > 3 ✅ 强一致
- ALL 写 + ONE 读 = 3+1 > 3 ✅ 强一致(写慢,读快)
- ONE 写 + ONE 读 = 1+1 ≯ 3 ❌ 最终一致

### 3. 协调者(coordinator)处理写

```
client → coordinator (任意节点)
       │
       ▼
1. coordinator 用 partitioner (Murmur3 / RandomPartitioner) 算 hash
2. 找到 key 的 preference list (Ring 上 N=RF 个不同节点,顺时针)
3. 把写请求发到前 W 个活着的节点
4. 等待 W 个 ack
       │
       ▼
5. 若偏好节点有 down,把写转交下一个健康节点 + 写一条 hint(intended=down_node, cell)
```

### 4. Hinted Handoff 精解

Cassandra 默认参数 (`cassandra.yaml`):

```yaml
hinted_handoff_enabled:        true
max_hint_window_in_ms:         10800000   # 3 小时
hinted_handoff_throttle_in_kb: 1024
```

工作流:
- 节点 down 时,coordinator 把数据写到 ring 上下一个**健康**节点,加 `hint` 标记
- 默认 3 小时窗口;过期 hint 丢弃
- 节点恢复 → 启动 `HintedHandoff` 任务扫描 hint 队列 → 投递到原目标节点 → 删除 hint
- **不依赖** anti-entropy repair;但 hint 不是 repair 的替代 — 大窗口下仍需手动 repair

### 5. Read Repair & Anti-Entropy

**Read Repair(读时)**:
- CL=QUORUM 读 2 个,coordinator 还会异步查第 3 个
- 若第 3 个过时,后台补写(默认 `read_repair_chance: 0.1` 即 10% 概率触发)
- 适用于**短窗口**不一致修正

**Anti-Entropy Repair(后台)**:
- 用 `nodetool repair` 触发
- 每个副本对自己的 key range 建 **Merkle tree**(每片是一段 key range)
- 树对比:根哈希相同 ⇒ 无差异;不同 ⇒ 逐层下钻到范围最小差异,**只同步差异 keys**
- 必须每 `gc_grace_seconds` (默认 10 天) 跑完一次
- 4.0+ 增量 repair:已修过的 SSTable 标记,后续只扫未修部分

### 6. Lightweight Transaction (LWT / Paxos)

Cassandra 用 Paxos 4 阶段做 LWT:

```
prepare (Phase 1a):  coordinator 选 ballot = max(microsecond_now, seen_ballot) + 1
          ↓
promise (Phase 1b):  replicas 承诺不接受 < ballot 提议,并返回"先前 accepted value"
          ↓
propose (Phase 2a):  coordinator 用 ballot 把 value 广播
          ↓
accept (Phase 2b):   replicas 接受并持久化 (ballot, value)
          ↓
commit:               coordinator 看到多数 accept 后 commit,异步通知所有 replicas learn
```

- 代价:4-5x 普通写延迟(多个 round-trip)
- 应用:**唯一性约束**(IF NOT EXISTS)、**库存超卖防双扣**、**分布式锁**
- ballot 必须单调递增,否则无法形成 quorum

## 核心 API(Cassandra CQL 表达)

```sql
-- 普通写入,CL=QUORUM
INSERT INTO users (id, name) VALUES (1, 'alice') USING CONSISTENCY QUORUM;

-- 读,CL=LOCAL_QUORUM(推荐)
SELECT * FROM users WHERE id = 1 USING CONSISTENCY LOCAL_QUORUM;

-- LWT:IF NOT EXISTS
INSERT INTO users (id, name) VALUES (1, 'alice') IF NOT EXISTS;

-- 触发后台 repair
nodetool repair -pr -par my_keyspace
```

## 对比 / 选型

| 维度 | Cassandra | DynamoDB | MongoDB | Riak |
| --- | --- | --- | --- | --- |
| 一致性配置粒度 | 每次调用 | 每次调用 | 每次调用 | 每次调用 |
| 推荐默认 | LOCAL_QUORUM | 默认 strong | 默认 majority | 默认 quorum |
| 多 DC 优化 | LOCAL_QUORUM / EACH_QUORUM | Global Tables | 多数派跨区 | 默认 vector clock |
| LWT / 唯一性 | Paxos LWT | 条件表达式 | 唯一索引 | pre/post commit hook |
| 调优旋钮数 | 多(hint/repair/gc_grace) | 受限 | 受限 | 多 |

## 环境

- **C**: gcc / clang (`-O2`); 仅标准 C 库
- **Python**: 3.10+; 仅标准库
- **Go**: 1.20+

## 运行

### C

```bash
gcc -O2 -Wall -Wextra -pedantic c/cassandra_cl.c -o c/cassandra_cl
./c/cassandra_cl
# 输出 3 个场景:3活/2活/0活 时各 CL 的 OK/FAIL 决策
# + 强一致性公式 4 组合 + cassandra.yaml 关键参数
```

### Python

```bash
cd python
python3 cassandra_cl_demos.py
# 5 个 demo: CL 表头 / W+R>RF / 写读 + read repair / LWT / Anti-Entropy Repair
#
# 文件结构:
#   cassandra_cl.py        (数据/集群模型, ~216 行)
#   cassandra_cl_demos.py  (demo 入口, ~112 行)
```

### Go

```bash
cd go
go run cassandra_cl.go
# 同 C 的 3 场景 + 强一致性公式 + 默认参数表
```

## 关键代码片段

### CL 决策核心(Python)

```python
def required_acks(cl, rf, dc_count=1):
    if cl == CL_ANY:        return 1
    if cl == CL_ONE:        return 1
    if cl in (CL_QUORUM, CL_LOCAL_QUORUM):
        return rf // 2 + 1          # RF=3 → 2
    if cl == CL_EACH_QUORUM:
        return (rf // 2 + 1) * dc_count
    if cl == CL_ALL:        return rf
```

### Sloppy + hint 队列(Python)

```python
# coordinator 偏好 3 个,有 down → 把 hint 写给"ring 上下一个健康节点"
for pref_node in preference_list:
    if pref_node.alive:
        coordinator.write(pref_node, cell)
    else:
        # sloppy: 转交 hint 接收者
        hint_receiver.store.put_hint(intended_for=pref_node, cell=cell)
```

### Read Repair(Python)

```python
# 读 QUORUM=2;同时让 coordinator 偷偷查第 3 副本做后台补写
winner = max(c for c in collected if not c.tombstone)
for idx in preference_list:
    if replica[idx].latest_ts < winner.ts:
        replica[idx].write(winner)   # 后台补写,消除短期不一致
```

### LWT(Paxos ballot 单调递增,Cassandra 关键)

```python
ballot = max(microsecond_now, seen_max_ballot) + 1
prepare  →  replicas.promise(ballot=ballot, prev_value=...)
propose  →  replicas.accept(ballot=ballot, value=v)
commit   →  majority accept ⇒ safe to commit
```

### Merkle 树对比(Python 简化版)

```python
def merkle_digest(replica, keys):
    items = []
    for k in sorted(keys):
        cell = replica.read(k)
        v = cell.value if cell and not cell.tombstone else "<tombstone>"
        items.append(f"{k}={v}")
    return sha256("|".join(items)).hexdigest()[:16]
# 两副本 digest 不同 ⇒ 逐 key 同步;相同 ⇒ 无须
```

## 性能与边界

| 项 | 量级 |
| --- | --- |
| 单 CL=ONE 写延迟 | ~毫秒级(单 DC) |
| CL=QUORUM 延迟 | ≈ p99 of 2 个副本,通常 5-15 ms |
| CL=ALL 延迟 | ≈ 最慢副本 p99 |
| LWT 写延迟 | 4-5x 普通写 |
| Read repair 同步时间 | 后台,几十 ms 级 |
| Anti-entropy repair | 全节点 TB 级数据小时级 |
| Hint 窗口 | 3h 默认,超长 drop |
| gc_grace_seconds | 10d 默认;超期 tombstone 提前 purge 风险数据复活 |

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 写 LOCAL_QUORUM 反而比 ONE 还快 | ONE 命中慢副本 | 集群不均衡时常见 |
| Read repair 滞后明显 | `read_repair_chance=0.1` 概率低 | 调高 0.2-0.3 或用 anti-entropy |
| Anti-entropy repair 后"删除数据复活" | tombstone 在一个节点被 purge,另一节点 SSTable 含旧版本 | 严格 `gc_grace_seconds` 内全节点完成 repair |
| `nodetool repair` 影响生产 | 全节点全 range 重算 Merkle | 用 -pr(parallel ranges)+ -par(partitioner);分阶段 |
| Hint 队列堆积 | 节点 down 超 3h | 临时拉宽窗口;或禁用 + 用抗熵 |
| LWT 严格顺序要求 | prepare/propose 必须 round-trip | 单 partition key 串行;高并发场景 LWT 不是银弹 |
| 用 `QUORUM` 而不是 `LOCAL_QUORUM` 跨 DC | 跨 DC RTT 拖尾 | 多 DC 必用 LOCAL_QUORUM |
| read repair 阻塞 | chance 累积调用 | 异步版 `dclocal_read_repair_chance` 不阻塞 |

## 参考资料(实际阅读过的权威链接)

- [DataStax archived 0.7 docs: Consistency Levels table](https://docs.datastax.com/en/archived/cassandra/0.7/docs/consistency/index.html) — 写 6 种 / 读 7 种 CL 的官方分类表
- [DataStax archived 0.8 docs: About Client Requests](https://docs.datastax.com/en/archived/cassandra/0.8/docs/cluster_architecture/about_client_requests.html) — coordinator 路径 + read repair + Hinted Handoff 流程
- [Reintech blog: Best Practices for Consistency Tuning](https://reintech.io/blog/consistency-tuning-best-practices-cassandra) — `cassandra.yaml` 关键配置项 + LWT 适用边界 + 多 DC 决策
- [let's build solutions: How Cassandra Works (Wide-Column / Gossip / Tunable Consistency)](https://letsbuildsolutions.com/blog/system-design/how-cassandra-works-wide-column-storage-gossip-protocol-and-tunable-consistency/) — 漫画式流程图 + Merkle 树差异对比
