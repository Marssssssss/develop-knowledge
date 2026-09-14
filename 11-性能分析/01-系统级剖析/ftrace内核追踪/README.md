# ftrace 内核追踪

## 简介

**ftrace** 是编译进 Linux 内核的**内置追踪框架**，通过一个专用文件系统 **tracefs** 用"读写文件"的方式控制：`echo function_graph > current_tracer` 打开追踪器，`cat trace` 读结果。它不需要装任何东西，是内核自带的"第一现场取证工具"。

关键概念：

- **tracefs**：`/sys/kernel/tracing`（4.1 起独立挂载点；更早只有 `/sys/kernel/debug/tracing`，且后者仍作为兼容路径存在）
- **tracer（追踪器）**：`function` / `function_graph` / `hwlat` / `wakeup` / `irqsoff` / `branch` / `nop` 等，写 `current_tracer` 切换
- **set_ftrace_filter**：只跟踪指定函数（内核源码级过滤，而不是事后 grep）
- **dynamic ftrace**：函数入口的 `mcount`/`__fentry__` 调用在编译期就是一条 **nop**，启用时才被改写成跳转——**未启用时开销几乎为零**
- **ring buffer**：per-CPU 环形缓冲，`trace` 是静态快照、`trace_pipe` 是消费者流

历史位置：追踪工具演进链是 **传统工具 → Ftrace → perf → eBPF**。ftrace 是内核的第一代内置追踪设施，至今仍是 `hwlat`（硬件延迟）、`wakeup`（唤醒延迟）这类"只能用内核自己测"的场景的首选。

## 原理详解

### 1. tracefs 挂载与文件

```bash
mount -t tracefs nodev /sys/kernel/tracing      # 或写进 /etc/fstab
ln -s /sys/kernel/tracing /tracing              # 可选软链
```

关键控制/输出文件（所有时间值单位**微秒**）：

| 文件 | 作用 |
| --- | --- |
| `current_tracer` | 设置/显示当前 tracer；**改动会清空 ring buffer 与 snapshot buffer** |
| `available_tracers` | 内核已编译进去的 tracer 列表 |
| `tracing_on` | `1`/`0` 开关**向 buffer 写入**（注意：只是停写，追踪本身的执行开销可能仍存在） |
| `trace` | 人类可读**文本**快照，**不是消费者**；以 `O_TRUNC` 打开即清空 buffer |
| `trace_pipe` | 同样的输出但**是消费者**：读完即消费，无新数据时**阻塞** |
| `per_cpu/cpu0/trace_pipe_raw` | **二进制**格式，配合 `splice()` 高效导出 |
| `trace_options` / `options/` | 输出字段与行为开关（每个选项一个文件） |
| `buffer_size_kb` / `buffer_total_size_kb` / `buffer_subbuf_size_kb` | 缓冲区大小 |
| `buffer_percent` | ring buffer 唤醒水位（0 / 50 / 100） |
| `free_buffer` | 进程退出时缩减/释放 ring buffer |
| `tracing_cpumask` | 限定只跟踪哪些 CPU（十六进制掩码） |
| `tracing_max_latency` / `tracing_thresh` | 最大延迟记录 / 延迟 tracer 的记录阈值 |
| `set_ftrace_filter` / `set_ftrace_notrace` | 函数白名单/黑名单 |
| `set_ftrace_pid` / `set_ftrace_notrace_pid` | 按 PID 过滤函数追踪 |
| `set_graph_function` / `set_graph_notrace` | function_graph 的"只看这棵子树" |
| `available_filter_functions` | 可跟踪函数名清单（**也可写行号**，比写名字快） |
| `dyn_ftrace_total_info` | 当前已转成 nop 的函数数量（调试用） |
| `enabled_functions` / `touched_functions` | 已挂回调的函数；标记 `R`(save regs) `I`(ip modify) `D`(BPF trampoline) `O`(ops 在入口上方) `M`(曾被 ip modify/direct call 改过) |
| `function_profile_enabled` → `trace_stat/function<cpu>` | 函数 profiler 直方图 |
| `stack_max_size` / `stack_trace` / `stack_trace_filter` | 栈使用追踪 |
| `trace_clock` | 时间戳时钟（local/global/counter/uptime/perf/x86-tsc/mono/boot/tai…） |
| `trace_marker` / `trace_marker_raw` | 用户态打点，与内核事件对齐时间轴 |
| `kprobe_events` / `uprobe_events` | 动态 tracepoint（kprobe 的上层接口） |
| `snapshot` / `per_cpu/cpu0/snapshot` | 不消费主 buffer 的"旁路快照" |
| `error_log` | 最近 8 条命令错误（环形），`echo > error_log` 清空 |

