# Controller Reconciler Loop 模式

## 简介

Kubernetes Controller 模式是声明式 API 的核心:用户写"期望状态"(spec),Controller 不断把它与"实际状态"(status)对齐。这种循环叫 **Reconciliation Loop**(调谐循环)。但很多人对它的实现有误解:以为 `r.Get()` 会去打 apiserver。实际上,**所有读都走本地缓存(由 list + watch 预热)**,只有写才走 apiserver。理解这一点是写对 Kubernetes controller 的关键。

**关键概念**:
- **Informer / Indexer**:本地线程安全的 map,键为 `namespace/name`,数据通过 list + watch 流式更新
- **Workqueue**:只存 key 的 FIFO 队列,自带去重;高频事件自动合并
- **Reconcile(ctx, NamespacedName)**:核心循环函数,读 cache → 算 diff → 写 apiserver
- **Eventually consistent**:写 apiserver 后 cache 不会立即更新,要等 watch event 流回来(毫秒级)
- **ResourceVersion**:乐观并发控制(OCC),写时校验,失败返 409 Conflict

## 原理详解

### 整体数据流(摘自官方博客心智模型图)

```
                     ┌─────────────────────────────────┐
                     │       API Server (etcd)         │
                     └──────┬───────────────────┬──────┘
                            │ list + watch      │ Create/Update/Patch
                            ▼                   ▼
                    ┌───────────────┐   ┌────────────────┐
                    │   Reflector   │   │   direct API   │
                    └───────┬───────┘   └────────────────┘
                            │ deltas (有序,不去重)
                            ▼
                    ┌───────────────┐
                    │   DeltaFIFO   │   (有序、无去重)
                    └───────┬───────┘
                            │ (先写 Indexer,再通知 handler)
                            ▼
                ┌───────────────────────────────┐
                │       Indexer / Store         │ ◀── Get/List
                │   (map + RWMutex,thread-safe) │     (从 cache)
                └───────────────┬───────────────┘
                                │ ResourceEventHandler
                                ▼
                       ┌─────────────────┐
                       │   workqueue     │   (key + 去重)
                       └─────────┬───────┘
                                 │
                                 ▼
              Reconcile(ctx, ctrl.Request{NamespacedName})
                                 │
                                 └─▶ Update/Create ─▶ API Server(绕开 cache)
```

### 七大核心知识点

#### ① r.Get / r.List 走本地缓存

| 项目 | 说明 |
|---|---|
| 数据来源 | `mgr.GetClient()` 返回的 client,读操作走**本地 in-memory Indexer** |
| 首次 Get 行为 | 首次 Reconcile 前 cache 已预热完毕(warm-up 发生在 `mgr.Start()`) |
| 底层结构 | `IndexByKey("namespace/name")` → `map[string]Object` + `sync.RWMutex` |
| 读路径性能 | map 查询 + 对象 `DeepCopy`,无 HTTP、无 protobuf |
| 致命陷阱 | **读不到刚刚写入的状态**——watch 事件异步传播,存在窗口期 |

#### ② cache miss → APIReader 回退

`mgr.GetAPIReader()` 返回**绕过缓存、直连 API Server** 的 reader,用于:
- 一次性读取未注册 informer 的资源(为单次操作启动 watch 太昂贵)
- `mgr.Start()` 启动前的读取(此时缓存尚未启动)

#### ③ r.Update 写路径

| 操作 | 路径 |
|---|---|
| Get / List | **缓存**(内存 Indexer) |
| Create / Update / Patch / Apply / Delete | **API Server**(直连) |

**为什么写不经过缓存**:读频繁 → 应该便宜;写稀少 → 应该准确;若写经过缓存会带来**脑裂**(本地以为成功,API Server 已拒绝)。

#### ④ watch 事件流

API Server → Reflector → DeltaFIFO → ResourceEventHandler(OnAdd/OnUpdate/OnDelete)→ workqueue → worker → Reconcile

