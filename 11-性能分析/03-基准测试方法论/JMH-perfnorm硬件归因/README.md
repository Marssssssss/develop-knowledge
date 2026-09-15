# JMH -prof perfnorm 硬件归因

## 简介

`perfnorm` 是 JMH 内置的 **Linux 外部 profiler** 之一（实现类 `LinuxPerfNormProfiler`），描述是 *"Linux perf statistics, normalized by operation count"*。它把 `perf stat -I <intervalMs>` 采集到的**原始硬件事件计数**换算成**每个 `@Benchmark` 操作**的量——因为 `perf` 原始输出里"跑了 12 亿个 cycle"这种数字无法与"一次调用多贵"对齐。

关键是它的定位：**perfnorm 是非主指标**。JMH 作者在 jmh-dev 邮件列表里明确说过 time 才是主指标，perfnorm 提供的是"把时间差异归因到微架构机制"的证据。

关键概念：

- **逐操作归一化**：`事件吞吐 / 操作吞吐`，操作吞吐 = `1000 * ops / timeMs`（ops 与 timeMs 来自 JMH 的 measurement 阶段元数据）。
- **CPI / IPC**：由 `cycles/instructions` 派生，单位分别是 `clks/insn` 与 `insns/clk`。
- **事件探测**：源码里有一张 22 个事件的"非穷举但我们关心"表，逐个 `perf stat --event X echo 1` 试探，不支持的静默丢弃。
- **采样而非精确计数**：PMU 计数是采样外推的估计值，绝对精度**低于**对多次采样求时间的做法（实测差值约 21.5 倍）。
- **归因**：`stalled-cycles-frontend/backend`、`L1-dcache-*`、`branch-misses`、`LLC-*` 用来判定"受限在前端 / 后端 / 数据缓存 / 分支预测"。

历史背景：JMH 从 1.13 起提供 Linux perf 系列 profiler（`perf` / `perfnorm` / `perfasm` / `perfc2c`），用于把"纳秒级微基准"与"微架构事件"对齐；`perfnorm` 是其中唯一做**逐操作归一化**的一个。

## 原理详解

### 1. 事件表与探测

```text
interestingEvents(22) = cycles, instructions,
  branches, branch-misses,
  L1-dcache-loads/-load-misses/-stores/-store-misses,
  L1-icache-loads/-load-misses,
  LLC-loads/-load-misses/-stores/-store-misses,
  dTLB-loads/-load-misses/-stores/-store-misses,
  iTLB-loads/-load-misses,
  stalled-cycles-frontend, stalled-cycles-backend
```

构造阶段逐事件执行 `perf stat --log-fd 2 --field-separator , --event <ev> echo 1`：若输出里出现"不支持该事件"的标记就丢弃该事件；`--events` 可覆盖候选表，`-prof perfnorm:useDefaultStat=true` 则改用 `perf stat -d -d -d`。此外会先用一次 `echo 1` 探测本机 perf 是否支持 `-I`（增量模式），不支持直接抛 `perf is too old, needs incremental mode (-I)`。

### 2. 增量输出的解析

`perf stat -I` 每次间隔输出一行，需要兼容两种列数：

| perf 版本 | 列布局 | 取值 |
| --- | --- | --- |
| 3.13 | `time,count,event` | 3 列，事件在 index 2 |
| > 3.13 | `time,count,<其他>,event,<其他>` | ≥4 列，事件在 index **3** |

跳过 `#` 开头的注释行；时间/计数解析失败的行直接忽略。

### 3. 时间窗裁剪

```text
delayMs  = 未显式指定时由 ProfilerUtils.measurementDelayMs(benchmarkResult) 自动测量
lengthMs = 未显式指定时由 ProfilerUtils.measuredTimeMs(benchmarkResult) 自动测量
readFrom = delayMs / 1000
readTo   = (delayMs + lengthMs + interval) / 1000
```
窗口外的样本丢弃：这样**预热阶段（JIT 编译、缓存冷启动）不会被算进测量窗口**。

### 4. 头尾过滤与"首样本跳过"

- `filter=true`（默认）时丢弃该事件序列**最后 2 个**样本：尾部既有基础设施 ramp-down，也有 profiler 自身收尾输出，有时会表现为两个独立样本。
- 求和时**第 1 个样本不计入**——它的时间区间实际不包含它自己。这是一条极易被漏掉、但会造成系统性高估的细节。