`per_cpu/cpu0/stats` 给出该 CPU 的 `entries` / `overrun` / `commit overrun` / `bytes` / `oldest event ts` / `now ts` / `dropped events` / `read events` —— **overrun 才是判断"有没有丢事件"的权威字段**。

### 2. 可用 tracer

| Tracer | 说明 |
| --- | --- |
| `function` | 跟踪所有内核函数调用 |
| `function_graph` | 入口与出口都探测，输出带缩进的**调用图 + 每函数耗时** |
| `blk` | 块层 tracer（blktrace 应用在用） |
| `hwlat` | 硬件延迟检测（`hwlat_detector/width` 与 `/window` 控制关中断测试的时长与周期） |
| `irqsoff` / `preemptoff` / `preemptirqsoff` | 记录关中断 / 关抢占 / 两者的**最大延迟** |
| `wakeup` / `wakeup_rt` / `wakeup_dl` | 记录最高优先级任务 / RT 任务 / SCHED_DEADLINE 任务被唤醒后的最大延迟 |
| `mmiotrace` | 跟踪二进制模块对硬件的读写 |
| `branch` | 跟踪 likely/unlikely 的**分支预测正确性** |
| `nop` | 什么都不跟踪；`echo nop > current_tracer` 用来**关掉所有 tracer** |

### 3. function_graph 输出格式与 overhead 标记

```text
# tracer: function_graph
#
# CPU  DURATION                  FUNCTION CALLS
# |     |   |                     |   |   |   |

 0)               |  sys_open() {
 0)               |    do_sys_open() {
 0)               |      getname() {
 0)               |        kmem_cache_alloc() {
 0)   1.382 us    |          __might_sleep();
 0)   2.478 us    |        }
```

- **入口行**以 ` {` 结尾、**出口行**是单独的 `}`；出口行上的 duration 就是**该函数的 inclusive 耗时**
- 叶子函数只有一行 `func();` 带耗时——它的 inclusive == self
- `funcgraph-cpu` / `nofuncgraph-cpu`、`funcgraph-duration` / `nofuncgraph-duration`、`funcgraph-overhead` / `nofuncgraph-overhead` 是 per-tracer 选项（`overhead` 依赖 `duration`）
- **overhead 标记**（出现在 duration 列）：

| 标记 | 阈值 |
| --- | --- |
| `$` | > 1 s |
| `@` | > 100 ms |
| `*` | > 10 ms |
| `#` | > 1000 µs |
| `!` | > 100 µs |
| `+` | > 10 µs |
| 空格 | ≤ 10 µs |

### 4. trace 与 trace_pipe 的语义差别（踩坑高发点）

| | `trace` | `trace_pipe` |
| --- | --- | --- |
| 是否消费者 | **否**（静态快照） | **是**（读走即消费） |
| 追踪中读取 | 可能读到**不一致**的内容（内核尝试读整个 buffer 而不消费） | 流式、一致 |
| 无新数据时 | 立即返回当前内容 | **阻塞**等待 |
| 清空方式 | `echo > trace`（O_TRUNC） | 消费即清 |
| 二进制版 | 无 | `trace_pipe_raw` + `splice()` |

### 5. dynamic ftrace 与函数 profiler

- 编译时每个可跟踪函数入口都埋一条 **nop**（原 `mcount` / `__fentry__` 调用点）。**未启用追踪时它真的只是一条 nop**，所以开销"几乎为零"
- 启用时内核做 **text patching**：把 nop 改写成跳转到 trampoline 的调用；`set_ftrace_filter` 命中哪些函数、就只改写哪些函数的调用点
- `dyn_ftrace_total_info` 报告"当前已转为 nop 的函数数量"；`enabled_functions` 里的 `M` 标记表示该调用点曾被 ip-modify/direct-call 改过且**永不清除**
- `set_ftrace_filter` 支持写**行号**（对应 `available_filter_functions` 里的位置），大批量设置时比写函数名快得多
- `function_profile_enabled=1` 打开**函数直方图**（调用次数 + 耗时），结果读 `trace_stat/function<cpu>`；`function-fork` 选项让 `set_ftrace_pid` 里任务 fork 出的子进程自动纳入跟踪

## 对比 / 选型

| 维度 | ftrace | perf | eBPF |
| --- | --- | --- | --- |
| 安装 | 内核自带，零依赖 | 需 perf 工具 | 需内核 4.x+ 与 BCC/libbpf |
| 编程模型 | 读写文件 | 命令行/API | C/脚本，可编程逻辑 |
| 聚合能力 | 弱（原始事件 + 函数直方图） | 中（有统计） | **强**（内核态聚合进 map） |
| 开销 | 低（nop 补丁） | 低-中 | 低（JIT） |
| 典型场景 | 内核路径调用图、hwlat 硬件延迟、唤醒延迟 | CPU 采样剖析、PMC | 生产可观测性、自定义指标 |

