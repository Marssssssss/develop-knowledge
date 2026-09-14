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

## 待研究

- [ ] `benchstat`：Go 生态里怎么判断"这个差异统计上显著"
- [ ] JMH `-prof perfnorm` 在 Linux 上把微基准结果归因到 IPC/cache miss
- [ ] 统计检验的选择：什么时候该用中位数 + 分位数，什么时候能用 t 检验
- [ ] CI 里的性能回归门禁（把基准结果做成趋势监控而不是单次断言）
- [ ] 主动基准测试的完整清单（Gregg 的 Active Benchmarking 文章，本页只覆盖了要点）

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
