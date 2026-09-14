# PMU 硬件计数器（perf_event_open）

## 简介

**PMU（Performance Monitoring Unit）** 是 CPU 里那块专门数"已退休指令、周期、缓存未命中、分支误预测"的硬件。每个事件占一个**硬件计数器槽位**，槽位数量由微架构决定。Linux 用它做**真正的零侵入剖析**：不插桩、不改代码，直接读硬件。用户态接口是 `perf_event_open(2)` 这个系统调用——`perf` 命令、PAPI、VTune 底层都是它。

关键概念：

- **counting 事件**：只累加总数，用 `read(2)` 取值（算 IPC、miss rate）
- **sampling 事件**：每溢出 N 次就往 mmap 环形缓冲区写一条样本（找热点）
- **group（事件组）**：组作为整体被调度，保证各成员的计数发生在**同一段已执行指令**上，比值才有意义
- **多路复用（multiplexing）**：事件数超过计数器槽位时轮转，用 `time_enabled`/`time_running` **缩放估算**
- **PEBS + `precise_ip`**：硬件在指令**退休时**取寄存器快照，把 skid 降到 0

## 原理详解

### 1. 系统调用原型与 pid/cpu 语义

```c
int syscall(SYS_perf_event_open, struct perf_event_attr *attr,
            pid_t pid, int cpu, int group_fd, unsigned long flags);
```

glibc **没有** wrapper，只能走 `syscall(2)`。返回一个 fd，之后可 `read` / `mmap` / `ioctl` / `fcntl`。

| pid | cpu | 含义 |
| --- | --- | --- |
| 0 | -1 | 测量调用进程/线程（任意 CPU） |
| 0 | ≥0 | 测量调用进程/线程（仅在该 CPU 上运行时） |
| >0 | -1 | 测量指定进程/线程 |
| >0 | ≥0 | 测量指定进程/线程（仅在该 CPU 上） |
| -1 | ≥0 | 测量该 CPU 上**所有**进程（需 `CAP_PERFMON`/`CAP_SYS_ADMIN`，或 `perf_event_paranoid < 1`） |
| -1 | -1 | **非法** |

`flags`：`PERF_FLAG_FD_CLOEXEC`（避免与 fork/execve 的竞态）、`PERF_FLAG_FD_NO_GROUP`、`PERF_FLAG_PID_CGROUP`（per-container 监控，仅系统级事件）。

### 2. struct perf_event_attr 关键字段

`size` 用 `sizeof(struct perf_event_attr)` 填，内核靠它做**前向/后向兼容**（`PERF_ATTR_SIZE_VER0`=64 → `VER5`=112 逐步长大）。

| 字段 | 作用 |
| --- | --- |
| `type` / `config` | 事件类别（`HARDWARE`/`SOFTWARE`/`HW_CACHE`/`TRACEPOINT`/`RAW`/`BREAKPOINT`；动态 PMU 取 `/sys/bus/event_source/devices/<pmu>/type`，core CPU PMU **通常为 4**）与具体事件（不够用时还有 `config1`/`config2`） |
| `sample_period` / `freq` / `sample_freq` | > 0 即成为**采样**事件（每 N 次溢出写一条样本）；设了 `freq` 就按**频率**采样，内核每 tick 调整周期 |
| `sample_type` | 每条样本带什么：`PERF_SAMPLE_IP/TID/TIME/ADDR/READ/CALLCHAIN/CPU/PERIOD/RAW/BRANCH_STACK/REGS_INTR/WEIGHT/DATA_SRC…` |
| `read_format` | `PERF_FORMAT_TOTAL_TIME_ENABLED` / `_TOTAL_TIME_RUNNING`（缩放用）、`_ID`、`_GROUP`（一次读整组）、`_LOST` |
| `disabled` / `inherit` | 初始是否禁用（组内通常 leader `disabled=1`、成员 `0`，leader 一开全开）/ 同时统计**新建**子任务（对 `PERF_FORMAT_GROUP` 无效） |
| `pinned` / `exclusive` | 尽量常驻计数器（仅硬件、仅 group leader；抢不到槽位即 error 态，`read` 返回 0）/ 要求独占计数器（NMI watchdog 等会**导致它永不运行**） |
| `exclude_user/kernel/hv/idle` | 按特权级/空闲过滤 |
| `mmap` / `mmap_data` | 记录 `PROT_EXEC` / 非可执行 的 mmap，用于把地址翻译回代码 |
| `comm` / `task` | 记录进程改名（execve/PR_SET_NAME）与 fork/exit 通知 |
| `precise_ip` | 0–3，控制 **skid**：3 = 必须在 CPU 支持时取 0 skid 采样（见 §5） |

### 3. 事件类型与 config 取值

**`PERF_TYPE_HARDWARE`（通用硬件事件）**：`CPU_CYCLES`、`INSTRUCTIONS`、`CACHE_REFERENCES`（通常指 LLC 访问）、`CACHE_MISSES`、`BRANCH_INSTRUCTIONS`、`BRANCH_MISSES`、`BUS_CYCLES`、`STALLED_CYCLES_FRONTEND`、`STALLED_CYCLES_BACKEND`、`REF_CPU_CYCLES`（3.3+，不受频率缩放影响）。

