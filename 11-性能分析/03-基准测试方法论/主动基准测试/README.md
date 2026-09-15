# 主动基准测试（Active Benchmarking）

## 简介

被动基准测试是"跑完看数字"；主动基准测试是"**在它还在跑的时候**，用其它工具分析整条链路，找出真正的限流因子"。Gregg 的原话把这两种做法的差别总结成一个方法论：

> **主动基准测试的两个动作**：
> 1. 尽可能让基准长时间跑在稳态（例如数小时）；
> 2. 在它运行期间用其它工具分析所有相关组件，找出真正的 limiter。

以及一句精确的失败模式描述：

> *casual benchmarking: you benchmark A, but actually measure B, and conclude you've measured C.*
> （随意基准测试：你基准了 A，实际测的是 B，却得出结论说测到了 C）

关键概念：

- **Data is not Information**：只有基准结果的柱状图、没有任何支撑性技术证据，几乎必然有问题（overlooked problems）。
- **限流因子（limiter）**：主动基准测试的产出必须是"为什么是 X，而不是 2X"。
- **problem checklist（7 条）**：Gregg 总结的常见陷阱清单，本 demo 把其中 6 条做成可程序化判定。
- **统计在 active 之后**：`iostat first, R later`——先拿有效数据，再做统计分析。

## 原理详解

### 1. 被动 vs 主动

| | 被动基准测试 | 主动基准测试 |
| --- | --- | --- |
| 何时分析 | 跑完之后只看结果 | **运行中**持续分析 |
| 使用的工具 | 只有基准工具本身 | vmstat / iostat / mpstat / sar / top / pidstat / tcpdump / perf / eBPF / strace… |
| 产出 | 一张对比柱状图 | 结果 + 限流因子 + 证据（截图/日志） |
| 常见结局 | "错误或误导性的结论几乎每次都是" | 能确认基准是否真的测了目标，或找出系统真实瓶颈 |
| 对错误的抵抗力 | 低 | 高（错误在运行中就被看见） |

Gregg 给了一个很好用的检验方法：**别人是否做了主动基准测试？**

1. 他们在基准运行时用过其它工具吗？能提供截图吗？
2. 他们能解释"为什么结果是 X，而不是 2X"（限流因子是什么）吗？

无法回答第 2 条的，结论默认不可信。

### 2. 7 条 problem checklist 与可计算化判定

| # | 原文条目 | 可计算的证据与判定 |
| --- | --- | --- |
| 1 | 被其它系统事件（含邻居）扰动 | 其它进程占用整机算力 ≥ 30% |
| 2 | 被软件资源控制限流 | 利用率 ≥ cgroup 配额的 95%（利用率被"钉住"在配额上） |
| 3 | 被客户端-服务端之间的网络限流 | 客户端吞吐 ≥ 链路上限的 95% **且** 服务端 CPU < 50% |
| 4 | 基准软件本身单线程 | 基准线程数 = 1，机器核数 > 1，进程占用 ≈ 1 核 |
| 5 | 对比时测了不同版本的客户端/服务端软件 | 需要版本清单（人工） |
| 6 | 测的是磁盘 I/O 而不是文件系统 I/O | `(rchar - read_bytes) / rchar ≥ 50%`（即读几乎全被 page cache 满足） |
| 7 | 应用了不现实的负载 | 需要人工判断（无法从运行时数据判定） |

第 6 条特别值得展开：`/proc/<pid>/io` 的 `rchar` 是**文件系统层**已读字节（含 page cache），`read_bytes` 是**块设备层**真实 I/O。两者差距巨大时，"我测了磁盘性能"这个结论是错的——你测的是内存。

第 4 条则是"基准软件自己成了瓶颈"：单线程跑满 1 核、机器还有 7 个空闲核，此时无论换什么服务器/存储，结果都不会变——因为限流因子在被测程序自己身上。

### 3. 统计只应在主动基准测试之后

Gregg 对这一点的措辞很强（原文）：

