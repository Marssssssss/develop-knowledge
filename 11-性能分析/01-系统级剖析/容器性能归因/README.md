# 容器性能归因（cgroup v2 与反向诊断）

## 简介

排查「容器里慢」和排查「物理机慢」是两件事，因为有两层机制同时作用：

- **命名空间（namespaces）限制「看得见什么」**：`pid`/`mnt`/`net`/`user`/`uts`/`ipc`/`cgroup` 七个命名空间把宿主视野切掉
- **cgroup 限制「能用多少」**：cpu / cpuset / memory / blkio / pids / devices … 限制用量

**容器 = 命名空间 + cgroup 的组合**。于是最经典的坑出现了：容器里 `top`/`free` 看到的是**宿主**的数据（因为 `/proc` 是宿主的内核接口），而「容器到底能用多少」必须回到 `/sys/fs/cgroup` 去读。

**反向诊断（reverse diagnosis）** 是 Gregg 在 DockerCon 2017 提出的方法：当资源控制是「权重/份额」这类模糊量时，正向推断很难（"这个容器现在是被自己的 share 限制，还是被系统拖累？"）；于是**先穷举全部可能结论，再倒推每种结论各需要哪些指标**。

**关键概念**

| 概念 | 含义 |
| --- | --- |
| `cpu.stat` 的恒定 3 字段 | `usage_usec`/`user_usec`/`system_usec`，**含后代 cgroup** |
| `cpu.stat` 的 CFS 带宽 5 字段 | `nr_periods`/`nr_throttled`/`throttled_usec`/`nr_bursts`/`burst_usec`，**非层级**：只算本 cgroup 自身 cap 造成的节流 |
| `cpu.stat.local` | 本 cgroup runqueue 的**实际**节流时间，**可能含祖先 cap 的牵连** |
| bursting | 份额只保证「保底」，空闲 CPU 可以被超用 |
| `memory.events` vs `.local` | 前者层级式（含后代），后者只算本层 |
| `cgroup.pressure` | 非层级开关；`PSI accounts stalls for each cgroup separately and aggregates it at each level of the hierarchy` |

## 原理详解

### 1. CPU 侧：反向诊断的排除顺序

```
容器的 CPU 为什么不够?
├─ 1. cpu.stat.throttled_usec 在涨?      -> 被自己的 cpu.max 硬 cap 限流(最常见,最容易排除)
├─ 2. 它为 0 但 cpu.stat.local 在涨?     -> 被**祖先** cgroup 的带宽限制牵连
├─ 3. 都不是,但 cpu.pressure some 很高?  -> 宿主/系统级争用(不是容器的配额问题)
└─ 4. CPU 侧完全没有等待                 -> 瓶颈在下游:转 off-CPU / 依赖分析
```

官方原话：*The first step refers to `/sys/fs/cgroup/.../cpu.stat -> throttled_time`, which indicates when a cgroup (container) is throttled by its hard cap (eg, capped at 2 CPUs). Since that's a straightforward metric, we check it first to take that outcome off the operating table.*

第 2 步是本 demo 的重点，也是最容易判错的地方——内核文档明确写着：

> the above five CFS bandwidth stats are **non-hierarchical**; they only account for throttling caused by **this cgroup's own bandwidth limit**, not including throttling inherited from ancestor cgroups.

所以一个 `cpu.stat.throttled_usec == 0` 的容器**仍可能**在被限流。真正的「我这次等了多久」在 `cpu.stat.local`：

> `cpu.stat.local` reports the actual throttling time incurred by this cgroup's own runqueues, which **may include throttling inherited from ancestor cgroup bandwidth limits**.

### 2. CPU shares 的两条公式（bursting 的来源）

```
容器 CPU 上限 = 100% × 自身 shares / 全部「忙」份额      ← 可以抢别家的空闲 CPU(bursting)
容器最小保障 = 100% × 自身 shares / 全部「已分配」份额   ← 全员都忙时的保底
```

同一个容器在「别人空闲」与「全员饱和」两种状态下，可用 CPU 可以差好几倍——这正是官方说的「Why did perf regress? Less bursting available?」。所以 `--cpu-shares` 的调优必须同时看**总分配份额**与**总忙份额**。

### 3. 内存侧：三层阈值与两种视图