**`PERF_TYPE_SOFTWARE`（内核合成事件）**：`CPU_CLOCK`、`TASK_CLOCK`、`PAGE_FAULTS`、`CONTEXT_SWITCHES`、`CPU_MIGRATIONS`、`PAGE_FAULTS_MIN`（不需磁盘 I/O）、`PAGE_FAULTS_MAJ`（需要磁盘 I/O）、`ALIGNMENT_FAULTS`、`EMULATION_FAULTS`、`DUMMY`（不计数，只用来挂 mmap/comm 记录）、`BPF_OUTPUT`（4.4+）、`CGROUP_SWITCHES`（5.13+，切到**不同 cgroup** 才计）。

**`PERF_TYPE_RAW`**：直接用 CPU 手册里的原始事件码（Intel SDM / AMD 手册）；libpfm4 可把架构手册里的名字翻译成原始十六进制。

### 4. 硬件缓存事件的编码公式

```c
config = (perf_hw_cache_id) |
         (perf_hw_cache_op_id << 8) |
         (perf_hw_cache_op_result_id << 16);
```

| 维度 | 取值 |
| --- | --- |
| **id**（缓存对象） | `L1D` / `L1I` / `LL`（末级） / `DTLB` / `ITLB` / `BPU`（分支预测单元） / `NODE`（3.1+，本地内存访问） |
| **op**（操作，<<8） | `READ` / `WRITE` / `PREFETCH` |
| **result**（结果，<<16） | `ACCESS`（访问次数） / `MISS`（未命中次数） |

于是 `L1D` 的"读未命中"= `L1D | (READ<<8) | (MISS<<16)`。**未命中率**才能用 `MISS / ACCESS` 算出来。

### 5. 采样、mmap 与 rdpmc 快路径

mmap 大小必须是 **`1 + 2^n` 页**：第一页是**元数据页** `struct perf_event_mmap_page`，其余是环形缓冲。元数据页里的关键字段：

| 字段 | 用途 |
| --- | --- |
| `index` / `offset` / `pmc_width` | `rdpmc` 的计数器序号、基数修正、返回值位宽（用于**符号扩展**） |
| `time_enabled` / `time_running` | 缩放因子来源 |
| `cap_user_rdpmc` / `cap_user_time` / `cap_user_time_zero` | 能否走用户态快路径（3.12+ 才有正确的 bit 分离；更早的内核两个 bit 挤在同一位置） |
| `time_shift` / `time_mult` / `time_offset` / `time_zero` | TSC ↔ 时间戳换算 |
| `data_head` / `data_tail` | 环形缓冲头尾；`data_head` **不回绕**，读后需 `rmb()` |

**rdpmc 路径**（`cap_user_rdpmc` 为真，x86 上即 `rdpmc` 指令）——绕开 `read(2)` 系统调用：

```c
do {
    seq = pc->lock;  barrier();
    enabled = pc->time_enabled; running = pc->time_running;
    idx = pc->index; count = pc->offset;
    if (pc->cap_user_rdpmc && idx) {
        width = pc->pmc_width;
        count += rdpmc(idx - 1);
    }
    barrier();
} while (pc->lock != seq);        // seqlock:被写者打断就重读
```

`precise_ip` 与 **PEBS**：`precise_ip=0` 允许任意 skid；`1` 要求恒定 skid；`2` 请求 0 skid；`3` 要求必须 0 skid。配合 `PERF_SAMPLE_REGS_INTR`，在 Intel x86 上会拿到**采样指令退休时**的寄存器值（PEBS 精确事件）。

### 6. 多路复用与缩放公式（本 demo 的核心）

事件数 > 计数器槽位时，事件只能**一部分时间**真的在计数器上跑。`time_enabled`（被启用总时间）与 `time_running`（真在计数器上的时间）之比就是缩放因子——**两者不相等即意味着发生了多路复用**，差距越大估算误差通常越大：

```c
quot  = count / running;
rem   = count % running;
count = quot * enabled + (rem * enabled) / running;   // 整数版缩放
```

### 7. 组读

```c
struct read_format {          // 指定 PERF_FORMAT_GROUP 时
    u64 nr;                   // 事件个数
    u64 time_enabled;         // if PERF_FORMAT_TOTAL_TIME_ENABLED
    u64 time_running;         // if PERF_FORMAT_TOTAL_TIME_RUNNING
    struct { u64 value; u64 id; u64 lost; } values[nr];
};
```

组 leader 用 `group_fd = -1` 创建，成员用 leader 的 fd 创建。**只有整组都能上 CPU 才会上**，这正是"组内比值可信"的前提。缓冲区不够大时 `read` 返回 **`ENOSPC`**。

## 对比 / 选型

