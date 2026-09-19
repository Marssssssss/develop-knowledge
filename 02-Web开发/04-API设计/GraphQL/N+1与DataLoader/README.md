# GraphQL N+1 与 DataLoader

## 简介

GraphQL 的接口形状天然放大 N+1：`{ posts { author { name } } }` 看起来是一个查询，
朴素实现却会发 **1 次取 posts + N 次取 author**。列表越长越糟，而且**与前端写了多少字段无关** ——
问题出在 resolver 的组织方式，不在 query 文本。

DataLoader 的解法是把"每个 resolver 各自取数"改成"**同一个执行帧内的取数请求合并成一次批量调用**"。
它不是 GraphQL 专属的（源自 Facebook 2010 年的 "Loader"，也是 Haskell 版 Haxl 的动机），
但它是 graphql-js 生态里的事实标准。

本 demo 用 **JavaScript（node 实跑 23 断言）+ Python（实跑 22 断言）** 两侧复刻官方 README 里
可判定的语义，并把 N+1 做成可数出来的往返次数。

## 原理详解

### 1. N+1 的形状

```text
朴素 resolver：      1 次 fetch posts  +  N 次 fetch author     → 1 + N
DataLoader：         1 次 fetch posts  +  1 次 fetch authors    → 2
三层（author→company）：朴素 1 + N + N = 21    DataLoader 1 + 1 + 1 = 3
```

关键在于：**合并窗口 = 一个执行帧（one tick of the event loop）**。同一帧里发出的 `load()` 会被攒起来，
帧结束时一次性交给 `batchLoadFn`。所以同一层的 N 个 resolver 自然落进同一帧，也就自然合并了。

### 2. batchLoadFn 的两条硬约束

官方 README 写得很死：

1. **返回数组的长度必须等于 keys 的长度**；
2. **第 i 个值必须对应第 i 个 key**。

这不是风格建议。后端 `WHERE id IN (...)` 的返回顺序**不保证**与请求顺序一致，缺记录时也不会补洞 ——
重排和补 `null`（或 `Error` 实例）是 batch 函数的责任：

```text
请求 keys  = [2, 9, 6, 1]
后端返回   = {9: Chicago}, {1: New York}, {2: San Francisco}      （乱序，且缺 6）
必须返回   = [San Francisco, Chicago, null, New York]              （等长且按索引对齐）
```

本 demo 的 `FakeDB.fetch_users()` 故意按 `sorted(USERS)` 顺序返回，就是为了逼出这两条约束。

### 3. 缓存是 per-request 的 memoization

DataLoader 的缓存**不替代 Redis/Memcached**，它的唯一目的是"同一个请求里不重复加载同一份数据"。
两条实践规则：

- **每个请求新建 DataLoader 实例**（尤其当不同用户看到不同数据时），否则缓存会跨用户串味；
- 长生命周期的实例要用 LRU 之类的 `cacheMap` 兜住内存。

一个容易被忽略的实现细节：**官方在 `load()` 时就缓存了 promise**，所以**同一帧内的重复 key 也会被去重**，
不只是跨帧。本 demo 的 JS 与 Python 两侧都复现了这一点（`loadMany([1,1,2,2,3])` → keys 为 `[1,2,3]`）。

### 4. 缓存命中但仍然"等当前批次"

官方专门解释了这个看似无用的设计：命中缓存的 key 不会进 keys，但它的 Promise **仍要等当前批次完成**
才 resolve。目的是让下游的依赖 load 也落在同一帧，从而继续合并。反例在 README 里给了：

```text
prime(1) 后 getBestFriend(1) 与 getBestFriend(2) 同帧发起
  → 两人同时 resolve → 两人的 bestFriendID 同帧 load → 共 2 次请求
  若缓存值立即 resolve → bestFriend load 分两帧 → 变成 3 次请求
```

### 5. 错误语义的分岔

| 情况 | 是否缓存 | 说明 |
| --- | --- | --- |
| `batchLoadFn` throw / reject | **不缓存** | 整批失败，缓存条目被删掉，下次重试会再打后端 |
| 某个值返回 `Error` **实例** | **缓存** | 官方明说"that Error will be cached to avoid frequently loading the same Error" |
| 返回数组长度不符 | 整批拒绝 | 违反第 1 条约束 |

需要"某些错误别缓存"时，在 catch 里手动 `clear(key)`。

### 6. 选项的默认与等价

| 选项 | 默认 | 备注 |
| --- | --- | --- |
| `batch` | `true` | `false` **等价于** `maxBatchSize: 1` |
| `maxBatchSize` | `Infinity` | 超过就切分成多个批次 |
| `cache` | `true` | `false` **等价于** `cacheMap: null`，此时 keys **会含重复** |
| `cacheKeyFn` | `key => key` | 对象作 key 时用得上 |
| `cacheMap` | `new Map()` | 只要有 `get/set/delete/clear` 就能换（如 LRU） |
| `batchScheduleFn` | 微任务 | 可换成 100ms 窗口（代价：+100ms 延迟）或手动 dispatch |