### 5. 归一化与派生

```text
事件吞吐 = Σ(样本计数, 跳过首样本) / (maxTime - minTime)
操作吞吐 = 1000 * measurementOps / measurementTimeMs
每操作计数 = 事件吞吐 / 操作吞吐          -> 单位 "#/op"
CPI = cycles / instructions              -> "clks/insn"
IPC = instructions / cycles              -> "insns/clk"
```

`CPI/IPC` 只有在 `cycles` 与 `instructions` 都可用时才输出；缺 `cycles` 时回退 `cycles:u`，缺 `instructions` 时回退 `instructions:u`。收尾的 `getMetadata()/timeMs/ops` 任一为 0 时返回空结果（`N/A`）而不是除零。

### 6. 归因（本 demo 的扩展层）

perfnorm 只给数字，不给结论。把数字变成机制需要一个判定层（本 demo 实现，阈值为经验值）：

| 证据 | 判定 |
| --- | --- |
| `stalled-cycles-frontend / cycles ≥ 0.30` | 前端受限（取指/译码/分支预测） |
| `stalled-cycles-backend / cycles ≥ 0.30` | 后端受限（执行端口/依赖链） |
| `L1-dcache-load-misses / L1-dcache-loads ≥ 0.10` | 数据缓存受限（L1/LLC 未命中） |
| `branch-misses / branches ≥ 0.02` | 分支误预测受限 |
| 以上都不满足 | 未见明显微架构受限：回到墙钟口径与系统级证据 |

判定必须输出"**为什么是 X 而不是 2X**"式的限流因子，这与主动基准测试的要求一致（见 `../主动基准测试/`）。

## 对比 / 选型

| Profiler | 平台 | 输出 | 用途 |
| --- | --- | --- | --- |
| `perf` | Linux | IPC / CPI 基础统计 | 一眼看执行效率 |
| **`perfnorm`** | Linux | **逐操作归一化事件** | 把时间差异归因到微架构机制 |
| `perfasm` | Linux | 热区 + 汇编 + 事件 | 定位到具体指令 |
| `perfc2c` | Linux | cache-to-cache 传递 | 伪共享/跨核缓存分析 |
| `async` | 多平台 | 采样火焰图 / JFR | 跨平台 CPU/分配/锁 |
| `xctracenorm` | macOS | 归一化计数器事件 | Apple 平台对应物 |

## 环境准备

- 操作系统：Linux（`perf` 可用，`perf_event_paranoid` 允许用户态采集）
- Python：3.10+；Go：1.21+
- 依赖：无（本 demo 是纯数据流水线复现，不真正调用 `perf`，因此可在任意平台运行自检）

## 运行方式

### Python

```bash
python3 python/perfnorm_report.py
```

### Go

```bash
cd go && go run perfnorm_report.go
```

## 关键代码片段

```python
# 「原理详解」第 3/4/5/6 步: 窗口裁剪 -> 丢尾 2 -> 跳首样本 -> 归一化
read_from, read_to = delay_ms / 1000.0, (delay_ms + length_ms + interval_ms) / 1000.0
for ts, ev, count in records:
    if ts < read_from or ts > read_to:      # 窗外丢弃
        continue
    by_event.setdefault(ev, []).append((ts, count))

for ev, series in by_event.items():
    series = sorted(series)
    kept = series[: len(series) - 2]        # 丢最后 2 个样本
    s = 0.0
    for i, (ts, v) in enumerate(kept):
        if i != 0:                          # 第 1 个样本的时间区间不含它自己
            s += v
    throughputs[ev] = s / (max_t - min_t)   # 事件吞吐

ops_throughput = 1000.0 * ops / time_ms     # 操作吞吐
per_op = {ev: thr / ops_throughput for ev, thr in throughputs.items()}
```

## 性能与边界

- **采样口径的精度上限**：JMH 作者给出的实测对照是同一段 `Hello` 基准同时给两种口径：

  | 口径 | 分数 | 绝对误差 | 相对误差 |
  | --- | --- | --- | --- |
  | 时间 | 0.252 ns/op | ±0.002 | 0.79% |
  | cycles（perfnorm） | 1.073 #/op | ±0.043 | 4.01% |

  分数只差 **4.26 倍**（同一次测量），但绝对误差差 **21.5 倍**——因为 perf 的 `cycles` 本身是**采样估计**（精度与采样频率成正比），而时间是对多次采样求得的。这就是 perfnorm 只能作"非主指标"的量化理由。
