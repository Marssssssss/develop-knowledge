# 缓存一致性模式（Cache-Aside / Write-Through / Lease）

## 一、简介

应用层缓存最难的不是「放进去、取出来」，而是**缓存与数据库双写时的不一致窗口**。本 demo 做两件事：

1. 用**交错执行枚举**量化四种写策略的不一致概率——不是讲「可能会不一致」，而是数出「在全部可能的交错里，有几种会留下脏缓存」。
2. 复现 Facebook 在 NSDI'13 论文里提出的 **lease** 机制，看它如何同时解决 stale set 与 thundering herd。

## 二、原理详解

### 2.1 四种写策略

| 策略 | 步骤 | 典型用途 |
| --- | --- | --- |
| Cache-Aside（先删缓存再更新库） | `DEL cache` → `UPDATE db` | 常见但**不推荐**的写法 |
| Cache-Aside（先更新库再删缓存） | `UPDATE db` → `DEL cache` | Azure 文档推荐 |
| 延迟双删 | `UPDATE db` → `DEL` → `sleep` → `DEL` | 上面的加固版 |
| Write-Through | 库与缓存在同一个写操作内更新 | 缓存层原生支持时用 |

### 2.2 用枚举量化不一致窗口

把一个「缓存未命中的读」建模为两步：`R_DBGET`（读库）→ `R_CACHESET`（回填），
把写建模为若干步，然后枚举**所有保持各自内部顺序的交错**（写者 m 步、读者 2 步 → `C(m+2, m)` 种），
逐个模拟后判定「缓存里要么没有该 key，要么值等于库值」。

| 写策略 | 交错总数 | 产生不一致 | 比例 |
| --- | --- | --- | --- |
| 先删缓存再更新库 | 6 | **4** | 66.7% |
| 先更新库再删缓存 | 6 | **1** | 16.7% |
| 延迟双删 | 10 | **1** | 10.0% |
| Write-Through（单原子步） | 3 | **1** | 33.3% |

第一条与第二条之比是 **4 倍**，这正是 Azure 文档那句话的定量版本：

> Update the data store *before* removing the item from the cache. If you remove the cached item first, there's a small window of time when a client might fetch the item before the data store is updated.

那唯一一条坏交错长这样（`先删缓存` 版）：

```
W_DEL → R_DBGET(读到 v1) → R_CACHESET(回填 v1) → W_DBSET(库变成 v2)
```

结果：库是 `v2`、缓存是 `v1`，**且没有任何后续操作能修复它**——只能等 TTL 或下一次写。这就是「先删缓存」危险的原因：**不一致是持久的，不是瞬时的**。

### 2.3 没有一种组合是零窗口

一个反直觉的结论：**Write-Through 也不是 0**。即使写是原子的（库和缓存同时更新），只要「读未命中 → 读库 → 回填」这三步不是原子的，就存在 `R_DBGET → W_ATOMIC_BOTH → R_CACHESET` 这条交错，把读到的旧值盖回去。

也就是说：

- **写路径原子化无法消除不一致**，因为不一致窗口的根源在**读路径**的「读库」与「回填」之间；
- 要真正闭合窗口，必须让「读回填」也受保护——这正是 lease 要解决的问题。

延迟双删降到 10% 也不是 0，而且**它的收益依赖「第二次删除发生在读回填之后」这一时序假设**；本 demo 把第二次删除建模为紧接着的一个步骤，这是偏乐观的建模，真实收益取决于 sleep 时长是否大于读回填的耗时。

### 2.4 Lease：64-bit token 绑定 key

论文 §3.2.1 的定义：

> a memcached instance gives a lease to a client to set data back into the cache when that client experiences a cache miss. The lease is a **64-bit token bound to the specific key** the client originally requested.

机制要点：

1. 缓存未命中时，服务端发一个 token，而不是让客户端随意写回。
2. 回写时必须带上 token；服务端校验 token 是否仍然有效。
3. **收到该 key 的 delete 请求会使在途 token 全部作废**——这就是防止 stale set 的关键。论文自己说它「similar to how load-link/store-conditional operates」。

对比本 demo 的实测：带作废 token 的回写被**拒绝**；不带 token 的普通回写被**接受**，缓存立刻被旧值污染。

### 2.5 Lease 顺手解决 Thundering Herd

同一个机制稍作改造就解决了惊群：**服务端限制发 token 的速率**，默认**每个 key 每 10 秒只发一个**。10 秒内到达的请求不拿 token，而是收到一个「稍等一会儿」的通知。拿到 token 的那个客户端通常几毫秒内就写回了，等它写完其他客户端重试时数据已经在缓存里。

论文的实测数据（对一组特别容易惊群的 key，采集一周）：

- 无 lease：所有缓存未命中的峰值数据库查询率 **17K/s**
- 有 lease：峰值 **1.3K/s**
- 降幅 **13.08 倍**

| 场景 | 打到数据库的次数 |
| --- | --- |
| 100 个客户端并发读冷 key（无 lease） | **100** |
| 100 个客户端并发读冷 key（有 lease） | **1** |
| 论文真实环境峰值 QPS | 17K/s → 1.3K/s（13.08×） |