| 文件 | 语义 |
| --- | --- |
| `memory.current` | 本 cgroup **及其后代**当前使用总量 |
| `memory.max` | 硬上限；到顶且无法回收 → 在该 cgroup 内触发 OOM killer |
| `memory.high` | 节流上限；越界 → 进程被节流并转入**直接回收**，**永不触发 OOM** |
| `memory.low` | 尽力保护；除非无保护 cgroup 里已无可回收内存，否则不回收 |
| `memory.events` | 层级式：`low`/`high`/`max`/`oom`/`oom_kill`/`oom_group_kill`/`sock_throttled` |
| `memory.events.local` | 只算本层 |

**判定要点**：`memory.events.max > memory.events.local.max` 说明顶到上限的是**后代** cgroup 而不是本层；`current > high` 说明在被节流但**不会** OOM。

### 4. cgroup v2 的两条层级规则

- **No Internal Process Constraint**：非根 cgroup 只有在**自己不持有进程**时，才能把 domain 资源分给子节点。即 `cgroup.subtree_control` 里启用控制器的 cgroup 必须是「空的内部节点」，进程只在叶子。
- **Top-down Constraint**：资源自顶向下分配，子节点的 `cgroup.subtree_control` 只能包含父节点已启用的控制器；父节点不能禁用一个仍被某个子节点启用的控制器。

## 对比

| 维度 | cgroup v1 | cgroup v2 |
| --- | --- | --- |
| 层级 | 每个控制器一棵独立树 | 单一统一层级 |
| CPU 带宽 | `cpu.cfs_quota_us` / `cpu.cfs_period_us` | `cpu.max`（`"$MAX $PERIOD"`） |
| CPU 权重 | `cpu.shares`（默认 1024） | `cpu.weight`（默认 100，范围 1–10000） |
| 内部进程 | 允许父节点带进程 | **No Internal Process** 约束 |
| 压力指标 | 无 | `cpu/memory/io.pressure` + `cgroup.pressure` 开关 |
| 内存事件 | `memory.oom_control` | `memory.events`（+ `.local`） |

## 环境准备

- 操作系统：Python/Go 版跨平台可跑（用内嵌 cgroup 文件样本）；C 版读真实 `/sys/fs/cgroup`，需要 Linux + cgroup v2（`stat -fc %T /sys/fs/cgroup` 应输出 `cgroup2fs`）
- 语言：Python 3.10+ / Go 1.21+ / gcc
- 依赖：无

## 运行方式

```bash
# Python(32 项自检)
python cgroup_attrib.py

# Go
go run .

# C
gcc -O2 -Wall -Wextra -pedantic cgroup_reader.c -o cgroup_reader
./cgroup_reader --selftest
./cgroup_reader /sys/fs/cgroup            # 在容器内读自己的 cgroup
```

## 关键代码片段

非层级字段与实际节流的区别（本 demo 的核心断言）：

```python
def diagnose_cpu(cpu_stat, cpu_local, cpu_pressure, usage_delta_usec):
    own = cpu_stat.get("throttled_usec", 0)
    # 第 1 步: 自己的 cap —— 最容易测,先排除
    if own and usage_delta_usec and own / usage_delta_usec > 0.05:
        return CAP_THROTTLED, f"throttled_usec/usage_usec = {own / usage_delta_usec:.1%}"
    # 第 2 步: 非层级字段为 0 也可能是被祖先限了
    if cpu_local and cpu_local.get("throttled_usec", 0) > 0 and own == 0:
        return ANCESTOR_THROTTLED, "cpu.stat 为 0 但 cpu.stat.local 非 0"
```

层级与本地的事件区分：

```python
if events["max"] > events_local["max"]:
    out.append(("CGROUP_TREE_HIT_MAX", "顶到上限的是后代 cgroup,不是本层"))
if current > high:
    out.append(("HIGH_THROTTLED", "被节流并转入直接回收,但永不触发 OOM"))
```

## 性能与边界

