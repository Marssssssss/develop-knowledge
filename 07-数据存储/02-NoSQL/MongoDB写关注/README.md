# MongoDB 副本集与 writeConcern / readConcern — 完整最小实现

> 权威来源:
> - [MongoDB manual: Write Concern](https://www.mongodb.com/docs/manual/reference/write-concern/) — `w` / `j` / `wtimeout` 三选项
> - [MongoDB manual: Read Concern](https://www.mongodb.com/docs/manual/reference/read-concern/) — local/available/majority/linearizable/snapshot 五级
> - [MongoDB source 5.0 Compatibility: Implicit Default Write Concern](https://www.mongodb.com/docs/release-notes/5.0-compatibility/) — 默认公式含 P-S-A trap
> - [MongoDB GitHub: src/mongo/db/repl/README.md](https://github.com/mongodb/mongo/blob/f1ec9a14cf02a08047a9741b8a60633e6677cdbd/src/mongo/db/repl/README.md) — Replication Internals(OpObserver / oplog / commit point)
> - [tech-interview.dev: Data replication across nodes](https://tech-interview.dev/tech/mongodb/replica-sets) — writeConcern / readConcern / rollback 综合

## 简介

MongoDB 副本集(replica set)是**单 primary + 多 secondary + 可选 arbiter** 的异步复制集群,以 oplog(capped collection)作主备同步总线,通过 writeConcern 与 readConcern 显式控制持久性与可见性。

关键概念(每条 1 句):
- **primary / secondary / arbiter**:primary 接受写,secondary 拉 oplog 跟上,arbiter 仅投票不存数据
- **oplog.rs**:local 库的 capped collection,记录所有写操作,secondaries 通过 tailable cursor 拉取
- **commit point**:所有数据承载成员 lastApplied ts 的最小值,作为多数派已持久化的截止线
- **writeConcern `{ w, j, wtimeout }`**:`w` 要多少副本确认、`j` 是否等 journal 落盘、`wtimeout` 等多久
- **readConcern 5 级**:local / available / majority / linearizable / snapshot,逐级变强
- **回滚(rollback)**:primary failover 后,旧 primary 上未达多数派的写被 revert,以保证不丢已确认数据
- **因果一致会话(causal session)**:驱动自动维护 operationTime + afterClusterTime,实现 read-your-own-writes

## 原理详解

### 1. 副本集成员与角色

```
┌────────────────────┐
│     PRIMARY        │←── 接受所有写
│  oplog.rs (capped) │      OpObserver 钩子写 oplog
└─────┬───┬───┬──────┘
      │   │   │  tailable cursor pull
      ▼   ▼   ▼
┌────────┐ ┌────────┐ ┌────────┐
│SECOND. │ │SECOND. │ │ARBITER │←── 仅投票;不存数据;不参与副本修复
│ priority│ │ priority│ │ priority 0
└────────┘ └────────┘ └────────┘

voting total = primary + 可投票 secondary + arbiter
                  (priority > 0 且非 hidden)
```

选举规则:获得多数派投票的候选成为新 primary;只有 priority > 0 的成员可被选。

### 2. 写路径(OpObserver → oplog → secondaries)

```
1) 客户端 → primary: insert/update/delete
2) primary 应用写到自己数据文件 (WiredTiger)
3) OpObserver 钩子把操作重写为幂等形式(insert / $set 替代 $inc)
   → 写入 oplog.rs
4) secondary 通过 tailable cursor 拉取 oplog 条目
5) secondary 应用(幂等重放)→ 更新 lastApplied ts
6) 多数派都拉到这条 → commit point 推进
7) writeConcern 等 w 个 ack 才返回客户端
```

**oplog.rs 是 capped collection**:
- 默认 5% 磁盘空间,满了就滚动覆盖最旧
- 索引:`{ ts: 1 }`,secondaries 按时间戳找新条目
- 每条长这样:`{ ts: Timestamp(秒,序数), t: long, h: long, op: 'i'/'u'/'d'/'c', ns, o2, o }`

### 3. writeConcern 三轴

**`w`** — 多少副本确认才能返回:

| w 值 | 含义 |
| --- | --- |
| `0` | 不等待 ack(已废弃,fire-and-forget) |
| `1`(默认 w/o j) | primary 落内存即返 |
| `N`(数值) | 至少 N 个副本落到内存 |
| `"majority"`(隐式默认 5.0+) | 数据承载 voting 多数派确认 |

**`j`** — 是否要求落盘 journal:
- `j: true` ⇒ ack 前 fsync journal
- 与 `writeConcernMajorityJournalDefault` 配合,`w:"majority"` 隐式 `j:true`

**`wtimeout`** — 等待 ack 的最大毫秒数(默认无限阻塞)
- `w ≤ 1` 时不生效
- 超时返回 write concern error,但**不回滚已成功的修改**

### 4. 隐式默认 writeConcern(5.0+ 公式)

```
defaultWriteConcern =
  if (voting > 0 AND data_bearing_voting <= majority_of_voting)
     { w: 1 }
  else
     { w: "majority" }

where:
  majority_of_voting = floor(voting/2) + 1
  data_bearing_voting = voting - arbiters
```

陷阱:**P-S-A(3 节点 1 arbiter)**:
- voting = 3,arbiters = 1,non-arbiters = 2,majority = 2
- data_bearing(2) ≤ majority(2) → 隐式 **w:1**!
- 这意味着 {w:1} 主上未复制的写仍然可能发生回滚

### 5. readConcern 5 级语义(从弱到强)

| RC | 语义 | 主要场景 | 持久化保证 |
| --- | --- | --- | --- |
| `local`(默认) | 节点当前最新 | 低延迟普通读 | 数据可能被回滚 |
| `available` | 同 local,但**分片**只允许此 RC 时取最快 | 分片 cluster | 可能孤立文档 |
| `majority` | 已 commit 的最新 | 强一致读 | 不可回滚 |
| `linearizable` | 强一致读,等并发写 | 单文档读己写(只 primary) | 不可回滚 |
| `snapshot` | 事务快照 | 多文档事务 | 不可回滚 |

**关键约束**:
- `linearizable` 仅可在 **primary** 上指定,且读操作必须用 unique index 精确定位单文档
- `snapshot` 仅在**事务内**使用(WiredTiger 必需);事务需以 `w:"majority"` 提交
- `available` 不能用于因果一致会话;`linearizable` 同上

### 6. Commit Point 与回滚

```
T1     T2     T3     T4     T5     T6     T7     <- oplog ts
^^                              ^
primary                secondary_1 applied

commit_point = min(lastApplied_ts) across voting data-bearing members
```

- secondary 必须 `lastApplied ts ≥ commit_point` 才能被晋升 primary
- 旧 primary 上的"已 ack w:1 但未达 commit_point"的写,failover 后被 revert
- 客户端用 `w:"majority"` 时不会发生回滚(因为已经 commit_point)

### 7. 因果一致性会话(Causal Consistency)

MongoDB 驱动支持会话(session):
- 客户端每个 operation 拿到 server 的 `operationTime`,作为下次读的下限
- 驱动自动设置 `afterClusterTime` readConcern
- 配合 `w:"majority"` 写 + `readConcern:"majority"` 读 ⇒ 实现 read-your-own-writes 不需要应用层补偿

## 核心 API(MongoDB 协议 + 驱动封装)

```js
// 写
db.coll.insertOne(
    { _id: 1, name: "alice" },
    { writeConcern: { w: "majority", j: true, wtimeout: 5000 } }
)

// 读
db.coll.find({ _id: 1 })
    .readConcern("majority")
    .readPreference("primary")
    .toArray()

// 因果一致 session
const session = client.startSession({ causalConsistency: true });
// 所有 CRUD 都自动包含 afterClusterTime
```

## 对比 / 选型

| 维度 | MongoDB 副本集 | Cassandra | DynamoDB | etcd/Raft |
| --- | --- | --- | --- | --- |
| 一致性模型 | tunable(wc/rc per call) | tunable(CL per call) | 默认 strong + 事务 API | 强一致 Raft |
| 复制算法 | 异步 oplog pull | gossip + hinted handoff | 中心化同步 | Raft 日志复制 |
| 回滚风险 | `w:1` 可回滚 | N/A(无 primary 概念) | N/A | 不可能 |
| 多 DC | 不透明(需自管) | 一等公民(NetworkTopologyStrategy) | Global Tables | 需 operator 自管 |
| 事务 | 多文档 ACID(4.0+) | LWT(Paxos)| DynamoDB Transactions | 单 key 事务 |

## 环境

- **C**: gcc / clang (`-O2`); 仅 stdlib
- **Python**: 3.10+; 仅标准库
- **Go**: 1.20+

## 运行

### C

```bash
gcc -O2 -Wall -Wextra -pedantic c/mongo_wc.c -o c/mongo_wc
./c/mongo_wc
# 输出 5 段: writeConcern 解析 / 默认公式 / j 选项 / 5 级 readConcern / oplog 路径
```

### Python

```bash
python3 python/mongo_wc.py
# 5 个 demo:
#   1) 默认 WC 公式(含 P-S-A trap)
#   2) 不同 wc 写入(w:1 / w:'majority' / w:3 超时)
#   3) readConcern 5 级语义
#   4) 故障切换 + 回滚
#   5) 因果一致性 session
```

### Go

```bash
cd go
go run mongo_wc.go
# C 版的精简版:决策表 + oplog 简介 + 推荐组合
```

## 关键代码片段

### writeConcern 决策(Python)

```python
def write(coll, doc, wc):
    if "w" not in wc: wc["w"] = "majority"   # 5.0+ 隐式默认
    # primary 本地写
    self.primary.store[doc["_id"]] = doc
    # OpObserver 钩子 → oplog
    entry = self.opobserver.record("i", coll, doc, ...)
    # 多数派计算
    voting = [m for m in self.members if m.priority > 0 and not m.hidden]
    majority = len(voting) // 2 + 1
    need = majority if wc["w"] == "majority" else wc["w"]
    # 让 secondary 拉 oplog 并应用
    for m in self.members:
        if m is not self.primary and m.role != "ARBITER":
            m.apply_oplog_entry(entry)
    # 计算 commit point
    commit = min(m.last_applied_ts for m in self.members if m.role != "ARBITER")
    acked = sum(1 for m in self.members if m.last_applied_ts >= entry.ts and m.role != "ARBITER")
    return acked >= need, f"acks={acked}/{need}, commit={commit}", entry.ts
```

### readConcern=majority 等待 commit point(Python)

```python
def read(coll, query, rc, from_secondary=False):
    target = self.primary if not from_secondary else next(SECONDARY)
    if rc == "majority":
        commit = min(m.last_applied_ts for m in self.members if m.role != "ARBITER")
        if target.last_applied_ts < commit:
            return None, f"等待 oplog 推进 (ts={target.last_applied_ts} < commit {commit})"
    return target.store.get(query["_id"]), f"ok from {target.name}"
```

### 默认 WC 公式(C)

```c
// P-S-A 隐式陷阱:voting=3, arbiters=1, non-arbiters=2, majority=2
// 2 <= 2 ⇒ 退回 w=1 ! 这就是 P-S-A 不可与 {w:majority} 同时使用
```

### Causal Session(概念)

```python
# 驱动侧:
session = client.startSession(causalConsistency=True)
# 每次读 / 写获取 serverTime;写入 next read 的 afterClusterTime
# → 自动实现 read-your-own-writes 不需要应用层补
```

## 性能与边界

| 项 | 量级 / 行为 |
| --- | --- |
| 默认 oplog 大小 | 磁盘 5%(min 990MB) |
| 写延迟(primary ack) | 数 ms 到 10ms(单 DC) |
| `w:"majority"` 延迟 | ≈ primary + 1 个 secondary 心跳周期 |
| readConcern=majority 延迟 | 节点必须达 commit point,通常 ≤ 100ms 同步 |
| 选举时间 | 默认 10s(`electionTimeoutMillis`);通常 1-3s 出新主 |
| 回滚窗口 | `w:1` 才可能;多数派写几乎不可回滚 |
| 事务大小上限 | WiredTiger 默认 16MB `WiredTigerMaximumTxnDurationMs`;长事务 = undo 大 |

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| P-S-A 部署仍发生回滚 | 隐式 `w:1` | 显式 `setDefaultRWConcern({w:'majority'})` |
| secondary 拉 oplog 卡死 | 副本跟不上时 oplog 旧条目被覆盖 | 监控 `db.getReplicationInfo()` 拿 oplog window;扩容 secondary |
| readConcern=majority 卡顿 | 节点落后 | 监控 repl lag;secondary 拉起来再 serve 读 |
| 隐式回滚看得见的"数据消失" | 客户端用 `w:1` 且 failover | 用 `w:"majority"` 全程保平安 |
| 大量事务导致 undo pile | WiredTiger 长事务保留 undo | 监控 `txnTotalCommitDuration` / 拆小事务 |
| 跨分片 transaction 慢 | 需要 coordinator 跨分片 2PC | 默认事务有时限(60s);用 causal session 替代多读 |
| oplog 入口冲突 | 隐式 id 冲突 | `_id` 唯一;upsert 走 `$setOnInsert` 而非 `$set` |
| linearizable 在 secondary 上 | 协议不允许 | 显式 `readPreference("primary")` 才生效 |

## 推荐配置

```js
// mongod.conf
replication:
  replSetName: "rs0"
  oplogSizeMB: 10240   // 10GB(覆盖≥24h 复制窗口)

// 应用层
db.coll.insertOne(doc, {
    writeConcern: { w: "majority", j: true, wtimeout: 5000 }
});
db.coll.find(filter).readConcern("majority").toArray();
```

## 参考资料(实际阅读过的权威链接)

- [MongoDB manual: Write Concern](https://www.mongodb.com/docs/manual/reference/write-concern/) — `w` / `j` / `wtimeout` 三轴默认值与隐式规则
- [MongoDB manual: Read Concern](https://www.mongodb.com/docs/manual/reference/read-concern/) — 5 级 readConcern 语义 + afterClusterTime 因果一致性
- [MongoDB source: src/mongo/db/repl/README.md](https://github.com/mongodb/mongo/blob/f1ec9a14cf02a08047a9741b8a60633e6677cdbd/src/mongo/db/repl/README.md) — Replication Internals(OpObserver + commit point + stepdown)
- [MongoDB 5.0 Compatibility: Implicit Default Write Concern](https://www.mongodb.com/docs/release-notes/5.0-compatibility/) — P-S-A trap 公式
- [tech-interview.dev: MongoDB replica sets](https://tech-interview.dev/tech/mongodb/replica-sets) — oplog pull + election trigger 概要
