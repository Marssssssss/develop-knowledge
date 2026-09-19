# kube-scheduler Scheduling Framework 扩展点

## 简介

kube-scheduler 不是一堆 if-else,而是一组**编译进二进制的插件 API**。官方把这套架构叫 *Scheduling Framework*,自 Kubernetes v1.19 起 Stable。调度一个 Pod 的一次尝试被切成两个阶段:**调度周期(scheduling cycle)** 选 node,**绑定周期(binding cycle)** 把决定落到集群,合称一个 scheduling context。调度周期**串行**,绑定周期**可以并发**。

最容易搞错的四件事,本项目用可运行代码逐条验证:

1. **Filter 在 node 内短路** —— 某个 plugin 判定 node 不可行后,该 node 剩下的 plugin 根本不会被调用(node 之间才可以并发)。
2. **PostFilter 只在「一个可行 node 都没有」时才触发** —— 它的典型实现就是抢占;成功只是**提名**(`NominatedNodeName`),本次并不绑定。
3. **NormalizeScore 拿到的是「自己这个 plugin」的全部 node 分数**,每轮调度每个 plugin 只调一次;调度器在此之后才按 `weight` 加权合并。
4. **Reserve 失败会逆序回滚**:`Unreserve` 对**所有**已 Reserve 的 plugin 按 Reserve 调用的**逆序**执行;Permit 的 `deny` 与 `wait` 超时都会触发它。

## 原理详解

### 扩展点全景(按触发顺序)

```
                    ┌──────────── PreEnqueue ────────────┐
                    │  (不入 activeQ 就结束,不打         │
                    │   Unschedulable condition)         │
                    └────────────────┬───────────────────┘
                                     ▼
   ┌─────────── 调度周期 scheduling cycle(串行) ───────────┐
   │  QueueSort      → 给队列排序,同时只能启用一个        │
   │  PreFilter      → 预处理 / 前置检查,出错即中止       │
   │  Filter         → 过滤;node 内短路,node 间可并发    │
   │  PostFilter     → 仅当 Filter 后无可行 node;=抢占    │
   │  PreScore       → 生成 Score 阶段可共享的状态        │
   │  Score          → 给每个通过 Filter 的 node 打分     │
   │  NormalizeScore → 归一化到自己 plugin 的分数区间     │
   │  Reserve        → 预留资源(有状态插件),失败即回滚  │
   │  Permit         → approve / deny / wait(带超时)      │
   └──────────────────────────┬──────────────────────────┘
                              ▼
   ┌─────────── 绑定周期 binding cycle(可并发) ───────────┐
   │  PreBind  → 绑定前准备(如挂网络卷)                  │
   │  Bind     → 真正绑定;首个接管的 plugin 之后全跳过   │
   │  PostBind → 纯通知,绑定周期终点                     │
   └─────────────────────────────────────────────────────┘
```

官方文档还定义了 `EnqueueExtension` / `QueueingHint`(v1.34 Stable,默认开启):实现 PreFilter/Filter/Reserve/Permit 的插件应当实现它,用来决定「集群里发生的某个事件是否值得把被拒的 Pod 重新入队」。

### Filter 的短路语义

> 原文:*"For each node, the scheduler will call filter plugins in their configured order. If any filter plugin marks the node as infeasible, the remaining plugins will not be called for that node. Nodes may be evaluated concurrently."*

所以「Filter 被调用的总次数」不是 `插件数 × node 数`,而是每个 node 各自在被拒处截断。本项目断言:n2 只剩 1 CPU 时,`Filter` trace 里只出现 `NodeResourcesFit`,`NodeUnschedulable` 一次都没被调用。

### PostFilter 与抢占

> 原文:*"These plugins are called after the Filter phase, but only when no feasible nodes were found for the pod... If any postFilter plugin marks the node as Schedulable, the remaining plugins will not be called. A typical PostFilter implementation is preemption."*

抢占成功只写 `NominatedNodeName`,**本次调度周期不绑定**。本项目用「两个 node 都被低优先级 Pod 占满」的场景,断言高优先级 Pod 得到提名、低优先级 Pod 提名失败。

### NormalizeScore 与权重

官方示例(原文照搬):

```go
func NormalizeScores(scores map[string]int) {
    highest := 0
    for _, score := range scores { highest = max(highest, score) }
    for node, score := range scores { scores[node] = score*NodeScoreMax/highest }
}
```

注意归一化**只作用于同一 plugin 自己的分数**,归一化之后调度器才按配置的 plugin `weight` 加权求总。本项目的 `LeastAllocated` 给 n1 打 100、n2 打 25,归一化后仍是 100/25;再与不打分的 `NodeResourcesFit`(恒 0)按权重 1:1 合并,最终分是 50 与 12.5 —— 断言的就是这两个值,不是 100/25。

