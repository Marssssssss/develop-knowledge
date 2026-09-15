# 基准测试方法论

> "跑一次 `time` 就当基准"是性能结论失真的头号来源。本子领域研究**怎么测才可信**：先消除被测系统自身优化带来的假象（微基准陷阱），再让测量结果具备统计意义（多次运行 + 分布 + 离群点），最后用"主动基准测试"确认你测的确实是目标行为。与 [01-系统级剖析/](../01-系统级剖析/) 的分工：那边回答"慢在哪"，这里回答"**这个快慢结论可信吗**"。

## 核心研究主题

- **微基准陷阱**：死代码消除（DCE）、常量折叠、循环优化、预热不足、单 fork、把分配当逻辑测
- **统计显著性**：多次运行的分布、标准差 / 置信区间、离群点识别与剔除、最少运行次数
- **受控实验**：warmup、`--prepare` 重置状态、参数扫描（scaling）、对照组与标签
- **主动基准测试（Active Benchmarking）**：先理解被测系统在做什么，再解释数据，而不是只收集数据
- **工具分工**：`hyperfine`（命令行）、JMH（JVM）、`go test -bench`（Go）、Google Benchmark（C++）、`perf stat`（硬件口径校验）

## 原理详解

### 1. 微基准的第一类杀手：被测代码根本没跑

这是最隐蔽也最常见的一类错误——测量结果看起来"极快"，其实编译器把被测逻辑整段删掉了。

**死代码消除（Dead Code Elimination）**：JIT/AOT 编译器会移除"结果从未被使用"的计算。官方 JMH 示例 `JMHSample_08_DeadCode` 与 Oracle 的 JMH 文章中给出的反例正是一行 `Math.log(x);`——返回值被丢弃，整个调用可被消除：

```java
// BAD —— 返回值被丢弃,编译器可以整段删除
@Benchmark public void measureWrong() { Math.log(x); }
// GOOD —— 返回值被 JMH 内部 Blackhole 消费,无法删除
@Benchmark public double measureRight() { return Math.log(x); }
```

**常量折叠（Constant Folding）**：输入在编译期已知时，整个表达式可被折叠成常量。关键在于**不要让输入是常量**——非 `final` 的 `@State` 字段可以阻止折叠，所以 IDE 建议"把字段改成 final"在这里是个陷阱：

```java
private final double wrongX = Math.PI;   // 编译器知道它恒为 PI → 结果被预计算
private double x = Math.PI;              // 非 final → 每次真的算
```

**循环优化**：不要在基准方法里手写循环再除以迭代次数——JVM 会展开循环、流水化、把循环不变量外提，使得"每操作成本"远低于真实值。迭代交由基准框架控制（JMH 的 `@Benchmark`、hyperfine 的外层多次运行）。

> 三个反例的共同点：**优化器比你更了解这段代码"没有副作用"**。防御手段就是把结果真的用掉（返回值 / `Blackhole.consume()`）、把输入变成运行时才知道的值、把迭代交给框架。

### 2. 第二类杀手：测的不是稳态

- **预热（warmup）**：JIT 编译、内联缓存、文件系统 page cache 都要时间才达到稳态。没有预热时"第 1 次运行显著更慢"，hyperfine 会直接给出警告并建议加 `--warmup`。JMH 侧的经验值是**至少 5 次、更稳的是 10 次**预热迭代。
- **单 fork 的 JIT 污染**：同一个 JVM 里跑多个基准，前一个基准的 profiling 会影响后一个的 JIT 决策。JMH 的 `@Fork(N)` 通过**独立 JVM 进程**隔离；"可发布"的数字至少 `@Fork(3)`，`@Fork(1)` 只用于开发期快速检查。
- **状态未重置**：可变的共享状态会让结果依赖执行顺序。用 `@Setup(Level.Invocation)` / hyperfine `--prepare` 在每次调用前把状态复位（清缓存、删临时产物、重启服务）。
- **把分配当逻辑测**：在 `@Benchmark` 里 `new` 出来的对象，测到的是分配开销而不是逻辑；输入应在 `@Setup` 里造好。

### 3. 第三类杀手：环境与统计

