# cgroup v2 线程模式与 cpuset 分区

> 默认 cgroup 的资源域是**进程**：一个进程的所有线程必须在同一个 cgroup。线程模式（threaded）打破这条限制，把线程散到子树里、同时保留一个共同的资源域；cpuset 分区则在这个层级上再切出**独占 CPU 的调度域**。两者都是 cgroup v2 里"看起来只是写个文件、实际有一堆拓扑约束"的典型。

权威来源（本 demo 实际读过）：

- `torvalds/linux` `Documentation/admin-guide/cgroup-v2.rst`（**135851 B**，master 分支原文）：§Threaded 模式的四种 `cgroup.type` 取值、转 threaded 的两个条件、`EOPNOTSUPP`、threaded 控制器清单；§CPUSET 的 `cpuset.cpus.exclusive` / `cpuset.cpus.exclusive.effective` / `cpuset.cpus.partition`
- `torvalds/linux` `Documentation/admin-guide/cgroup-v1/cpusets.rst`（37976 B）：cpuset 的基本语义与历史

```bash
python python/main.py                  # 演示入口
python python/selfcheck_cgthreaded.py  # 47 项断言
go run go/cg_threaded.go go/main.go
```

## 1. `cgroup.type` 的四种取值

| 读值 | 含义 |
| --- | --- |
| `domain` | 普通的域 cgroup |
| `domain threaded` | **thread root**：它是某棵线程子树的资源域 |
| `threaded` | 线程 cgroup，是某个 thread root 下的成员 |
| `domain (invalid)` | 拓扑非法——生在 threaded 之下却还没转成 threaded，**不能用** |

关键点：

- 新建的 cgroup 一律是 `domain`，写 `echo threaded > cgroup.type` 才能变 threaded，**单向不可逆**；
- 一个 domain cgroup 在「有子 cgroup 变 threaded」或「自己有进程且开了 threaded 控制器」时，会**自动**变成 `domain threaded`；条件消失就退回 `domain`；
- `domain (invalid)` 上的操作一律 `EOPNOTSUPP`（文档明确：*Operations which fail due to invalid topology use EOPNOTSUPP as the errno*）；
- **根 cgroup 豁免** no-internal-process 约束，所以它既能当 thread root，又能同时拥有 domain 子 cgroup。

## 2. 转 threaded 的两个条件

要让一个 cgroup 变 threaded：

1. 父必须是「有效的（threaded）domain」或「threaded cgroup」；
2. **父若是未线程化的 domain**：它不能开了任何域控制器（domain controller），也不能有已填充的 domain 子 cgroup。**根 cgroup 豁免这条**。

第二个条件很容易踩：你在一个刚建好、看起来很干净的 cgroup 下开线程模式，失败原因往往是它的兄弟 cgroup 里已经跑着进程。

threaded 控制器目前只有 4 个：`cpu`、`cpuset`、`perf_event`、`pids`。在 threaded 子树里**只能**开这四个；其余（`memory`、`io` 等）一律 `EOPNOTSUPP`。

## 3. 资源域：线程散开了，域还在哪

threaded 子树**豁免 no-internal-process 约束**——threaded 控制器可以开在**非叶** cgroup 上，哪怕它里面没有线程。这带来一个必须回答的问题：不属于任何特定线程的资源消耗算谁的？

答案是 **thread root**：它是整棵子树的资源域。

- `cgroup.procs` 在 thread root 里包含**整个子树**的所有 PID，而在子树内部**不可读**；
- `cgroup.threads` 是逐线程的，格式同 `cgroup.procs`，但**只能在同一 thread root 内迁移**——写它不会把线程挪到别的域去；
- 反过来，`cgroup.procs` 可以从子树**任何位置**写，语义是把该进程的**所有线程**迁到目标 cgroup。

## 4. cpuset 分区：member / root / isolated

`cpuset.cpus.partition` 只对**非根** cpuset cgroup 存在，写值只有三个：

| 值 | 含义 |
| --- | --- |
| `member` | 分区的非根成员（所有非根 cgroup 的初值） |
| `root` | 分区根：一个新的调度域 |
| `isolated` | 分区根，且**关闭负载均衡**、从 unbound workqueue 中排除 |

读值多两种形态：`root invalid (<原因>)` / `isolated invalid (<原因>)`——**失效的分区根不是报错，而是降级**：状态信息还在，但行为更像 member。文档说三者之间**所有**迁移方向都允许。

根 cgroup 恒为 partition root，状态不可改；它的 `cpuset.cpus*` 隐式等于 `/sys/devices/system/cpu/possible`。

## 5. local 与 remote 分区

- **local**：父本身是有效 partition root；
- **remote**：父不是有效 partition root。

区别在创建成本上：

- local 分区**不必**预先写 `cpuset.cpus.exclusive`——没写时它隐式等于 `cpuset.cpus`；
- remote 分区**必须**在创建前把 `cpuset.cpus.exclusive` 沿层级一路写下来，否则拿不到独占 CPU。

另外一条硬约束：**remote 分区不能建在 local 分区之下**——除根以外，remote 分区根的所有祖先都不能是 partition root。

## 6. 有效性三条件

一个 partition root 要"有效"，local 需三条全中，remote 只需后两条：

1. **父是有效 partition root**（local 专用）；
2. `cpuset.cpus.exclusive.effective` **非空**（可以含 offline CPU）；
3. `cpuset.cpus.effective` 非空，**除非该分区没有任何任务**。

两个推论（都在自检里）：

- 兄弟之间抢同一批 CPU 会让**双方**的 `exclusive.effective` 同时落空，于是双双 invalid——独占是按"申请集互斥"算的，不是先到先得；
- 把一个有效 partition root 改成 `member`，它下面所有 **local** 子分区立刻失效（父不再是有效 partition root）；改回去又能恢复，因为"失效"只是降级态、状态信息还在。

外部事件（CPU hotplug、改 `cpuset.cpus` / `cpuset.cpus.exclusive`）也会让有效分区变无效，反之亦然。分区状态变化会触发 poll/inotify 事件，用户态守护进程不用轮询。

`isolated` 的额外语义：分区内的 CPU 不参与负载均衡、也不跑 unbound workqueue，所以**多 CPU 的 isolated 分区里的任务要自己绑核**才有意义。启动时用 `isolcpus` 预置的 CPU 只能放进 isolated 分区。

## 7. 与既有 demo 的分工

- `Cgroups-v2`：v2 的单一层级、`subtree_control`、`memory.max`、`cpu.max`（**基础限制**）；
- `Cgroup-eBPF附加`：BPF 程序挂到 cgroup 上（**可编程控制**）；
- 本 demo：线程粒度的资源域与 CPU 硬分区（**拓扑与调度域**）。

## 8. 自检覆盖

47 项断言，分六组：`cgroup.type` 四态与不可逆、invalid 拓扑拒绝放进程、根豁免；转 threaded 的两个条件（域控制器、已填充 domain 子、干净父、根豁免、threaded 子树只能开 threaded 控制器）；partition 有效性（local 判定、兄弟抢 CPU 导致 `exclusive.effective` 空、有任务但 `cpus.effective` 空、任务迁走后恢复）；local/remote（父非 partition root 时是 remote、祖先有 partition root 时 remote 建不出来、member 不是 partition root）；状态迁移（root↔isolated↔member 全方向、父子失效传播与恢复）；`exclusive.effective` 的继承与互斥。