- 如果基准结果本身就是错的，**统计分析只会让错误显得更可信**；"a sound statistical method can make benchmark results seem trustworthy, when in fact, they are false"。
- 错误数据下唯一"好"的结局是：**统计分析判定它不可信**（例如 CoV 过高），于是分析转向"基准本身出了什么问题"。
- 但实践中这很少发生——"often, the wrong target has been benchmarked, but the results are statistically sound"（基准错了，但统计上完全成立）。

所以本 demo 把"CoV 过高"直接升级为**门禁**：`重复测量 CoV > 2%` 时，不问"哪个更快"，只回答"结果不可信，先降噪"。

### 4. 工具侧：/proc 采样器

主动基准测试需要"运行中的多口径证据"。`c/proc_sampler.c` 用一个纯文件接口实现最小采样器（无需 root、无需安装任何工具）：

```text
time_s  proc_cores  other_cpu_pct  threads  cpu_mhz  proc_fs_MB  proc_disk_MB
```

| 字段 | 来源 | 支撑的判定 |
| --- | --- | --- |
| `proc_cores` | `/proc/<pid>/stat` 的 utime+stime 增量 / 墙钟 | 第 2、4 条 |
| `other_cpu_pct` | `/proc/stat` 整机忙碌比例 − 本进程折算比例 | 第 1 条 |
| `threads` | `/proc/<pid>/status` | 第 4 条 |
| `cpu_mhz` | `/proc/cpuinfo` | 热降频 |
| `proc_fs_MB` / `proc_disk_MB` | `/proc/<pid>/io` 的 `rchar` / `read_bytes` | 第 6 条 |

有两处解析细节容易写错，都写在注释里：`/proc/<pid>/stat` 的 `comm` 字段**可能含空格与右括号**（必须用最后一个 `)` 定位后续字段），以及 `utime` 在 `)` 之后是第 12 个 token（`state` 是第 1 个）。

`python/active_benchmarking_check.py` 消费这些字段（也可从 pidstat/iostat 手工喂），输出命中项、证据与限流因子结论。

## 对比 / 选型

| 做法 | 能回答的问题 | 不能回答 |
| --- | --- | --- |
| 只看结果数字 | "谁快"（可能答错） | 为什么快、是否测错了目标 |
| 统计后处理（见 `../benchstat统计显著比较/`） | 差异是否显著、是否稳定 | 差异由什么机制造成 |
| **主动基准测试** | 限流因子、是否测错目标 | 统计上是否为噪声（需要配合统计） |
| 系统级剖析（见 `../../01-系统级剖析/`） | 慢在哪 | 结论是否可信 |

结论：**主动基准测试与统计分析是互补品**，顺序是先 active 后 statistical。

## 环境准备

- 操作系统：C 采样器需要 Linux（依赖 `/proc`、`/sys/fs/cgroup`）；Python/Go 判定器跨平台
- Python：3.10+；Go：1.21+；C：任意 C99 编译器
- 依赖：无

## 运行方式

### C（采样器，Linux）

```bash
cd c && gcc -O2 -Wall -Wextra -pedantic -o proc_sampler proc_sampler.c
./proc_sampler <被测进程 pid> 10 1        # 10 轮、每轮 1 秒
```

### Python

```bash
python3 python/active_benchmarking_check.py
```

### Go

```bash
cd go && go run active_benchmarking_check.go
```

## 关键代码片段

```python
# 判定器核心: 每条 check 返回 (是否命中, 证据文本), 便于把"结论"与"证据"一起展示
def check_wrong_target(ev):
    fs, disk = ev.get("fs_bytes_read", 0.0), ev.get("disk_bytes_read", 0.0)
    ratio = (fs - disk) / fs
    fired = ratio >= 0.50          # 一半以上由 page cache 满足 -> 没测到磁盘
    return fired, f"文件系统读 {fs:.3g} B, 磁盘读 {disk:.3g} B -> {ratio:.0%} 由 page cache 满足"

# CoV 门禁: 抖动过大时唯一正确的结论是"结果不可信"(Gregg: 错误数据下唯一好的结局)
def check_untrustworthy(ev, limit=0.02):
    cv = _cov(ev["repeat_results"])
    return cv > limit, f"重复测量 CoV={cv:.2%}(阈值 {limit:.0%})"
```