**关键点**:
- 队列中**只有 key**(如 `default/my-pod`),没有对象体
- 同一 key 重复入队会被静默合并(去重)
- 例如一个 Pod 创建后涌入 5 个 Update 事件,handler 被调 5 次,但 workqueue 中只有 1 条记录,最终只触发 1 次 Reconcile

#### ⑤ Reconcile 内部数据流

```
Reconcile(ctx, req)
   ↓ Get/List (走 cache Indexer)
读取当前状态 + 计算 diff
   ↓ Create/Update/Patch (直走 API Server)
输出新的期望状态
   ↓ API Server 接收写入
触发新一轮 watch 事件 → 再次入队 → Reconcile
```

**幂等性要求**:由于最终一致性,Reconcile **必须幂等**——读到的状态可能滞后,但若有偏差,下次 Reconcile 会自动修正。**不要 sleep 或手动重试**,只需保证逻辑对"晚一点到的状态"也安全。

**延迟触发机制**:用 `ctrl.Result{RequeueAfter: 30 * time.Second}` 而非 `time.Sleep`——若期间真实事件到达,会立即触发而无需等待定时器。

#### ⑥ Manager.Start 启动机制

| 步骤 | 行为 |
|---|---|
| 1 | `mgr.Start(ctx)` 启动所有已注册的 informer |
| 2 | 每个 GVK 的 Reflector 拉取全量快照(经 `ByObject{Namespaces, Label}` 等过滤) |
| 3 | 快照装入 informer 的 store,注册索引重建,标记为 synced |
| 4 | 从快照对应的 `resourceVersion` 开启 watch |
| 5 | **所有源都报告 synced 后**,controller 才启动 worker 出队 → 首次 Reconcile |

#### ⑦ Indexer 索引机制

| 特性 | 说明 |
|---|---|
| 等价查询 | 仅支持 `client.MatchingFields{"spec.nodeName": "node-1"}` |
| 不支持 | 范围查询、`LIKE`、排序、聚合 |
| 索引命名 | 第二参数任意字符串——仅为约定,不会被解析为 JSONPath |
| 无索引查询 | 直接报错,不会退化到全表扫描 |

### 乐观并发控制(409 Conflict)

写 apiserver 时会带上 `resourceVersion`(读时拿到),apiserver 比对:
- 匹配 → 写入成功
- 不匹配 → 返回 **409 Conflict**(他人抢先)

处理:
```python
rc = api.update(obj)
if rc == -2:  # 409
    wq.add(ns, name)  # 重新入队,等下次 watch event
```

## 对比 / 选型

| 实现 | 适用场景 |
|---|---|
| **裸 client-go informer**(本 demo 思路) | 完全控制缓存、批处理、性能优化 |
| **controller-runtime**(kubebuilder 默认) | 90% 场景,声明式 Manager + Reconciler |
| **Operator SDK** | 业务级 operator,封装 Helm/Ansible |
| **k8s.io/client-go 直写** | 不要这么做——会绕过所有缓存与重试逻辑 |

## 环境准备

- **操作系统**:Windows / Linux / macOS(纯算法模拟)
- **语言版本**:
  - C:任意 C99 编译器
  - Python:3.10+
  - Go:1.18+
- **依赖**:无第三方依赖,仅标准库

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -std=c99 reconciler.c -o reconciler
./reconciler
```

### Python

```bash
python3 reconciler.py
```

### Go

```bash
go run reconciler.go
```

## 关键代码片段

### Python:Reconcile 主体

```python
def reconcile(self, key: str):
    ns, _, name = key.partition("/")
    # 1. r.Get reads from CACHE (not apiserver)
    cached = self.cache.get(ns, name)
    if not cached:
        return  # fallback to APIReader
    # 2. Reconcile logic
    if cached.replicas != cached.ready:
        # 3. r.Update writes to APISERVER (bypasses cache)
        updated = Object(..., resource_version=cached.resource_version)
        rc = self.api.update(updated)
        if rc == -2:  # 409 Conflict
            self.wq.add(ns, name)  # retry
