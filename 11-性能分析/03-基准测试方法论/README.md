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
| [GoBench样本量/](./GoBench样本量/) | `testing.B` 的迭代标定：`durationOrCountFlag.Set` 的 Nx/duration 两态、`predictN` 的先乘后除 + 1.2× + `100*last` + `1e9` 上限（**不是** 1,2,5,10 序列）、`1x` 复用 `run1`、`RunParallel` grain 钳制、`-count` 才是样本量 | Python / Go |
| [MDE与样本量估算/](./MDE与样本量估算/) | 最小可检测效应与样本量方程：NIST §7.2.2.2 两个原例（8.567→9、10.6→11）、t 迭代解、`MDE = (z+z)·CoV·√(2/n)`、`-count=10/20` 对应 1.253%/0.886%×CoV | Python / Go |
| [CoV噪声地板/](./CoV噪声地板/) | 变异系数与噪声地板：A/A 地板 `CoV·√(2/n)·√(2/π)`、Apogee「60%→5%」的口径核对（每侧 16 次才够）、离群剔除的 O(σ) 偏差 −0.2907σ、交错 vs 顺序跑的漂移倍数恰为 n | Python / Go |
| [多重比较校正/](./多重比较校正/) | FWER vs FDR：全局零效应下二者**相等**（V≡R）、有真效应时 BH 用 FWER 0.220 换功效 0.530、Holm ⊇ Bonferroni、BY 的 H_m 惩罚因子、benchstat 不做任何校正 | Python / Go |
| [分配测量与GC/](./分配测量与GC/) | `-benchmem` 的净值/差值语义、整数截断导致的 `0 B/op ≠ 零分配`、GC 摊销使 `ns/op` 随 N **非单调**（100→120→108）、`GCCPUFraction` 分母是 GOMAXPROCS 积分 | Python / Go |
| [JMH-gc与benchmem分配口径/](./JMH-gc与benchmem分配口径/) | 两侧分配口径对拍：JMH `gc.alloc.rate.norm` 是浮点且**零分配时整列消失**，Go 一律打印 `0 B/op`；JMH 是进程级窗口而 Go 只算 `StartTimer→StopTimer`；`gc.count` 用 SUM 而 `alloc.rate` 用 AVG ⇒ 两者不可相除；JMH **没有对象数**口径 | Python(40 断言实跑) / Go |
| [预热充分性判定/](./预热充分性判定/) | pyperf `test_calibrate_warmups` 的可执行判据：样本对半分 + 离群（只查最大值、iqr 里不含首值）+ 五条不等式；`mean_diff` 区间**非对称** `[-0.5, +0.10]`；离散度用 MAD 不用 stdev；JMH 只有固定 5 次，实测欠预热 216% | Python(35 断言实跑) / Go |
| [趋势存储与分片/](./趋势存储与分片/) | Perfherder 的 40 字符 `signature_hash`（key 与 value 混在一个桶里排序 ⇒ 键值互换碰撞）vs Chrome Perf 的**路径即主键**；父子关系一用 `parent_signature` 外键、一用 `parts[:-1]` 推导；`Row` 的 X 轴必须是单调整数 | Python(54 断言实跑) / Go |
| [容器与虚拟化下的标定/](./容器与虚拟化下的标定/) | cgroup CFS 配额的**量化**闭式 `(k-1)P + (R-(k-1)Q)`：前 Q 的 CPU 时间免费 ⇒ 短任务免疫、配额边界是悬崖、长任务趋近 `P/Q`；限流**不改 N 却把 ns/op 放大约 P/Q 倍**；steal 是乘性且叠加；`GOMAXPROCS` 手动设过就不再跟随 cgroup | Python(45 断言实跑) / Go |
| [参数化基准的多维比较/](./参数化基准的多维比较/) | hyperfine 默认以**全局最快**为参考 ⇒ 多维扫描把各维度混成一个数（×10 = 4× size × 2.5× threads），公平做法是分组内选参考；`RangeStep` 的 `size_hint` 用 `(end-start+1)/step` ⇒ 整数步长低估、小数步长高估；JMH `@Param` 走外积、Enum 默认取全部常量 | Python(37 断言实跑) / Go |

## 待研究

