# Dynamo 风格存储 — 4 大核心机制最小实现

> 论文:*Dynamo: Amazon's Highly Available Key-value Store* (SOSP 2007, DeCandia et al.) — [原 PDF (Allan 镜像)](https://www.cs.cmu.edu/afs/cs/academic/class/15712-f08/www/lectures/DeCandia07lecture.pdf) / [Cornell CS 6410 课件](https://www.cs.cornell.edu/courses/cs6410/2017fa/slides/22-p2p-storage.pdf)
> 2017 SIGOPS Hall of Fame Award。Cassandra / Riak / ScyllaDB / DynamoDB 直接或间接来自 Dynamo 的设计。

## 简介

Dynamo 是 Amazon 为"购物车"等"始终可写(key 可丢失但绝不可拒绝写)"场景设计的**去中心化 KV 存储**。它牺牲强一致性换取高可用与分区容忍,通过一组组合技术做到这一点。

关键概念(每条 1 句):
- **一致性哈希(consistent hashing)**:节点和 key 都散到同一 ring,key 顺时针找节点
- **虚节点(virtual nodes)**:每个物理节点占 ring 上多个位置,扩容/缩容时分摊更均匀
- **偏好列表(preference list)**:key 的 N 个目标节点(按 ring 顺时针取)
- **sloppy quorum**:R/W < N 允许网络分区时仍能达成"伪 quorun",用 hint 补偿
- **向量时钟(vector clock)**:跟踪一个 key 的多个并发版本,读时应用层协调
- **hinted handoff**:目标节点临时 down 时,写发到下一个健康节点并加 hint 标记
- **反熵 / Merkle 树**:后台异步把副本修齐,Merkle 树按子树哈希精确锁定差异范围

历史背景:Dynamo 论文发布于 2007,SOSP 2017 入 Hall of Fame,直接孵化了 Cassandra(2008 开源),启发了 Riak、ScyllaDB,间接影响了 DynamoDB(2007 公告)。

## 原理详解

### 1. Partitioning — 一致性哈希 + 虚节点

Dynamo 把哈希函数输出当作一个**固定大小的环形空间**(论文用 MD5,我们 demo 用 SHA-1 前 4 字节):

```
           0°
           /\
   n_B   /    \   n_A
        /  •  \
   n_C | pos=key |   n_D
        \      /
         \____/
   n_F        n_E
```

- 每个物理节点在 ring 上被赋予**多个虚拟位置(vnode)**
- key → 顺时针找第一个 vnode 所在物理节点负责
- 节点加入/离开时,只影响它的 vnode 区间,其他节点不受波及 → **增量扩容**
- vnode 数量按物理节点容量异构分配(权重)

```
vnode_count(n_A) = 16   # 16 vnode 不必均匀;实际系统按机器性能分配
```

### 2. Replication — 偏好列表 + sloppy quorum

```
            顺时针 → N 个不同物理节点
            ┌──────────────────────────┐
            │ key=user:42:cart          │
            │ hash = pos(key)           │
            │ N=3 → [n_X, n_Y, n_Z]    │  ← preference_list
            └──────────────────────────┘
```

- **R / W / N**:每次 get 取 R 份,put 写 W 份,副本总数 N
- **R + W > N** 才形成 quorum;论文用 (N=3, R=2, W=2) 在 99.9% SLA 下压低延迟
- **Sloppy quorum**:若偏好列表中前 N 个里有临时 down,写被路由到 ring 上下一个健康节点,并打一个**hint 标记(本该发往谁)**

### 3. Data Versioning — 向量时钟

Dynamo 用**向量时钟**(vector clock)取代全局时间戳,因为写可以乱序,没有统一 wall clock。每个 (key, version) 关联一个 `{node: counter}` 字典:

| 操作 | 时钟变化 |
| --- | --- |
| 客户端首次 put(key) | `{}` → `{A:1}` |
| A 再 put(key) | `{A:1}` → `{A:2}` |
| B 独立 put(key) | `{A:2}` 下读到 → A 先把 A:2 推到 B → B 自增 `{A:2, B:1}` |
| A 与 B 独立写 | `{A:1}` 与 `{B:1}` **并列(sibling)**,两版都保留 |

**Dominates** 关系(用于读 reconcile 修剪):
> A.vc dominates B.vc ⇔ A.cnt[i] ≥ B.cnt[i] for all i, 且至少一个严格 >