| | counting | sampling | tracepoint |
| --- | --- | --- | --- |
| 输出 | 一个数 | 样本流（ip/栈） | 结构化事件 |
| 开销 | 极低 | 中（看采样率） | 低 |
| 回答 | "多少次/多快（IPC）" | "热在哪里（火焰图）" | "发生了什么事，参数是多少" |
| 数据量 | O(1) | O(采样数) | O(事件数) |

与 ftrace / eBPF 的分工：**PMU 回答"CPU 干了多少活"，ftrace / eBPF 回答"这活是谁干的"**。生产上常见组合是 `perf stat`（PMC 定调）→ `perf record -g` + 火焰图（定热点）→ eBPF（按需聚合）。

## 环境准备

- 操作系统：Linux；`perf_event_paranoid` 决定非 root 能读多少（`< 1` 才允许系统级 `cpu=-1`）
- 语言：Python 3.8+ / Go 1.21+ / C（GCC，需 `<linux/perf_event.h>`）；权限需 `CAP_PERFMON`（5.9 起）或 `CAP_SYS_ADMIN`
- **本 demo 的 Python/Go 部分离线可跑**

## 运行方式

```bash
gcc -O2 -Wall -Wextra -pedantic pmu_group.c -o pmu_group && ./pmu_group   # C:真开一个 3 事件组;EPERM 时降级为说明模式
python3 pmc_multiplex.py    # Python:模拟 4 槽位多路复用,比较 真值/测量值/缩放估算值
go run .                    # Go:硬件缓存事件编码/解码矩阵 + IPC/miss rate 派生
```

## 关键代码片段

C 侧最核心的是**打开一个组并读整组**：

```c
struct perf_event_attr attr = {0};
attr.type = PERF_TYPE_HARDWARE;
attr.size = sizeof(attr);
attr.config = PERF_COUNT_HW_INSTRUCTIONS;
attr.read_format = PERF_FORMAT_GROUP | PERF_FORMAT_TOTAL_TIME_ENABLED
                 | PERF_FORMAT_TOTAL_TIME_RUNNING;
int leader = syscall(SYS_perf_event_open, &attr, 0, -1, -1, 0);   // group_fd = -1 建 leader
/* 后续事件用 leader 当 group_fd 加进同一组 */
```

读取时要按 `nr + 2` 个 u64 解析，并用 `time_enabled/time_running` 判断是否需要缩放：

```c
if (rf->time_enabled != rf->time_running) {
    scaled = (double)value * rf->time_enabled / rf->time_running;   /* 发生多路复用 */
}
```

## 性能与边界

- counting 事件几乎零开销（读一次系统调用）；**采样事件的开销正比于采样率**，99 Hz 是 Brendan Gregg 推荐的"不扰动系统"频率
- 槽位数量由微架构决定；事件多于槽位就必然多路复用 → **估算值不等于真值**
- `exclusive` 在多数系统上会导致事件**永不运行**（NMI watchdog 等也在用计数器）；`pinned` 抢不到槽位返回 0 且进入 error 态
- `read` 缓冲区不足返回 `ENOSPC`；`PERF_FORMAT_GROUP` 与 `inherit` 组合无效；mmap 必须 `1 + 2^n` 页、`data_head` 不回绕

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| `EPERM` / `EACCES` | `perf_event_paranoid` 太严或缺 `CAP_PERFMON` | `sysctl kernel.perf_event_paranoid=1`（需权衡安全）或提权 |
| 读到的值明显偏小 | 多路复用只跑了一部分时间 | 用 `time_enabled/time_running` 缩放，并**同时报告缩放比** |
| 比值算出来离谱 | 组内成员**不在同一组**，被调度到不同时间段 | 用 `group_fd` 建组，别各自 `open` |
| 采样地址总是偏后几条 | skid | 提高 `precise_ip`（需要 PEBS 等硬件支持） |
| 想跨进程看整机 | `pid=-1` 需要更高权限 | 或改用 cgroup 级（`PERF_FLAG_PID_CGROUP`） |
| 循环里比 IPC 却每次都不一样 | 未固定 CPU/频率 | 配合 `taskset` + 关 turbo，或用 `REF_CPU_CYCLES` |

## 参考资料（实际阅读过的权威来源）

- [perf_event_open(2) — man7.org](https://man7.org/linux/man-pages/man2/perf_event_open.2.html) — 系统调用原型与 pid/cpu 语义表、`perf_event_attr` 全字段、HW/SW/HW_CACHE 事件常量、缓存事件编码公式、`PERF_ATTR_SIZE_VER0..5`、多路复用缩放公式与 rdpmc 代码、`precise_ip`/PEBS、`read_format` 与 `ENOSPC`
- [Ftrace — Linux Kernel Documentation](https://docs.kernel.org/trace/ftrace.html) — 与之互补的 `trace_clock` 时钟源（含 `x86-tsc`/`perf`），对照理解时间戳口径
- [BPF Instruction Set Specification](https://docs.kernel.org/bpf/standardization/instruction-set.html) — 现代替代路线（eBPF + PERF_COUNT_SW_BPF_OUTPUT）的指令层依据