- [x] `benchstat`：Go 生态里怎么判断"这个差异统计上显著"（[benchstat统计显著比较/](./benchstat统计显著比较/)，2026-09-15）
- [x] JMH `-prof perfnorm` 在 Linux 上把微基准结果归因到 IPC/cache miss（[JMH-perfnorm硬件归因/](./JMH-perfnorm硬件归因/)，2026-09-15）
- [x] 统计检验的选择：什么时候该用中位数 + 分位数，什么时候能用 t 检验（[统计检验的选择/](./统计检验的选择/)，2026-09-15）
- [x] CI 里的性能回归门禁（把基准结果做成趋势监控而不是单次断言）（[CI性能回归门禁/](./CI性能回归门禁/)，2026-09-15）
- [x] 主动基准测试的完整清单（Gregg 的 Active Benchmarking 文章，本页只覆盖了要点）（[主动基准测试/](./主动基准测试/)，2026-09-15）
- [x] Go `testing.B` 的 `-benchtime=1000000x` 与 `-count` 组合下如何选样本量（[GoBench样本量/](./GoBench样本量/)，2026-09-19）
- [x] 变异系数（CoV）门禁阈值的经验取值与"降噪优先于判据"的工程顺序（[CoV噪声地板/](./CoV噪声地板/)，2026-09-19）
- [x] 多重比较校正的选型：何时 Bonferroni 过保守、BH 的 FDR 控制在基准场景是否合适（[多重比较校正/](./多重比较校正/)，2026-09-19）
- [x] 微基准的"分配当逻辑测"如何在 `-benchmem` 与 `-prof gc` 之间交叉验证（[分配测量与GC/](./分配测量与GC/)，2026-09-19）
- [x] JMH `-prof gc` 与 Go `-benchmem` 的分配口径对比（一次分配的对象数 vs 字节数 vs GC 次数）（[JMH-gc与benchmem分配口径/](./JMH-gc与benchmem分配口径/)，2026-09-21）
- [x] 预热充分性的判定：如何程序化确认 JIT / 内联缓存已进入稳态（而不是固定 warmup 次数）（[预热充分性判定/](./预热充分性判定/)，2026-09-21）
- [x] 基准结果的长期趋势存储与分片（benchstat 之外的时序方案，如 Skia Perf / Firefox Perfherder 的数据模型）（[趋势存储与分片/](./趋势存储与分片/)，2026-09-21）
- [x] 虚拟化与容器环境（`cgroup` CPU quota、 steal time）对 `-benchtime` 标定结果的影响（[容器与虚拟化下的标定/](./容器与虚拟化下的标定/)，2026-09-21）
- [x] 参数化基准（hyperfine `--parameter-scan` / JMH `@Param`）的结果如何做公平的多维比较（[参数化基准的多维比较/](./参数化基准的多维比较/)，2026-09-21）
- [ ] 跨语言基准的口径对齐：同一段逻辑在 Python / Go / C 三个实现下如何公平比较（编译期常量折叠、运行时分配策略都不等价）
- [ ] 基准结果的可复现性打包：如何把「环境指纹」（CPU 型号 / 微码 / 内核 / 编译器版本）随结果一起存
- [ ] 长跑基准的漂移检测：同一台机器跨月的结果如何区分「代码变慢」与「机器变慢」
- [ ] JMH `-prof async` / `perfasm` 的汇编级归因：如何确认热点指令确实来自被测代码而不是框架
- [ ] 基准套件的时间预算分配：在固定 CI 时长下按哪些基准的方差分配 `-count`

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

### 2026-09-21 第三批 5 demo 新增来源

