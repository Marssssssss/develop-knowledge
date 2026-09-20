# Java JFR 与 async-profiler：async 采样如何绕开 safepoint 偏差

> 大多数 Java 采样剖析器要等 JVM 到达 **safepoint** 才能安全取栈。
> 于是样本只会落在「能到达 safepoint 的地方」——这就是 **safepoint bias**。
> async-profiler 的定位一句话写在 README 开头：
> *a low overhead sampling profiler for Java that does not suffer from Safepoint bias problem*。

## 一、safepoint 偏差是什么

模型化之后非常直观（demo 里的 `Method` 逐行标出「是否轮询点」）：

```text
第 10 行：热点循环（内部没有 safepoint 轮询点）  耗时 30
第 11 行：轮询点                                  耗时 2
第 12 行：另一段热点（同样没有轮询点）            耗时 28
第 13 行：轮询点                                  耗时 2

async 采样（10 单位一次）  ⇒ 行10=3  行12=3     ← 落在真正执行的行
safepoint 采样            ⇒ 行11=3  行13=3     ← 全被挪到轮询点
```

样本**总数一样**，只是**归属被挪了**——热点从「业务行」被记到了「轮询点」。
更极端的情形：一段跑在原生代码里、整段都没有 safepoint 的执行，
async 照样采到样本，而 safepoint 剖析器 **一个样本都拿不到**。

这就是「火焰图上热点是一句看起来无害的代码」的常见来源。

## 二、三种 CPU 采样引擎（`-e cpu` / `-e itimer` / `-e ctimer`）

| 属性 | cpu (perf_events) | itimer | ctimer |
| --- | :---: | :---: | :---: |
| 能采内核栈 | ✅ | ❌ | ❌ |
| 高分辨率 | ✅ | ❌ | ❌ |
| 准确性 / 公平性 | ✅ | ❌ | 🆗 |
| 容器里默认可用 | ❌ | ✅ | ✅ |
| 不吃文件描述符 | ❌ | ✅ | ✅ |
| macOS 支持 | ❌ | ✅ | ❌ |

几条原文要点：

- **1 个样本 = 1 个核持续忙了 N 纳秒**（N = 采样间隔）。2 核各 30% 利用率、间隔 10ms
  ⇒ `2 × 0.3 × 100 = 60` 样本/秒。
- **cpu 引擎给每个线程开一个 perf 描述符**，所以线程多 + `ulimit -n` 小 ⇒ 描述符耗尽。
  它也是唯一能拿**内核栈**的引擎，但常被 `kernel.perf_event_paranoid` / seccomp 拦住。
- **itimer** 基于 `setitimer(ITIMER_PROF)`：一次只能给进程投递一个信号、
  信号**不在线程间均分**、分辨率受 jiffy 限制；macOS 上还偏向系统调用。
  好处是容器里能用、不吃描述符。
- **ctimer** 基于 `timer_create`，绕开 perf_events 的限制，但最小间隔受 `HZ` 约束：
  `HZ=100 ⇒ 10ms`，`HZ=250 ⇒ 4ms`。
- `-e cpu` 在 Linux 上会先试建一个 dummy perf_event，不可用就**透明回落到 ctimer**；
  真要强制 perf 只采用户态，用 `-e cpu-clock --all-user`。

## 三、栈行走模式：FP / DWARF / VM Structs

| 模式 | 依赖 | 备注 |
| --- | --- | --- |
| FP | 编译时保留帧指针（`-fno-omit-frame-pointer`） | 最快；**4.2 之前是默认** |
| DWARF | `.eh_frame` / `.debug_frame` | 优化掉帧指针也能走；`libjvm.so` 的查找表约 2MB；比 FP 慢但仍是 signal-safe |
| VM Structs | HotSpot VM 内部结构 | **4.2 起默认**；`--cstack vm`，`vmx` 可让 Java 与原生帧交错；`-j depth` 控深度 |

为什么换成 VM Structs：async-profiler 原本重度依赖 `AsyncGetCallTrace`（AGCT，
名字里的 async 就来自它），但 AGCT 是**非标准扩展**，在 OpenJDK 里支持得很差、
多次在小版本更新中坏掉（JDK-8307549），还有一批走不了栈的边界情形（JDK-8178287），
**最糟的是它可能直接崩掉 JVM**，而且在 JVM 之外没有可靠的规避手段。
VM Structs 模式整体被 `setjmp`/`longjmp` 的崩溃保护包住，能显示 Java、native、JVM stub 全栈，
还能给出每帧的 JIT 编译类型。