- **线程迁移与共核**：线程在核间迁移会让 cycles 差分失去归属；线程数多于核数时，同一核的 PMC 差分必须在多个线程间分摊。"同步的逐线程时间戳"比外部 PMU 计数更可靠。
- **`--interval` 的取舍**：默认 `interval=100` ms，越小越准但 profiler 自身开销越大。
- **判别力受 IPC 上限约束**：`IPC > 2` 说明发射宽度没被打满，此时"前端/分支"解释优先于"后端端口"。

## 注意事项与常见坑

- **千分位分组符会造成列错位**：`--field-separator ,` 与本地化的千分位分隔符冲突时，`1234` 会被打印成 `1,234`，于是行多切出一列——事件名仍在 index 3，但计数被截断成 `1`。自检里显式复现了这个行为（`"0.1,1,234,cycles"` → count=1）。解析异常行必须能被识别，否则会静默产出错数。
- **只丢尾部不丢头部是不够的**：头 1 个样本由"求和时跳过首样本"处理，尾 2 个由 `filter` 处理，两者是**两个独立机制**，自检显示漏掉任一都会让 `cycles/op` 高估 30%~37.5%。
- **perfnorm 的分数不能和时间的分数混着比较**：`#/op` 的单位是"每操作事件数"，`ns/op` 是时间；只有在知道频率时 `cycles/op / ns/op ≈ GHz` 才有意义。
- **极小的逐操作计数要按近似零看**：示例输出里 `LLC-stores` 显示 `≈ 10⁻⁴`，把它当成"确实有 1e-4 次/操作"会导出错误结论（本 demo 用 `< 1e-3` 作为近似零阈值）。
- **权限与环境**：`perf` 需要足够的 `perf_event_paranoid` 权限；同时"perfnorm 支持哪些事件"取决于 CPU 型号与内核版本，换机器后事件集合会变（所以探测是必需的，不能硬编码）。
- **不要用它做跨机器对比**：事件集合、采样频率、频率策略都不同，跨机器比较 `#/op` 没有意义。

## 参考资料（实际阅读过的权威来源）

- [openjdk/jmh — LinuxPerfNormProfiler.java 源码](https://raw.githubusercontent.com/openjdk/jmh/master/jmh-core/src/main/java/org/openjdk/jmh/profile/LinuxPerfNormProfiler.java) — 22 个候选事件表、探测逻辑与 `perf is too old, needs incremental mode (-I)` 检查、两种列布局的解析分支、`delay/length` 自动测量、最后 2 个样本的过滤与"首样本不计入求和"的理由注释、`s/(maxTime-minTime)` 与 `1000*ops/timeMs` 的归一化、CPI/IPC 的 `:u` 回退、`filter`/`interval`/`useDefaultStat` 选项
- [DeepWiki — JMH External Profiling Integration](https://deepwiki.com/openjdk/jmh/6.2-external-profiling-integration) — profiler 注册与发现（ServiceLoader）、`perf`/`perfnorm`/`perfasm`/`perfc2c`/`async` 的平台与用途对照、`LinuxPerfNormProfiler` "auto-detects available hardware counters / normalizes metrics per benchmark operation" 的定位
- [jmh-dev 邮件列表 — "Any option to use something other than time to measure benchmarks?"（Aleksey Shipilëv 回复）](https://mail.openjdk.java.net/pipermail/jmh-dev/2016-July/002282.html) — perfnorm 是"逐操作归一化"的非主指标；`cycles` 为采样估计导致绝对误差更大；`0.252±0.002 ns/op` vs `1.073±0.043 #/op` 的 4.26×/21.5× 对照；线程迁移与多线程共核使 PMC 差分不可归属；"把基准扔给未准备的硬件不可能产出有意义数据"
- [JMH LinuxPerfNormProfiler 实测输出对照（`while(run){}` vs `Thread.onSpinWait()`）](https://example-a.com/answer/73263359) — 同一时间预算（~36.4 K cycles/op）下 `instructions` 82 K vs 17 K、`IPC` 2.25 vs 0.467，且 `branches`/`L1-dcache-loads` 随迭代次数下降；机制解释为 x86 的 `PAUSE` 指令延迟下一次执行