```

### Go:Workqueue 去重

```go
func (q *Workqueue) Add(ns, name string) {
    key := ns + "/" + name
    if q.inQueue[key] {
        fmt.Printf("  [wq] dedup: %s already in queue\n", key)
        return
    }
    q.keys = append(q.keys, key)
    q.inQueue[key] = true
}
```

### C:Indexer (map[ns/name] → object)

```c
static object_t *indexer_get(indexer_t *idx, const char *ns, const char *name) {
    char key[64];
    snprintf(key, sizeof(key), "%s/%s", ns, name);
    for (int i = 0; i < idx->n; i++) {
        if (strcmp(idx->entries[i].key, key) == 0 && idx->entries[i].present)
            return &idx->entries[i].obj;
    }
    return NULL;
}
```

## 性能与边界

- **cache 读**:`O(1)`(map 查找)+ `DeepCopy` 开销
- **workqueue 增删**:`O(n)`(slice 头部删除),生产用环形 buffer
- **Reconcile 单次**:取决于业务逻辑,通常 < 100ms
- **内存**:每个 informer 持有所有 watch 对象,**大集群需谨慎**——`controller-runtime cache` 错误配置可占 GB 内存
- **watch 时延**:正常情况 < 1s,API server 压力大时可能数秒
- **409 冲突**:高频并发写场景下常见(尤其 Deployment controller 多副本),需要 backoff + 重试

## 注意事项与常见坑

- **"读到自己刚写的值"陷阱**:写完立刻 Get 会读到旧 cache。常见错误:Update 后立刻 List 校验——不会看到自己的改动
- **必须幂等**:Reconcile 可能被同一对象调多次(spec 改了、status 改了、watch event 来回)。任何 sleep / 累加 / 非幂等操作都会爆
- **cache miss 不一定是错误**:刚启动 / 对象被删除 / namespace filter 过滤都可能导致 miss。fallback APIReader 处理
- **workqueue 只存 key**:很多人误以为可以存对象体——存了会引入 stale 风险,且破坏 dedup 语义
- **`RequeueAfter` vs `time.Sleep`**:`RequeueAfter` 可被真实事件抢先唤醒,`time.Sleep` 会阻塞整轮
- **`resourceVersion` 必须用读到的值**:写时 apiserver 校验,不匹配返回 409(并发控制正确做法)
- **informer 必须 warmed up 再启动 worker**:否则首次 Reconcile 时 cache 为空,会全部 fallback APIReader 击穿 apiserver
- **大集群内存风险**:每个 GVK 一个 informer + 所有对象 + 索引 → 10k 节点 × 100 Pod/节点 = 100 万对象 × 数 KB = GB 级
- **`MatchingLabels` 不是索引**:走 ThreadSafeStore 全遍历,selector 在 DeepCopy 前评估;要下推到 watch 层面用 `cache.ByObject{Label: ...}`

## 参考资料(实际阅读过的权威来源)

- [How the controller-runtime Cache Actually Works | Kubernetes Blog](https://kubernetes.io/blog/2026/07/29/controller-runtime-cache-explained/?utm_source=tldrdevops) — 核心文章,TL;DR + 7 大知识点 + 整体心智模型图 + Indexer 结构 + Manager.Start 流程
- [Kubernetes v1.36: Staleness Mitigation for Controllers | K8s Blog](https://kubernetes.io/blog/2026/04/28/kubernetes-v1-36-staleness-mitigation-for-controllers/?utm_source=tldrdevops) — AtomicFIFO 特性(1.36 解决乱序事件导致的不一致)+ ConsistencyStore 接口
- [Introducing client-go version 6 | Kubernetes Blog](https://kubernetes.io/blog/2018/01/Introducing-Client-Go-Version-6/) — `NewFilteredSharedInformerFactory` 命名空间/标签过滤 informer + DeepCopy 演进
- [API 流式传输增强 K8s API Server 效率 | K8s Blog 中文](https://steering@kubernetes.io/zh-cn/blog/2024/12/17/kube-apiserver-api-streaming/) — WatchListClient 特性(K8s 1.32 默认 Beta,降低大集群内存 10×)