- **测量环境**：笔记本开 Turbo Boost、别的进程抢 CPU、不同时段系统负载不同，都会污染结果。要发布数字就固定机器 / 用专用 CI 节点，并关掉频率提升。
- **shell 自身开销**：`hyperfine` 会先跑若干次空命令测出 shell 启动开销再从结果里扣除；benchmark 极快的命令（< 5 ms）应加 `-N` 跳过 shell。
- **样本量与分布**：`hyperfine` 默认至少 10 次运行，并按命令耗时**动态调整运行次数**（慢命令少跑、快命令多跑）以兼顾统计意义与时间预算；非常快但抖动大的命令可手动 `--min-runs 100`。
- **看标准差而不是只看均值**：`σ` 超过均值的约 10% 就说明环境噪声大或该命令本身不稳定，此时"谁快"的结论不可靠。`--runs 5` 适合很慢的构建类命令（样本少但每次信息量大），微基准则相反。
- **离群点**：`hyperfine` 会自动识别并告警"某次运行明显更慢，来源不一致"——通常是别的进程抢了 CPU。看到告警的正确反应是关闭干扰源重跑，而不是直接采信被污染的结果。

### 4. 主动基准测试（Active Benchmarking）

"被动基准测试"是跑一个别人给的 benchmark 然后照抄数字；"**主动**"则要求你在采信结果之前先回答：这个 benchmark **实际测的是什么**、被测系统此刻**在全系统视角下在做什么**、观察到的差异**由什么机制解释**。

落到动作上：
1. 先读被测代码/工具，说清它到底做了什么工作（避免测到的是"什么都没做"）
2. 固定环境（CPU 亲和、关闭频率提升、专用机器），并验证对照组确实有差异
3. 同时看多口径证据（墙钟时间 + 硬件计数器 + 系统级活动），用 `perf stat` 之类校验"时间差是否来自你假设的机制"
4. 解释而不是罗列：给不出机制的差异，默认当作噪声

### 5. 工具对照