- [`openjdk/jmh` — `jmh-core/.../profile/GCProfiler.java`](https://github.com/openjdk/jmh/blob/master/jmh-core/src/main/java/org/openjdk/jmh/profile/GCProfiler.java) — 541 的依据：`gc.count` 遍历全部 GC bean、`gc.time` 条件发射、`gc.cpuTime` 整除 1e6、`gc.alloc.rate.norm` 的 `allocated != 0` 分支、全局与 per-thread 两条快照路径、负差钳 0、churn 的 `c > 0` 与 `churnWait=500`
- [`golang/go` — `src/testing/benchmark.go`](https://github.com/golang/go/blob/master/src/testing/benchmark.go) — 541 与 544 的依据：`StartTimer`/`StopTimer`/`ResetTimer`/`runN` 顺序、`predictN` 四步钳制与"先乘后除"注释、`maxBenchPredictIters=1e9`、`launch` 循环判据
- [`psf/pyperf` — `pyperf/_worker.py` 与 `_utils.py`](https://github.com/psf/pyperf/blob/main/pyperf/_worker.py) — 542 的依据：`MAX_WARMUP_VALUES=300`、`WARMUP_SAMPLE_SIZE=20`、五条不等式、"只查最大值"的离群检验、`percentile` 线性插值、`median_abs_dev`、`mad_diff` 的除零 FIXME
- [`openjdk/jmh` — `runner/Defaults.java` 与 `annotations/Warmup.java`](https://github.com/openjdk/jmh/blob/master/jmh-core/src/main/java/org/openjdk/jmh/runner/Defaults.java) — 542 的对照组：`WARMUP_ITERATIONS=5`、`WARMUP_ITERATIONS_SINGLESHOT=0`、`WARMUP_FORKS=0`、`BLANK_* = -1` 哨兵
- [`mozilla/treeherder` — `treeherder/etl/perf.py` 与 `perf/models.py`](https://github.com/mozilla/treeherder/blob/master/treeherder/etl/perf.py) — 543 的依据：`_get_signature_hash` 的键桶排序、`SIGNATURE_HASH_LENGTH=40`、`parent_signature` 注入、`_create_or_update_signature` 的 `last_updated` 单调、两条 `unique_together`
- [`catapult-project/catapult` — `dashboard/dashboard/models/graph_data.py`](https://github.com/catapult-project/catapult/blob/master/dashboard/dashboard/models/graph_data.py) — 543 的依据：`TestMetadata` 以完整路径为 key、`bot`/`parent_test` 的段数判据、`Row` 的 `revision = key.integer_id()`、`d_`/`r_`/`a_` 前缀、`LastAddedRevision` 拆实体的原因
- [`golang/go` — `src/internal/runtime/cgroup/cgroup.go`](https://github.com/golang/go/blob/master/src/internal/runtime/cgroup/cgroup.go) — 544 的依据：`parseV1Number` 换行截断、`parseV2Limit` 的 `max` 字面量、v1 CPU controller 优先于 v2
- [`golang/go` — `src/runtime/proc.go`](https://github.com/golang/go/blob/master/src/runtime/proc.go) — 544 的依据：`sysmonUpdateGOMAXPROCS` 的 `customGOMAXPROCS` 与"值没变就不动"两道闸门
- [Linux `Documentation/filesystems/proc.rst`](https://github.com/torvalds/linux/blob/master/Documentation/filesystems/proc.rst) — 544 的依据：`/proc/stat` cpu 行 `steal: involuntary wait`
- [`sharkdp/hyperfine` — `src/parameter/range_step.rs`、`src/benchmark/relative_speed.rs`、`benchmark_result.rs`](https://github.com/sharkdp/hyperfine/blob/master/src/parameter/range_step.rs) — 545 的依据：三条拒绝、`MAX_PARAMETERS=100_000`、`size_hint` 公式、`fastest_of` 与 ratio 三向取法、误差传播、`parameters: BTreeMap`
- [`openjdk/jmh` — `annotations/Param.java`](https://github.com/openjdk/jmh/blob/master/jmh-core/src/main/java/org/openjdk/jmh/annotations/Param.java) — 545 的依据：outer product 原文、`BLANK_ARGS` 哨兵、`@State` 非 final 约束、Enum 的隐式默认值

### 2026-09-19 第二批 5 demo 新增来源

- [`golang/go` — `src/testing/benchmark.go`（master 分支源码）](https://raw.githubusercontent.com/golang/go/master/src/testing/benchmark.go) — `-benchtime` 的 `durationOrCountFlag.Set` 解析、默认 `1s`、`predictN` 的四处钳制、`launch`/`run1`/`runN` 的调用关系、`runtime.GC()` 的位置、`RunParallel` 的 grain、`BenchmarkResult` 的整数除法与 `Extra` 优先
- [`pkg.go.dev/runtime#MemStats`](https://pkg.go.dev/runtime#MemStats) — `TotalAlloc`/`Mallocs`/`Frees`/`PauseTotalNs`/`NextGC`/`GCCPUFraction` 的字段文档原文
- [NIST/SEMATECH e-Handbook §7.2.2.2（Sample sizes required）](https://www.itl.nist.gov/div898/handbook/prc/section2/prc222.htm) 与 [§1.3.5.3（Two-Sample t-Test）](https://www.itl.nist.gov/div898/handbook/eda/section3/eda353.htm) — 样本量公式、迭代要求与 Welch-Satterthwaite 自由度
- [Keeping sync fast with automated performance regression detection — Dropbox Tech Blog](https://dropbox.tech/infrastructure/keeping-sync-fast-with-automated-performance-regression-detectio) — Apogee 的 60%→5%、「每 5 次剔除 1 个」、用 CoV 做相关分析
- [Benchmarking tips — LLVM Documentation](https://llvm.org/docs/Benchmarking.html) — 降噪清单（ASLR / scaling_governor / cpuset / SMT 对 / tmpfs）、"perf variations of less than 0.1%"、"low noise is required, but not sufficient"
- [`statsmodels.stats.multitest.multipletests` 文档](https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html) — FWER/FDR 方法清单与"独立下控制、多数在正相关下稳健"
- [Benjamini & Hochberg (1995) 与 Benjamini & Yekutieli (2001) 的临界常数](https://www.sciencedirect.com/science/article/abs/pii/S0378375808000165) — `α_i=(i/m)α` 与任意依赖下 `α_i = iα/(m·Σ1/j)`

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