- dominates ⇒ 旧版可丢
- sibling(并列)⇒ 两版都保留,由应用层(购物车合并 product 列表 / last-write-wins for session)协调

### 4. Hinted Handoff + Anti-Entropy / Merkle Tree

**临时失败**的处理(hinted handoff):

```
put(key) → preferred = [n_X, n_Y, n_Z]
                  ↓ n_Z down
1) 写 n_X, n_Y (前两个健康)
2) 把本应给 n_Z 的副本写到 ring 上 n_Z 的下一健康节点 n_W
3) n_W.store[(key, "hint-for-n_Z")]
4) n_Z 恢复 → n_W 周期扫描 hint 队列 → 把 (key, value, vc) 推给 n_Z
5) n_W 删除该 hint
```

窗口通常 ≤ 数小时(超长时段仍残留的分裂,用下面的 Merkle tree 修)。

**永久失败 / 长期不一致**(anti-entropy using Merkle tree):

```
         H_root
        /      \
   H(L)        H(R)
   / \          / \
  h  h        h   h     ← 每片负责 1 个 key range;
                          叶子 = sha256(keys),内部 = sha256(children)
```

两节点比较 Merkle tree 根哈希:
- 相同 ⇒ 区间无差异
- 不同 ⇒ 逐层下钻到范围最小差异,只流式同步该 range 的 keys

## 核心 API(简化)

论文的客户端接口只有两个:

```
put(key, context, object)  → object 通常是 (value, vector_clock)
get(key)                   → 返回多版本 object[], 客户端 reconcile
```

附 Ring 上的内部:
- `preference_list(key, N, alive)` → `[node for first N distinct positions]`
- `vector_clock.increment(node)` → 返回新 VC
- `vector_clock.dominates(other)` → True ⇒ 旧版可丢

## 对比 / 选型

| 维度 | Dynamo | Cassandra | Riak | DynamoDB(托管) |
| --- | --- | --- | --- | --- |
| 同步策略 | 异步写 + 读修 | 一致性级别(quorum) | 异步 + CRDT 选项 | 每个 API 可选 |
| 版本机制 | Vector Clock | Last-write-wins | Vector Clock / CRDT | 全托管,无版本 |
| 协调 | 无中心 | 无中心 | 无中心 | 中心(由 AWS) |
| 编程界面 | 自定义 SDK | CQL | HTTP | AWS SDK |
| 适用 | Amazon 内置购物车 | 大写多读 | 多 DC 灵活 | 通用托管 |

## 环境

- **C**: gcc / clang (`-O2`),POSIX
- **Python**: 3.10+(仅标准库)
- **Go**: 1.20+,仅用 `hash/fnv` + 标准库

## 运行

### C

```bash
gcc -O2 -Wall -Wextra -pedantic c/dynamo.c -o c/dynamo
./c/dynamo          # 直接跑默认 demo(打印 preference list + hinted handoff)
```

### Python

```bash
python3 python/dynamo.py
# 4 个 demo 涵盖:
#   1) preference list 路由
#   2) sloppy quorum 计算
#   3) vector clock dominates / sibling
#   4) put/get 完整数据流
```

### Go

```bash
cd go
go run dynamo.go
# 命令行驱动: 空回车跑默认 demo,或手动 input put/get/pref/hint/replay/quit
```

## 关键代码片段

### 一致性哈希 + preference list(Python)

```python
def preference_list(self, key, N, alive):
    pos = hash32(key)
    idx = bisect.bisect_right(self._sorted_positions, pos)
    if idx == len(self._sorted_positions):
        idx = 0  # 绕回 ring 起点
    seen, out = set(), []
    while len(out) < N and len(seen) < len(self._sorted_positions):
        node = self._pos_to_node[self._sorted_positions[idx]]
        if node not in seen:
            out.append(node); seen.add(node)
        idx = (idx + 1) % len(self._sorted_positions)
    # sloppy:不够 N 时,从后续 ring 位置拿"hint 接收者"
    return out[:N]
```

### 向量时钟 dominates / sibling 判定(Python)

```python
def dominates(self, other):
    keys = set(self.entries) | set(other.entries)
    if any(self.entries.get(n, 0) < other.entries.get(n, 0) for n in keys):
        return False  # 有 n 上 self < other ⇒ 不 dominates
    return any(self.entries.get(n, 0) > other.entries.get(n, 0) for n in keys)
# sibling: 互不 dominates ⇒ 都保留
```