### Reserve / Unreserve / Permit

> 原文:*"The Unreserve phase is triggered if the Reserve phase or a later phase fails. When this happens, the Unreserve method of **all** Reserve plugins will be executed in the reverse order of Reserve method calls."*

Permit 的三态:approve(全通过才去绑定)、deny(回队列 + 触发 Unreserve)、wait(带超时;超时 → deny)。本项目用 `DenyAll` / `WaitForever` 两个插件断言 deny 与 wait 都会产出逆序 Unreserve 序列。

## 对比

| 机制 | 触发条件 | 失败/拒绝后的行为 |
| --- | --- | --- |
| PreFilter | 每个调度周期一次 | 出错即中止整个调度周期 |
| Filter | 每 node 一轮 | node 内短路,该 node 被标记不可行 |
| PostFilter | **仅**无任何可行 node | 返回提名 node;失败则 Pod 回队列 |
| Score | 每个可行 node | 不失败,只影响排序 |
| NormalizeScore | 每 plugin 每周期一次 | 出错即中止调度周期 |
| Reserve | 选定 node 之后 | 失败 → 逆序 Unreserve 全部已 Reserve 插件 |
| Permit | 调度周期末尾 | deny / 超时 → Unreserve + 回队列 |
| Bind | 全部 PreBind 之后 | 首个接管的 plugin 之后全部跳过 |

## 环境

- Python 3.8+(仅标准库),可直接运行
- Go 1.18+(仅标准库),无三方依赖,本机无工具链时按人工审查
- C99 编译器(仅标准库)

## 运行方式

```bash
python python/scheduler_framework.py     # 27 项断言
go run go/scheduler_framework.go
gcc -std=c99 -o /tmp/sf c/scheduler_framework.c && /tmp/sf
```

## 关键代码

```python
# Filter:node 内短路
for n in nodes:
    rejected = None
    for p in self.plugins:
        self.trace.append(("Filter", p.name, n.name))
        reason = p.filter(self, pod, n)
        if reason:
            rejected = reason
            break                      # 该 node 剩下的 plugin 不再调用
    if rejected is None:
        feasible.append(n)

# Reserve:失败则逆序回滚全部已 Reserve 的 plugin
for p in self.plugins:
    self.trace.append(("Reserve", p.name))
    if not p.reserve(self, pod, best):
        self._unreserve_all(pod, best, reserved)   # reversed(reserved)
        return out
```

## 性能边界

- Filter 是 O(插件数 × node 数)上界,短路后实际远小于此;node 之间并发求值,所以单个慢插件只拖慢自己的 node。
- Score 是 O(插件数 × 可行 node 数),NormalizeScore 是 O(插件数 × 可行 node 数)。官方默认**不会对全量 node 打分**:`percentageOfNodesToScore`(默认按集群规模自适应)会在找到足量可行 node 后提前停止。
- 调度周期串行是吞吐瓶颈所在;绑定周期并发,所以 `Bind` 插件必须自己保证幂等与并发安全。
- 抢占发生在 Filter 全灭之后,代价是一次额外的「驱逐哪些 victim」计算,且要满足 PodDisruptionBudget。

## 注意事项与常见坑

- **PostFilter 不等于「每次调度都跑」**。很多人以为抢占插件一直在跑,实际它只在无可行 node 时被调用。
- **抢占成功 ≠ 已调度**。`NominatedNodeName` 只是提示,下一个调度周期才真正绑定。
- **`Unreserve` 必须幂等且不允许失败**(官方原文 *"must be idempotent and may not fail"*),因为它可能在部分状态已变更时被调用。
- **归一化后最高分是 `NodeScoreMax`(100),不是原始分**。跨插件比较分数没有意义,只能在归一化 + 加权之后比。
- **Bind 插件顺序敏感**:第一个返回「已处理」的插件之后全部被跳过,把兜底 Bind 插件放前面会吞掉后面所有插件。
- PreEnqueue 拒绝的 Pod 不会带 `Unschedulable` condition —— 排查「Pod 一直在队列里但没事件」时要想到这一层。

## 参考资料

已实际阅读:

1. Kubernetes 官方文档 — Scheduling Framework,https://kubernetes.io/docs/concepts/scheduling-eviction/scheduling-framework/
2. `kubernetes/kubernetes@master` — `pkg/scheduler/framework/interface.go`,https://cdn.jsdelivr.net/gh/kubernetes/kubernetes@master/pkg/scheduler/framework/interface.go
3. Kubernetes 官方文档 — Scheduler Configuration(extension points / profiles),https://kubernetes.io/docs/reference/scheduling/config/