## 环境准备

- 操作系统：Linux（tracefs 需内核带 `CONFIG_TRACING`；`function_graph` 需 `CONFIG_FUNCTION_GRAPH_TRACER`）
- 语言：Python 3.8+ / Go 1.21+ / C（GCC）
- 权限：写 tracefs 需 root；**本 demo 的 Python 与 Go 部分离线可跑**

## 运行方式

```bash
gcc -O2 -Wall -Wextra -pedantic ftrace_control.c -o ftrace_control && ./ftrace_control
#   ↑ C:写 tracefs 需 root,否则打印应执行的命令;可传 'do_sys_open' 自定义 set_graph_function 目标
python3 funcgraph_parser.py            # Python:解析样例输出的调用树 + self/inclusive 耗时
python3 funcgraph_parser.py trace.txt  # Python:解析真实 trace 文件
go run .          # Go:dynamic ftrace 补丁模型 + 过滤器优先级 + 函数 profiler 直方图
```

## 关键代码片段

Python 侧的解析器用**花括号配对**而不是缩进宽度来还原调用树——因为缩进宽度会随内核版本变化，`{` / `}` 是格式契约：

```python
if body.endswith("{"):                    # 入口行
    stack.append(Node(name, dur=None, depth=len(stack)))
elif body.strip() == "}":                 # 出口行:这里才有 inclusive 耗时
    node = stack.pop()
    node.inclusive = dur
    if stack:
        stack[-1].children.append(node)
```

`self time` 由 inclusive 减去子节点 inclusive 得到，这正是火焰图里"平台宽度"的来源：

```python
def self_time(node):
    return node.inclusive - sum(c.inclusive or 0.0 for c in node.children)
```

## 性能与边界

- **未启用时零开销**：函数入口是 nop，只有 text patching 之后才产生调用
- 缓冲区是 per-CPU 的，`per_cpu/cpuN/stats` 的 `overrun` 非 0 说明**丢事件**，应调大 `buffer_size_kb` 或收窄过滤条件
- `function_graph` 会产生海量数据（每个函数进出各一条），**必须**配合 `set_graph_function` / `set_graph_notrace` 收窄
- `current_tracer` 写入会**清空 buffer 与 snapshot**，排查现场前不要随手改
- `trace` 在追踪开启时读取可能不一致；要一致用 `trace_pipe` 或先 `tracing_on=0` 再读 `trace`
- `error_log` 只保留最近 **8** 条错误，复杂 filter 命令失败时优先看它

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 找不到 `/sys/kernel/tracing` | 内核未挂 tracefs 或版本 < 4.1 | `mount -t tracefs nodev /sys/kernel/tracing`，或读 `/sys/kernel/debug/tracing` |
| 设了 filter 还是不生效 | filter 与 notrace **同时命中时不跟踪** | 先 `cat set_ftrace_notrace` 确认 |
| `cat trace_pipe` 卡住不动 | 它是**消费者**，无新数据时阻塞（设计如此） | 用 `timeout` / `head -n N` 包一层 |
| 事件丢了却没报错 | 看着 `trace` 有条目，其实中间 overrun | 检查 `per_cpu/cpuN/stats` 的 `overrun` |
| 改了 `current_tracer` 现场没了 | 写入会清空 buffer 与 snapshot | 先 `snapshot` 保存，或先读走 `trace` |
| 命令没报错但没生效 | 失败信息只在 `error_log`（环形，仅 8 条） | `cat error_log` |
| `hwlat` 测出的延迟很奇怪 | 它测的是**关中断窗口**内的停顿 | 看 `hwlat_detector/width` 与 `/window` 设置是否合理 |

## 参考资料（实际阅读过的权威来源）

- [Ftrace — Linux Kernel Documentation（docs.kernel.org/trace/ftrace.html）](https://docs.kernel.org/trace/ftrace.html) — tracefs 挂载与全部控制文件、13 种 tracer、function_graph 格式与 overhead 标记阈值、trace vs trace_pipe 语义、`set_ftrace_filter`/`set_graph_function`、函数 profiler 与 `trace_stat`、`function-fork`、per-CPU 缓冲与 `stats`、`error_log`
- [Kernel Probes (Kprobes) — Linux Kernel Documentation](https://docs.kernel.org/trace/kprobes.html) — `kprobe_events` 背后的打补丁机制（ftrace 的动态事件层）
- [BPF Instruction Set Specification](https://docs.kernel.org/bpf/standardization/instruction-set.html) — 对比"eBPF trampoline"路线时读到的调用编码