### 7. 什么时候清缓存

同一请求内先 mutation 再查询时，缓存里的旧值会挡住新值 —— 官方给的写法是 mutation 之后立刻 `clear(key)`：

```js
await sqlRun('UPDATE users WHERE id=4 SET username="zuck"');
userLoader.clear(4);
```

## 对比

| 方案 | 往返次数 | 引入复杂度 | 适用 |
| --- | --- | --- | --- |
| 朴素 resolver | 1 + N | 无 | N 恒很小、或字段很少被同时请求 |
| DataLoader 批处理 | 1 + 1 | 一个 loader 层 | 绝大多数 GraphQL 服务 |
| JOIN / 单条大 SQL | 1 | SQL 变复杂、过取 | 关系固定、查询形状稳定 |
| 查询计划（如 Haxl） | 自动最优 | 高 | Haskell / 需要跨源并发去重 |

DataLoader 的位置很微妙：它**不改查询结果**，只改**查询次数**。本 demo 断言了这一点 ——
`naive_posts_authors` 与 `loader_posts_authors` 返回完全相同的结果集，只有往返次数不同。

## 环境

- Node.js ≥ 14（需要 `queueMicrotask`；本机用 v22 实跑）
- Python 3.8+（仅标准库）

## 运行方式

```bash
cd javascript && node check.js       # 23 项断言，全绿
cd python     && python check.py     # 22 项断言，全绿
```

## 关键代码

```js
// 官方在 load() 时就缓存 promise —— 所以同帧内的重复 key 也去重
const promise = new Promise((resolve, reject) => {
  this._queue.push({ key, cacheKey, resolve, reject });
  this._maybeSchedule();
});
if (this._cacheMap) this._cacheMap.set(cacheKey, promise);
```

```python
def dispatch(self) -> None:
    while self.queue:
        size = len(self.queue) if self.max_batch_size is None \
            else max(1, self.max_batch_size)
        batch, self.queue = self.queue[:size], self.queue[size:]
        keys = [p.key for p in batch]
        values = self._fn(keys)
        if not isinstance(values, list) or len(values) != len(keys):
            raise ValueError("batchLoadFn must return an Array of the same length ...")
```

## 性能边界

- 往返压缩比 = `(1 + N) / 2`（两层、无分片时），随 N 线性放大收益；N=1 时 DataLoader 反而多一层开销。
- `maxBatchSize` 不是越大越好：超过数据库参数上限（`IN (...)` 的绑定变量数）会退化成多次查询，
  甚至直接报错。
- 缓存是**无限增长的 Map**，长生命周期实例是内存泄漏源 —— 用 `cacheMap` 换成 LRU。
- 批处理把 N 次小查询换成 1 次大查询，**单次延迟会上升**（等一帧 + 大查询结果集），
  换的是总吞吐而不是单次 RTT。
- `batchScheduleFn` 换成时间窗口（如 100ms）会**明确增加 100ms 延迟**，只在请求天然分散时划算。

## 注意事项与常见坑

1. **`load()` 之后必须 `await`/dispatch**，否则批次不会发出（手动调度模式下尤其容易漏）。
2. **别把 DataLoader 当共享缓存**：跨请求复用会让 A 用户看到 B 用户的数据。
3. **`cache: false` 时 keys 会重复**，batch 函数要为每个重复项都给值 —— 这不是 bug，是官方行为。
4. **返回 `Error` 实例 ≠ 抛错**：前者被缓存，后者不缓存。混用会导致"错误被永久缓存"或"错误反复重打"。
5. **mutation 后必须 `clear()`**，否则读到 mutation 之前的旧值。
6. **重排是 batch 函数的责任**，`WHERE id IN (...)` 的结果顺序不保证。
7. **N+1 也可能出现在 REST 聚合层**，不只在 GraphQL —— 只要"循环里逐条查关联"就是同一个病。
8. **DataLoader 不解决 over-fetching**（取了字段却不用），那是 query 形状的问题。
9. **同一帧的判定依赖事件循环**：在 `await` 之后发起的 load 属于新的一帧，
   所以"把 load 提前到循环外发起"是有效的优化（本 demo 的三层写法即如此）。

## 参考资料

- graphql/dataloader 官方 README（"Batching" / "Batch Function" / "Batch Scheduling" /
  "Caching" / "Caching Per-Request" / "Caching and Batching" / "Caching Errors" /
  "Disabling Cache" / "Custom Cache" / API 选项表）
  — <https://raw.githubusercontent.com/graphql/dataloader/main/README.md>（全文实读）
- graphql/dataloader 源码 `enqueuePostPromiseJob`（默认调度器的实现依据）
  — <https://github.com/graphql/dataloader>
- Facebook Haxl（同一动机的 Haskell 实现，README 中作为对照引用）
  — <https://github.com/facebook/Haxl>