### Hinted handoff 投递(C)

```c
static void hint_push(const char *owner, const char *target,
                      const char *key, const char *value) {
    // 真实节点会写一条 (intended_for=owner, key=key) 到 ring 上 target 节点的 hint 队列
    // 演示把 hint 进队,等 hint_replay(owner) 模拟上线后投递
    ...
}
static void hint_replay(const char *recovered_node) {
    // 扫描 hint 队列,找到 owner == recovered_node 的,投递并删除
    ...
}
```

### FNV-1a 偏好列表(Go,sort.Search 二分起点)

```go
func preferenceList(key string) []physNode {
    h := hash32(key)
    idx := sort.Search(len(ring), func(i int) bool { return ring[i].pos > h })
    if idx == len(ring) { idx = 0 }                // 绕回
    seen, out := map[int]bool{}, []physNode{}
    for step := 0; step < ringSize && len(out) < replicaN; step++ {
        v := ring[idx]; if !seen[v.nodeID] {
            out = append(out, nodes[v.nodeID]); seen[v.nodeID] = true
        }
        idx = (idx + 1) % len(ring)               // 顺时针 +1
    }
    return out
}
```

## 性能与边界

| 项 | 量级 / 行为 |
| --- | --- |
| Ring 节点数(单 cluster) | 几千到几万 vnode 量级;论文实测数百节点 |
| 单 put 延迟 | 99.9% 分位 ~200 ms,均值 ~20 ms |
| Vclock 长度 | O(Nodes who wrote);默认删除 dominated 旧版,实际收敛 |
| N / R / W | 默认 3 / 2 / 2;R+W ≤ N 时牺牲一致性;R+W > N 形成强 quorum |
| Hint 窗口 | 实测 (Cassandra) 默认 3 小时(max_hint_window_in_ms = 10800000) |
| Merkle 树高度 | log2(range_num);Cassandra 默认 16 个 key range/节点 ≈ O(log 2¹⁶) |

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 同一 key 在不同节点读到不同值 | Vector clock 未解决并列 sibling | 应用层必须实现"读 reconcile"(购物车合并 product / LWW for session) |
| Hint 队列无限增长 | 节点长期 down | 限制 max_hint_window(如 3h),过期丢盘,改用 anti-entropy repair 补 |
| Ring 上节点加入/离开后负载不均 | vnode 均匀,但机器性能异构 | 按 CPU/RAM 比例分配 vnode_count |
| Vector clock 增长失控 | 长时间离线后合并导致 entries 累加 | Dynamo 自己 truncate vector clock,丢次要历史 |
| Merkle 树同步风暴 | 全节点同时 repair | Cassandra 用 -pr(parallel) + -par(partitioner),分阶段跑 |
| Slack quorum 在半数以上节点 down 时不可用 | "sloppy" 不代表任意 | 若必须强一致:用 quorum(N/2+1) + read-repair + anti-entropy 兜底 |
| 看 Cassandra 而非"Dynamo 论文"做容量规划 | Cassandra 一致性级别语义有变化 | 读 Cassandra 4.x docs;论文 = 设计意图,实际生产默认 LOCAL_QUORUM |

## 参考资料(实际阅读过的权威来源)

- DeCandia et al., *Dynamo: Amazon's Highly Available Key-value Store*, SOSP 2007 — [Allan CMU 镜像 PDF](https://www.cs.cmu.edu/afs/cs/academic/class/15712-f08/www/lectures/DeCandia07lecture.pdf) — 论文原文;Part 3 (Related Work) 给了后续 Riak/Cassandra 的对比
- [Cornell CS 6410 课程 22-p2p-storage.pdf](https://www.cs.cornell.edu/courses/cs6410/2017fa/slides/22-p2p-storage.pdf) — 教学提炼,Sloppy Quorum vs strict quorum 的例子(购物车 add X / add Y 后分歧)
- [SIGOPS Hall of Fame 2017](https://www.sigops.org/2017/hof2017) — 入选论文的官方颁奖词,作为权威地位背书
- Cassandra 一致性级别(为求严谨) — 详见同级目录 [`../Cassandra一致性/README.md`](../Cassandra一致性/README.md)