理想化的 100 倍与实测的 13.08 倍之间的差距，来自「并非所有请求都恰好同时到达」——论文数字是在真实流量分布下测的，比合成的 100 倍更可信。

### 2.6 Stale value：能容忍旧数据的应用不用等

论文还有一个常被忽略的细节：key 被删除后，它的值会被转存到一个「最近删除项」结构里存活一小段时间。`get` 可以返回 lease token，**也可以返回标记为 stale 的数据**。能接受稍旧数据的应用（论文观察到「缓存值往往是数据库的单调递增快照」，所以大多数应用无需改动就能用 stale 值）完全不必等待。

本 demo 中 stale 分支**不消耗 token**，因此不受 10 秒限流约束——这和「限流是为了减少回源」的目标一致：返回 stale 并不回源。

## 三、对比

| 手段 | 解决什么 | 代价 |
| --- | --- | --- |
| 先更新库再删缓存 | 把不一致窗口从 4/6 压到 1/6 | 无（纯粹是顺序问题） |
| 延迟双删 | 再降到 1/10 | 多一次删除 + 一次 sleep；依赖时序假设 |
| Write-Through | 写路径原子 | 写延迟变高；**读路径仍非原子** |
| Lease | stale set + 惊群 | 每次未命中多一轮 token 往返 |
| Stale value | 惊群下的等待时间 | 读到稍旧的数据 |

## 四、环境

- Python 3.13（纯标准库）
- C（C11，`gcc -std=c11 -O2 cache_consistency.c -o cc && ./cc`）
- Go 1.22+（仅 `fmt`）

## 五、运行方式

```bash
cd 07-数据存储/03-缓存/缓存一致性模式
python cache_consistency_selftest.py   # 33 条断言
gcc -std=c11 -O2 cache_consistency.c -o cc && ./cc
go run cache_consistency.go
```

## 六、关键代码

交错枚举用位掩码生成——写者 m 步、读者 2 步，从 `m+2` 个位置里选 m 个给写者，天然枚举出全部 `C(m+2, m)` 种交错且不重不漏：

```c
for (int mask = 0; mask < (1 << L); mask++) {
    int bits = 0;
    for (int i = 0; i < L; i++) if ((mask >> i) & 1) bits++;
    if (bits != m) continue;              /* 必须恰好 m 个位置给写者 */
    ...
}
```

lease 的作废判据只有一行，但它是整个机制的核心：

```python
def set_with_lease(self, key, value, lease, now):
    if lease.key != key:
        return False
    return self._accept(key, value, lease.epoch, now, require=True)
    # _accept 里：if require and epoch != self.epochs.get(key, 0): return False
```

## 七、性能边界

- 交错枚举是 `O(C(m+n, m))`，写者步骤数一多就爆炸——**只适合给 2~3 步的写策略做穷举验证**，真实系统要用 TLA+ / 线性一致性检查器
- lease 的 10 秒限流是**每 key** 的，不是全局的；key 基数极大时状态开销随之增长
- 限流窗口设太长会让「真的缓存未命中」也被迫等待；论文选 10 秒是因为「持有 lease 的客户端通常几毫秒内就写回了」
- 本 demo 的 lease 是单 key 简化模型；真实 memcached 还需要处理 CAS、slab 淘汰导致的隐式失效

## 八、注意事项与常见坑

1. **「先删缓存再更新库」的不一致是持久的**，不是「短暂的读到旧值」——脏数据会一直待到 TTL。
2. **Write-Through 不等于零窗口**：读路径的「读库 → 回填」之间仍有缝。
3. **延迟双删不是银弹**，它的正确性依赖 sleep 时长大于读回填耗时；sleep 太短等于没做，太长则多一次无用删除。
4. **lease token 绑定的是 key**，而不是连接或客户端；换 key 用同一个 token 必须拒绝。
5. **delete 作废 token 是防 stale set 的唯一手段**，没有它 lease 就只剩限流功能。
6. **stale 分支不该消耗 token**，否则「能容忍旧数据」这个优化反而会加剧回源。
7. **论文的 13.08 倍与合成的 100 倍口径不同**，写文档/汇报时不要混用这两个数字。
8. **Cache-Aside 本身不保证一致性**——Azure 文档明说外部进程随时可以改库，而缓存不会知道。TTL 是最后一道防线。

## 九、参考资料

- Microsoft Learn, Azure Architecture Center — *Cache-Aside pattern*（§Solution 三步、§Problems and considerations 的 Consistency/Staleness after writes、以及代码示例里「Update the data store before removing the item from the cache」的 Note） — https://learn.microsoft.com/en-us/azure/architecture/patterns/cache-aside
- Nishtala et al., *Scaling Memcache at Facebook*, NSDI'13（§3.2.1 Leases：64-bit token、stale set、thundering herd、10 秒限流、17K/s → 1.3K/s、stale value 的「recently deleted items」结构；§3.2.2 Memcache Pools） — https://www.usenix.org/system/files/conference/nsdi13/nsdi13-final170_update.pdf
