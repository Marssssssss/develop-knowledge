# CFS 完全公平调度器:vruntime 与红黑树的最小模拟

## 简介

CFS(CFS Scheduler,Ingo Molnar 设计,Linux 2.6.23 合入)把调度建模为
"理想的精确多任务 CPU":**80% 的设计一句话讲完——在真实硬件上模拟一台
每个任务都以 1/nr_running 精确等速并行运行的理想 CPU**。实现核心是每任务
`p->se.vruntime`(纳秒精度)加一棵按 vruntime 排序的红黑树。本 demo 用三种
语言实现同一最小模拟器:vruntime 记账、最左节点选取、min_vruntime 单调推进、
新任务/睡眠者放置,并断言权重比例即 CPU 份额。

关键概念:

- **vruntime**:实际运行时间按权重归一化后的"虚拟运行时间"。
- **最左节点(leftmost)**:红黑树上 vruntime 最小的任务 = 亏欠 CPU 最多者。
- **min_vruntime**:运行队列的单调递增值,跟踪全部任务的最小 vruntime,
  用来放置新激活实体。
- **granularity(粒度)**:当前任务与最左任务的 vruntime 差超过它才切换,
  避免过度调度打爆缓存。

## 原理详解

### 1. 理想 CPU 模型与 vruntime(设计文档 §1/§2)

理想多任务 CPU 有 100% 算力、n 个任务各以 1/n 精确等速运行;真实 CPU 一次
只能跑一个任务,于是引入 **vruntime = 实际运行时间按运行任务数归一**。理想
状态下所有任务 vruntime 恒相等——不平衡(多拿了/少拿了)直接体现在 vruntime
差上。记账户:

```text
任务跑 delta 后:  vruntime += delta * NICE_0 / weight
                 (weight 大 → vruntime 涨得慢 → 可以多跑;NICE_0 为基准权重)
```

### 2. 选取逻辑:最左节点 + 粒度(设计文档 §3)

所有可运行任务按 vruntime 为键放在**时间有序红黑树**里;CFS 永远挑**最左**
(vruntime 最小 = 执行得最少)的任务。任务跑一小段后被记账、越跑越靠右,
其他任务迟早成为新的最左节点——每个任务都能在**确定时间内**上 CPU。
真正的切换多加一层粒度保护:当前任务 vruntime 与最左任务差超过 granularity
才抢占(防过度调度)。

### 3. min_vruntime 与新实体放置(设计文档 §3)

`rq->cfs.min_vruntime` 是**单调递增**值,跟踪队列中最小的 vruntime。它标记
"系统已完成的功";新激活实体(新 fork 的任务、睡眠醒来者)用它来放置——
**尽量放在树的左侧**(给点优惠)但不至于无中生有地跑到所有人前面。本模拟
采用最简放置:`vruntime = max(自身旧值, min_vruntime)`(睡眠者反投机的关键,
精确的补偿数值内核演进中多次调整,以源码为准)。

### 4. 没有时间片(设计文档 §4)

CFS **纳秒精度记账,不依赖任何 jiffies/HZ 细节**,没有旧调度器意义上的
"time slice"概念,也没有任何启发式;唯一中心化可调参数
`/sys/kernel/debug/sched/base_slice_ns`(桌面低延迟 ↔ 服务器批处理)。

### 5. 策略与调度类(设计文档 §5/§6)

CFS 实现三种策略:`SCHED_NORMAL`(常规)、`SCHED_BATCH`(不常被抢占,利缓存
伤交互)、`SCHED_IDLE`(比 nice 19 还弱)。RT 的 `SCHED_FIFO/_RR` 在另一模块。
调度类(`sched_class`)是一组钩子(`enqueue_task` / `dequeue_task` /
`pick_next_task` / `task_tick`…),调度核心不假设策略细节。

### 6. 组调度(设计文档 §7)

`CONFIG_FAIR_GROUP_SCHED` 允许把任务分组、先在组间公平再在组内公平
(cgroup 的 `cpu.shares`,如 multimedia=2048 vs browser=1024 即 2:1 带宽)。

### 7. 现状:EEVDF 正在取代 CFS

内核 6.6 起 fair class 已实现 **EEVDF**(Peter Zijlstra 2023 版):同样用
虚拟运行时间,但引入 **lag**(欠账:正=被欠 CPU、负=超额)定义资格
(lag ≥ 0),按**最早虚拟截止时间(VD)**选取;睡眠任务用 VRT 衰减 lag 防止
"睡一下重置负 lag"的投机。注意:**本文/CFS 设计文档 §2 §3 的"最左节点 +
min_vruntime"描述的是原始 CFS,6.6+ 代码已不再如此**——但 vruntime 归一化
思想被 EEVDF 完整继承,本 demo 模拟的是 CFS 原始设计。

## 环境准备

- Python:3.8+(本仓库 Windows 自检方式);C:gcc;Go:1.21+。
- 纯内存模拟,无特权、无内核依赖(不读写 debugfs)。

## 运行方式

```bash
python3 main.py      # 完整断言:权重份额/单调性/迟到者/睡眠者反投机
gcc -O2 -Wall -Wextra main.c -o cfs_demo && ./cfs_demo
go run .
```

## 关键代码片段

```python
# 记账(原理 §1):权重 1024 vs 512 → 同样 8ms,vruntime 各涨 8 / 16
task.vruntime += delta * NICE_0 // task.weight

# 选取(原理 §2):最左 = vruntime 最小
leftmost = min(runnable, key=lambda t: t.vruntime)

# 放置(原理 §3):新/醒任务以 min_vruntime 兜底,防止睡眠投机
task.vruntime = max(task.vruntime, sim.min_vruntime)
```

## 性能与边界

- 真实 CFS 挑最左是红黑树 O(log n)(缓存的 leftmost 指针实际 O(1));
  本 demo 用线性扫描 O(n),演示规模无感。
- 粒度切换保证"确定性延迟上界"——每个任务等待上界与任务数、粒度成比例。
- nice 值到权重的精确映射表在内核源码(`kernel/sched/core.c` 的
  `sched_prio_to_weight[]`,相邻 nice 约 1.25 倍);本 demo 直接把 weight
  作为参数(1024/512),映射表数值未逐格核读,以源码为准。

## 注意事项与常见坑

- **现象**:两个 nice 差 1 的任务份额差远超 10% → **原因**:记住了
  "CPU 百分比 nice 语义"的旧习惯 → **CFS 是权重比例语义**,份额=权重比。
- **现象**:睡眠醒来的任务独占 CPU 一长段 → **原因**:醒者 vruntime 远小于
  min_vruntime,成了极端最左 → **规避**:放置时以 min_vruntime 兜底(本 demo
  §3 断言;内核对睡眠者补偿策略多次演进)。
- **坑**:照着本 demo(或老博客)去读 6.6+ 内核源码会对不上——EEVDF 已换掉
  最左选取逻辑,见原理 §7。

## 参考资料(实际阅读过的权威来源)

- [CFS Scheduler — The Linux Kernel documentation](https://docs.kernel.org/scheduler/sched-design-CFS.html) —
  理想 CPU 模型、vruntime 定义、红黑树最左选取、min_vruntime 放置、
  base_slice_ns、调度策略与调度类、组调度
- [EEVDF Scheduler — The Linux Kernel documentation (7.1)](https://docs.kernel.org/7.1/scheduler/sched-eevdf.html) —
  6.6 起以 EEVDF 取代 CFS、lag/虚拟截止时间、睡眠任务 VRT 衰减
  (本页经 WebSearch 检索阅读摘要,与 CFS 文档 §1 的"CFS is making room for
  EEVDF"相互印证)