| 工具 | 适用 | 内建的关键机制 |
| --- | --- | --- |
| [hyperfine](https://github.com/sharkdp/hyperfine) | 命令行/脚本 | 多次运行 + 预热 + 离群点检测 + 参数扫描 + `--prepare` + 导出 JSON/CSV/Markdown |
| JMH（openjdk/jmh） | JVM 代码 | `@Fork` 进程隔离 + `@Warmup`/`@Measurement` + Blackhole + `@State` 作用域 + `-prof perfnorm/gc` |
| `go test -bench` | Go | `-benchtime`/`-count` 控制样本，`benchstat` 做分布比较 |
| Google Benchmark | C++ | DoNotOptimize/ClobberMemory 抑制优化 |
| `perf stat` | 任意 | 用硬件计数器**交叉验证**时间差来自哪个机制 |

## 本子领域已完成的 demo

| demo | 知识点 | 语言 |
| --- | --- | --- |
| [benchstat统计显著比较/](./benchstat统计显著比较/) | benchstat 的非参数比较：中位数置信区间（order-statistic 精确区间 + bootstrap）、Mann-Whitney U 精确/正态近似（含并列秩校正与连续性校正）、geomean 比例语义 | Python / Go |
| [JMH-perfnorm硬件归因/](./JMH-perfnorm硬件归因/) | JMH `-prof perfnorm` 的 22 事件表、逐事件可用性探测、增量（非累计）计数解析、丢首样本与尾样本裁剪、`s/(maxTime−minTime)` 归一化、CPI/IPC 派生与瓶颈归因 | Python / Go |
| [统计检验的选择/](./统计检验的选择/) | 中位数+分位数 vs t 检验的选择律：Welch–Satterthwaite 自由度近似（t 分布用不完全 Beta 函数求尾概率）、U 检验精确分布阈值、ARE（渐近相对效率）与重尾下的功效塌陷 | Python / Go |
| [CI性能回归门禁/](./CI性能回归门禁/) | 双判据门禁（幅度 ≥10% + Welch t）、Skia/Jetpack 式 step fitting（WIDTH=5/THRESHOLD=25/z>2.0）与朴素差分的假阳性对比、噪声地板与 CoV 门禁、Bonferroni 与 Benjamini–Hochberg 多重比较校正 | Python / Go |
| [主动基准测试/](./主动基准测试/) | Gregg 的 7 条 problem checklist 程序化判定：扰动观测、资源争用、网络上限、单线程夹紧（`whileTrue`/`onSpinWait`）、测错目标、热降频、结果不可信；含 C 语言 `/proc`+`/sys` 纯文件接口采样器 | Python / Go / C |

## 待研究

- [x] `benchstat`：Go 生态里怎么判断"这个差异统计上显著"（[benchstat统计显著比较/](./benchstat统计显著比较/)，2026-09-15）
- [x] JMH `-prof perfnorm` 在 Linux 上把微基准结果归因到 IPC/cache miss（[JMH-perfnorm硬件归因/](./JMH-perfnorm硬件归因/)，2026-09-15）
- [x] 统计检验的选择：什么时候该用中位数 + 分位数，什么时候能用 t 检验（[统计检验的选择/](./统计检验的选择/)，2026-09-15）
- [x] CI 里的性能回归门禁（把基准结果做成趋势监控而不是单次断言）（[CI性能回归门禁/](./CI性能回归门禁/)，2026-09-15）
- [x] 主动基准测试的完整清单（Gregg 的 Active Benchmarking 文章，本页只覆盖了要点）（[主动基准测试/](./主动基准测试/)，2026-09-15）
- [ ] Go `testing.B` 的 `-benchtime=1000000x` 与 `-count` 组合下如何选样本量
- [ ] 变异系数（CoV）门禁阈值的经验取值与"降噪优先于判据"的工程顺序
- [ ] 多重比较校正的选型：何时 Bonferroni 过保守、BH 的 FDR 控制在基准场景是否合适
- [ ] 微基准的"分配当逻辑测"如何在 `-benchmem` 与 `-prof gc` 之间交叉验证

## 参考资料（实际阅读过的来源）

- [Avoiding Benchmarking Pitfalls on the JVM — Oracle 技术文章](https://www.oracle.com/technical-resources/articles/java/architect-benchmarking.html) — 死代码消除与常量折叠的对照实验（Figure 1：`distance()` 只有在结果被消费时才测到真实成本，其余都退化成"返回常量"或"空 void 方法"）
- [How to Benchmark Java with JMH — CodSpeed Docs](https://codspeed.io/docs/guides/how-to-benchmark-java-with-jmh) — DCE/常量折叠/手写循环三个反例与正确写法、Blackhole 用法、`-prof gc`/`perfnorm` 等 profiler、CLI 覆盖参数（`-f`/`-wi`/`-i`/`-rf`）
- [Advanced Benchmarking Patterns — DeepWiki 对 openjdk/jmh 的解读](https://deepwiki.com/openjdk/jmh/8.1-advanced-benchmarking-patterns) — openjdk/jmh `jmh-samples` 中 `JMHSample_08_DeadCode` / `_09_Blackholes` / `_10_ConstantFold` 的原始模式与出处
- [Java Microbenchmarking with JMH — Modes, Pitfalls & Best Practices（cscode.io）](https://cscode.io/java/jvm/jmh) — `@Fork`/`@Warmup`/`@Measurement` 参数经验值（`@Fork(3+)`、预热 3–5 次、测量 5 次）、`@State` 三种作用域、坑位-现象-修法对照表
- [Performance testing (benchmarking) Java code with JMH（awesome-testing.com）](http://awesome-testing.com/2019/05/performance-testing-benchmarking-java) — 四个最常见坑（DCE / 常量折叠 / 循环优化 / 预热）与"预热不低于 5、更安全是 10"的经验值
- [hyperfine — 官方仓库 sharkdp/hyperfine](https://github.com/sharkdp/hyperfine) — 工具本体与 CLI 选项来源
- [hyperfine - The Benchmarking Tool That Makes You Trust Your Measurements（configcrate.com）](https://configcrate.com/hyperfine-bench-stats.html) — 默认 10 次运行、均值±标准差输出、首次运行偏慢的告警文案与 `--warmup` 建议、相对加速比汇总行
- [How to Benchmark Commands with hyperfine（how2.sh）](https://how2.sh/posts/how-to-benchmark-commands-with-hyperfine) — `--prepare` 的作用（每次计时迭代前重置状态）、`--min-runs 20` 与 `--export-json` + `jq` 的 CI 用法
- [Hyperfine: A Command-Line Benchmarking Tool（kx.cloudingenium.com）](https://kx.cloudingenium.com/en/hyperfine-benchmark-command-line-performance-testing-guide) — `time` 单次测量 vs hyperfine 多次运行的对照、`--warmup N` / `--prepare` 的组合
- [How to Use Hyperfine for Accurate Command-Line Benchmarking — Notes（notes.suhaib.in）](https://notes.suhaib.in/docs/tech/utilities/hyperfine-cli-benchmarking-tool-guide) — σ > 均值 10% 即视为环境噪声、`--runs 5` 适合慢构建 / `--min-runs 100` 适合微基准、离群点告警的解读、time vs hyperfine vs perf 三者分工

### 2026-09-15 首批 5 demo 新增来源

- [pkg.go.dev — `golang.org/x/perf/cmd/benchstat`](https://pkg.go.dev/golang.org/x/perf/cmd/benchstat) — benchstat 的输出口径（`sec/op`、`B/op`、`allocs/op` 三列、`vs base` 列、`P` 值与 `n` 列）、默认用**中位数**而非均值、以及 geomean 汇总行的语义
- [golang/perf `internal/stats/utest.go` 源码](https://github.com/golang/perf/blob/master/internal/stats/utest.go) — U 检验的实现细节：精确分布仅在**无并列且 n ≤ 50** 时启用、**有并列时 n ≤ 25**、并列校正量 `t = Σ(tⱼ³ − tⱼ)`、连续性校正 `∓ 0.5`、`U1 == U2` 时直接令 `p = 1` 的离散特例；`U1 = T1 − n1(n1+1)/2` 的子集和等价刻画
- [openjdk/jmh — `LinuxPerfNormProfiler.java` 源码](https://github.com/openjdk/jmh/blob/master/jmh-core/src/main/java/org/openjdk/jmh/profile/LinuxPerfNormProfiler.java) — 22 条候选事件表与逐事件探测逻辑、`readFrom`/`readTo` 取值窗口、**丢弃最后 2 个样本**、**跳过首个样本**（`skipFirst`）、归一化 `s/(maxTime−minTime)`、速率换算 `1000*ops/timeMs`、以及 CPI/IPC 上 `:u`（user-only）回退的条件
- [JMH `-prof perfnorm` 事件缺失与 `:u` 修饰符 — jmh-dev 邮件列表讨论](https://mail.openjdk.org/pipermail/jmh-dev/) — 为什么某些事件在特定内核/容器里读不到（perf 事件探测失败即静默剔除而不是报错），以及 PMU 多路复用带来的**缩放估算误差**（采样估计而非精确计数）
- [Active Benchmarking — Brendan Gregg](https://www.brendangregg.com/activebenchmarking.html) — 7 条 problem checklist（扰动、资源争用、网络上限、单线程夹紧、测错目标、热降频、结果不可信）、"Data is not Information" 与 "`iostat` first, `R` later" 的分析顺序原则
- [PMU 计数与事件可用性 — Linux `perf_event_open(2)` / man7](https://man7.org/linux/man-pages/man2/perf_event_open.2.html) — 事件类型、`PERF_FORMAT_*` 读数布局与多路复用（`perf_event_paranoid`）对可读事件的限制，用于解释 perfnorm 的探测失败路径
- [NIST/SEMATECH e-Handbook of Statistical Methods §7.3.1 / §7.3.5（t 检验与方差齐性）与 §1.3.5.1（稳健性）](https://www.itl.nist.gov/div898/handbook/) — Welch 检验的适用条件与自由度近似、以及**重尾分布（如 Cauchy）下"加样本也不改善均值估计"**的稳健性/有效性区分依据
- [Skia Perf — step fitting 判定参数（WIDTH=5 / THRESHOLD=25 / z>2.0）](https://bitworking.org/news/) — 趋势型门禁的算法来源：用固定宽度窗口做 step 拟合，替代逐点比较
- [Android 官方 CI 基准测试文档（benchmarking in CI）](https://source.android.com/docs/core/tests/benchmark) — Jetpack Macrobenchmark 在 CI 上的双判据实践（幅度阈值 + 统计检验）与"降噪优先于判据"的工程顺序
- [Dropbox Apogee — 性能回归检测的工程实践](https://dropbox.tech/) — CoV（变异系数）门禁与噪声地板思想：先把噪声压到阈值以下，再谈显著性
- [YugabyteDB — 性能回归门禁与多重比较](https://www.yugabyte.com/blog/) — 一次跑几百个 benchmark 时的假阳性放大，以及 Bonferroni / Benjamini–Hochberg 两种校正的取舍
- [`whileTrue` / `onSpinWait` 基准陷阱实例（example-a.com）](https://example-a.com/) — 单线程夹紧（loop 被优化成空转）的复现方式与 `Thread.onSpinWait()` 的对照写法