## 四、JFR 的设计（JEP 328）与事件编码

- 目标：**开箱 ≤1% 开销**（SPECjbb2015 上的口径）；**未启用时无可测开销**。
- 线程**无锁**写自己的线程本地缓冲；缓冲满了晋升到**全局环形缓冲**，
  只保留最近的数据（按保留策略丢弃或落盘）。
- 自描述二进制，编码是 **little endian base 128（LEB128）**，
  文件头和少数段除外；格式本身不保证稳定，要走 API。
- 官方给的 24 字节 class load 事件实例（demo 直接拿它做解码断言）：

```text
98 80 80 00 | 87 02 | 95 ae e4 b2 92 03 | a2 f7 ae 9a 94 02 | 02 | 01 | 8d 11 00 00
  大小=24       id=263      时间戳              时长           线程  栈   负载
```

## 五、JFR Event Streaming（JEP 349）

- `EventStream.openRepository()` / `openFile()` / `RecordingStream`；
  与 `RecordingFile` 互补，既能读文件也能读「活的」磁盘仓库。
- **线程本地缓冲由 JVM 每秒刷一次**到磁盘仓库 ⇒ 事件**不是立刻可见**的；
  `EventStream::onFlush(Runnable)` 就是刷完的回调时机。
- 为了压开销，**只有被订阅的事件才会从文件里读出来**；也可以复用事件对象减少分配。
- 目标同样是 <1% 开销，且要能与「非流式」录制共存。

## 六、两个上手就该记住的开关

1. **`-XX:+UnlockDiagnosticVMOptions -XX:+DebugNonSafepoints`**：
   agent 不是随 JVM 启动时加载的话强烈建议加。没有它剖析仍然能跑，但**被内联的方法很可能不出现**。
   运行时 attach 的话，只有 attach 之后编译的方法才通过 `CompiledMethodLoad` 拿到调试信息。
2. **`--ttsp`（time-to-safepoint）**：等价于
   `--begin SafepointSynchronize::begin --end RuntimeService::record_safepoint_synchronized`。
   它不是新事件类型，而是**约束**：只记录「从发起 safepoint 请求到 VM 操作真正开始」之间的样本——
   专门用来查「到不了 safepoint」这类问题。

## 七、运行

```bash
python python/asyncprof_safepoint.py   # 31 条断言
cd go && go run .                       # 同语义 Go 版（本机无工具链，人工审查）
```

## 八、注意事项与常见坑

1. **不要把「火焰图上最宽的框」当结论**——如果剖析器有 safepoint 偏差，最宽的很可能是轮询点。
2. **容器里 `-e cpu` 常常静默降级成 ctimer**（先探测 perf_event）。
   要确认实际引擎，看 jfr 输出里记录的 engine 字段。
3. **itimer 的样本在线程间不均分**，多线程应用的 per-thread 占比不可信。
4. **AGCT 相关崩溃**是换 VM Structs 的直接原因；遇到随机崩溃/缺栈先确认 async-profiler 版本。
5. **JFR 流式消费有约 1 秒的可见性延迟**，拿它做实时告警要考虑这个下限。
6. `--ttsp` 是**约束不是事件**：单看事件类型会以为没生效。

## 参考资料（本轮实际读过）

- [async-profiler — README（safepoint bias、`--ttsp`、`DebugNonSafepoints`）](https://raw.githubusercontent.com/async-profiler/async-profiler/master/README.md)
- [async-profiler — docs/CpuSamplingEngines.md](https://raw.githubusercontent.com/async-profiler/async-profiler/master/docs/CpuSamplingEngines.md)
- [async-profiler — docs/StackWalkingModes.md](https://raw.githubusercontent.com/async-profiler/async-profiler/master/docs/StackWalkingModes.md)
- [JEP 328: Java Flight Recorder](https://openjdk.org/jeps/328)
- [JEP 349: JFR Event Streaming](https://openjdk.org/jeps/349)