- **`cpu.stat` 的前 3 个字段是层级式的**（含后代），而 CFS 带宽 5 字段不是——同一文件里两种口径并存，这是误判的最大来源。
- **`cgroup.pressure` 的非层级开关是有意设计**：官方给的理由是 PSI 要逐级聚合，深层级下有可观开销，所以允许**只关掉非叶节点**的 PSI 记账而不影响后代。
- 官方提到这类反向诊断「会随时间变简单」——因为内核在持续往 cgroup 里补「状态与停留时间」类指标，值得定期复核。
- 本 demo 的边界：`cpu.stat` 的字段集合按内核文档的 8 个实现；`io.stat` 的 `depth`/`avg_lat`/`win`/`missed`/`total` 附加字段只在启用 `blkcg_debug_stats` 时出现，未实现。
- **PSI 归属的口径差异（重要）**：内核合并补丁里写的是这三种 cgroup 压力文件 *track aggregate pressure stall times for **only the tasks inside the cgroup***；`cgroup-v2` 对 `cpu.pressure` 的表述是 *accounts for all the processes in the cgroup*；而 `cgroup.pressure` 的说明是 *PSI accounts stalls for each cgroup separately and **aggregates it at each level of the hierarchy***。三者合起来可以推出「父节点压力包含后代」，但**没有任何一处明文直说**——本 demo 因此不把「层级聚合」写成断言，只做口径并列呈现。

## 注意事项与常见坑

- **别用 `cpu.stat.throttled_usec == 0` 下结论**：它是非层级的。要看 `cpu.stat.local`。（本 demo 的 fixture 就构造了这种场景：容器自己 `cpu.max` 给了 4 CPU，但父 cgroup 只给 2 CPU，于是 `throttled_usec` 恒 0 而 `.local` 猛涨。）
- **容器里的 `free`/`top`/`/proc/meminfo` 是宿主的**：`memory.max` 与 `MemTotal` 完全不是一回事。判断「容器内存够不够」只能看 `memory.current`/`memory.max`。
- **`memory.high` 与 `memory.max` 是两种故障模式**：前者是「变慢」，后者是「被杀」。只看 OOM 计数会漏掉大量「没被杀但一直在回收」的容器。
- **`pids.current > pids.max` 是可能的**：官方说组织性操作不受 cgroup 策略阻塞（因为策略只拦 `fork()`/`clone()`，会让它们返回 `-EAGAIN`）。所以这个不等式不是 bug。
- **`cpu.max` 写一个数只更新 `$MAX`**，周期保持原值——别以为写一个数就把周期也重置了。
- **`memory.current` 可能短暂超过 `memory.max`**：官方原文提到 "Under certain circumstances, the usage may go over the limit temporarily"。
- **时间单位**：cgroup v2 所有时长都是**微秒**（`usage_usec`/`throttled_usec`/PSI 的 `total`），PSI 的 `avg10/60/300` 才是百分比。

## 参考资料（实际阅读过的权威来源）

- [Control Group v2 — Linux Kernel Documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html) — `cpu.stat` 8 字段及其「恒定 3 个含后代 / CFS 5 个**非层级**」原文、`cpu.stat.local` 原文、`cpu.max`/`cpu.max.burst`/`cpu.weight`/`cpu.weight.nice` 格式、`memory.current`/`max`/`high`/`low`/`events`/`stat` 全字段、`io.stat` 字段与附加字段、`pids.current`/`max`/`events`、`cgroup.pressure` 与「逐级聚合」原文、No Internal Process Constraint 与 Top-down Constraint
- [Container Performance Analysis at DockerCon 2017 — Brendan Gregg](https://www.brendangregg.com/blog/2017-05-15/container-performance-analysis-dockercon-2017.html) — 三类瓶颈、**反向诊断**方法与「第一步查 `cpu.stat -> throttled_time`」原文、容器感知工具清单、Netflix Titus 规模
- [Container Performance Analysis（DockerCon 2017 slides）](https://www.slideshare.net/slideshow/container-performance-analysis-brendan-gregg-netflix/75406874) — 命名空间/cgroup 清单、CPU shares 的两条公式原文（limit / minimum / bursting）
- [PSI - Pressure Stall Information — Linux Kernel Documentation](https://docs.kernel.org/accounting/psi.html) — cgroup2 下每个子目录都有 `cpu.pressure`/`memory.pressure`/`io.pressure` 且格式与 `/proc/pressure` 相同
- [[PATCH 09/10] psi: cgroup support — Johannes Weiner, LKML](https://lore.kernel.org/cgroups/20180712172942.10094-10-hannes@cmpxchg.org/) — 合并补丁原文 "track aggregate pressure stall times for **only the tasks inside the cgroup**"（用于并列呈现口径差异）
- 配套书：《Systems Performance》第 2 版第 11 章（Cloud and Virtualization）