## 性能与边界

- **采样成本**：1 秒间隔、读取 5 个 `/proc` 文件的开销可忽略；但间隔越短越容易与基准本身抢 CPU（这也是"测量扰动"的一部分）。
- **稳态窗口**：Gregg 建议基准跑数小时，至少要让采样覆盖"预热之后到跑完之前"的稳态区间。
- **cgroup 只有 v2 路径**：采样器按 `/sys/fs/cgroup<cgroup path>/cpu.max` 解析配额；cgroup v1 需要 `/sys/fs/cgroup/cpu/.../cpu.cfs_quota_us`，本 demo 未实现。
- **不可计算项**：7 条清单里"不同版本对比"与"不现实的负载"必须人工确认，任何脚本都无法从运行时数据判定。
- **`other_cpu_pct` 是粗估**：用"整机忙碌比例 − 本进程折算比例"得到，只用于识别"明显有别的进程在抢 CPU"；精确定位需要 `pidstat`/`perf`。

## 注意事项与常见坑

- **只在容器里看 `loadavg` 会误导**：宿主机负载、cgroup 配额、邻居容器都会影响结果，而 `loadavg` 无法归因到容器（详见 `../../01-系统级剖析/LoadAverage与PSI/`）。
- **先确认基准在测什么，再解释数字**：Gregg 指出最常见的情形就是"基准并没有真的测试它声称要测的东西"，此时结果往往仍有价值，只是必须重新解读。
- **别把"单线程基准"的结论外推到多核场景**：单线程跑满时，换更多核/更快磁盘都不会改变结果，结论对多线程场景零参考价值。
- **"测磁盘 I/O"要先看 page cache**：同一份读负载在冷缓存与热缓存下可以差几个数量级；不区分 `rchar` 与 `read_bytes` 就无法解释差异（对齐 `../../01-系统级剖析/页缺失与内存剖析/`）。
- **模拟器/共享 VM 上的数字不可比**：Android 官方明确不建议在模拟器上跑基准（结果取决于宿主机），Dropbox 也遇到"CI 的 VM 资源被共享"导致变异过大——这类环境需要先降噪，再谈回归检测（见 `../CI性能回归门禁/`）。
- **CoV 高时的正确反应**：不是"多跑几次取最好值"，而是"关掉干扰源、固定环境、重跑"。统计上"取最小值"并没有理论依据（干扰只会让结果变慢，不会变快——这点反而是取 min 的辩护理由，但需要单独论证）。

## 参考资料（实际阅读过的权威来源）

- [Active Benchmarking — Brendan Gregg](https://www.brendangregg.com/activebenchmarking.html) — 主动/被动基准测试的定义与两个动作、"benchmark A / measure B / conclude C"的失败模式、"Data is not Information"、7 条 problem checklist 全文、"别人是否做了主动基准测试"的两条检验、统计必须在 active 之后（`iostat first, R later`）与"错误数据下唯一好结局是统计判定其不可信"、Bonnie++ 工作示例、Surge 2013《Benchmarking Gone Wrong》演讲
- [Benchmark in Continuous Integration — Android Developers](https://developer.android.com/studio/profile/benchmarking-in-ci) — 基准结果是"模糊的"、必须在真机而非模拟器上跑、微基准需要锁频（`lockClocks`），用于对齐"环境本身就可能是否定结论的陷阱"
- [Keeping sync fast with automated performance regression detection — Dropbox Tech Blog](https://dropbox.tech/infrastructure/keeping-sync-fast-with-automated-performance-regression-detectio) — 共享 VM/跨平台/磁盘与网络差异造成的测量变异，"先证明同码重复跑一致再谈检测"的实践
- [Detecting Benchmark Regression — Joe Gregorio（Skia Perf）](https://bitworking.org/news/2014/11/detecting-benchmark-regressions/) — "单条基准测量几乎无意义"（机器过热降频、其它进程抢 CPU 都会污染单点），与主动基准测试"必须在运行中观察"互为印证
