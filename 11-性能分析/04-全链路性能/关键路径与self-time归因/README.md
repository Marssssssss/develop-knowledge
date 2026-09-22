# 关键路径与 self time：把"端到端慢"拆成可归因的段落

> 有了全链路 trace 之后，"慢"仍然不是一个可以直接行动的词。一次 800 ms 的端到端请求，可能慢在服务端、可能慢在网络、可能慢在某个根本没被调用的下游——**span 树本身不告诉你答案，差值才告诉你**。
>
> 本 demo 给出三个可计算的归因口径（self time / parallel wait / 关键路径），并明确写出它们各自失效的场景。

## 核心研究主题

- span 树的数据模型：谁决定父子关系，谁决定跨进程配对
- **self time（独占时间）**：`duration − Σ 子 span 时长`
- **并发陷阱**：子 span 重叠时 self time 变负，此时该用什么口径
- **关键路径**：以 self time 为点权的根到叶最长路径
- **跨进程一跳的差值**：`CLIENT` 侧与 `SERVER` 侧时长之差 = 网络 + 排队
- **结构性异常**：多根、孤儿 span、负 self time

## 原理详解

### 1. 数据模型：父子与配对是两件事

OpenTelemetry 的 trace 是**一棵 span 树**：同一 trace 的所有 span 共享 `trace_id`，`parent_id` 为空的是根 span，`parent_id` 指向同 trace 内另一个 span 的 `span_id` 即构成父子关系。

真正的跨进程语义由 `SpanKind` 给出。规范把五个值按两个独立维度区分：

| `SpanKind` | 调用方向 | 通信形态 |
| --- | --- | --- |
| `CLIENT` | 出站 | 请求/响应 |
| `SERVER` | 入站 | 请求/响应 |
| `PRODUCER` | 出站 | 延迟执行 |
| `CONSUMER` | 入站 | 延迟执行 |
| `INTERNAL` | — | 进程内（默认值）|

并明确约定：**"`CLIENT` span 的上下文被传播出去后，`CLIENT` span 通常成为远端 `SERVER` span 的父"**。这句话是差值归因的许可证——它保证了"一跳"在调用方和被调用方**各有一个 span**，且二者是父子。

规范还给了两条容易被忽略的约束：一个 span 不应身兼数职（服务端 span 不该同时描述出站调用），以及**在注入 `SpanContext` 做远程调用之前应当先新建一个 span**。违反后者会让 `CLIENT` 的时长被算进父 span 的 self time 里，归因直接偏掉。

### 2. self time：差值归因的第一刀

```
self_time(s) = duration(s) − Σ duration(child)
```

单链上它非常干净。本 demo 的 `A → B → C`（100 / 90 / 60 ms）给出 self time **10 / 30 / 60 ms**，三者之和恰好等于根 span 的 100 ms——**单链上 self time 是对根时长的一个划分**，这也是它能做归因的根本原因。

### 3. 并发子 span 会让 self time 变负

扇出场景下这个公式会失效。根 `R` 时长 100 ms，三个**并发**子 span 各 60 / 40 / 30 ms，子 span 之和 130 ms：

```text
self_time(R) = 100 − 130 = -30 ms
```

负数不是 bug，它是一条**信息**：子 span 在时间上重叠了。此时正确的"独占等待"口径应该换成

```
parallel_wait(s) = duration(s) − max(duration(child))
```

本例中 `parallel_wait(R) = 100 − 60 = 40 ms`，读作"根 span 自己额外等了 40 ms（扣掉最长的那个分支之后）"。

判断该用哪个的实践规则：**`duration < Σ 子 span 时长` 即存在并发**，此时一律用 `parallel_wait`；只有子 span 顺序执行时两者相等（本 demo 的单链里 10/30/60 两口径完全一致）。

### 4. 关键路径：点权必须用 self time，不是 duration

关键路径 = **以 self time 为点权的根到叶最长路径**：

```
cp(s) = self_time(s) + max over children of cp(child)   （无子节点时取 0）
```

两个要点：

- 点权必须用 self time。用 duration 会把父 span 的时长沿路径反复计入，得出的"路径长度"比端到端时间还大；
- 结果**不等于**根 span 的 duration（除非是单链）。缺口就是"并行分支里没走完的那部分时间"——**这个缺口本身就是优化空间的上界**。

单链上 `cp = 10+30+60 = 100 ms = duration`，扇出上 `cp = −30 + 60 = 30 ms < 100 ms`。

一个实现层面的坑：**同分会由子节点顺序决定**。本 demo 的 Go 实现刻意对 children 按 `span_id` 排序后再取最优，Python 实现则保持插入顺序——两侧都在 README 里标注了口径，避免"同一份数据两个数字"。

### 5. 跨进程一跳：两侧差值才是网络 + 排队

按 SpanKind 的约定，一次 RPC 在调用方是 `CLIENT` span、在被调用方是 `SERVER` span，且是父子。于是：

```
gap = duration(CLIENT) − duration(SERVER)
```

本 demo 的 B(CLIENT, 90 ms) → C(SERVER, 60 ms) 给出 **gap = 30 ms**。这 30 ms 是网络往返 + 排队 + 序列化的总和，**在 client 侧和 server 侧的 profiler 里都看不见**——这是端到端追踪相对单机剖析最独特的价值。

配对的**方向是硬约束**：`(SERVER, CLIENT)` 与 `(INTERNAL, SERVER)` 都不构成合法配对，本 demo 用负向断言锁死了这一点（否则会把进程内的调用误算成网络开销）。`PRODUCER → CONSUMER` 同理，但因为 `CONSUMER` 可能**晚于** `PRODUCER` 才开始，gap 可以为负——这是正常的，不代表数据错了。

### 6. 结构性异常：先看树坏没坏，再谈归因

- **多根**：一条 trace 出现两个根，通常意味着上下文传播在中间断了；
- **孤儿 span**：`parent_id` 非空但父 span 不在集合里。最常见的原因是**同一条 trace 的 span 被路由到了不同的 collector 实例**（尾采样处理器对此有明确警告：all spans for a given trace MUST be received by the same collector instance），其次是采样不一致导致父 span 被丢；
- **负 self time**：并发信号，本身不算故障，但要按需切换口径。

本 demo 把孤儿**提升为伪根**而不是丢弃——丢弃会让它连同整棵子树一起从视图里消失，那比多一个根更糟。

## 代码结构

| 文件 | 说明 |
| --- | --- |
| `python/spantree.py` | 建树、self time、parallel wait、关键路径、配对差值、异常检测 |
| `python/main.py` | 四组现象驱动（单链 / 配对 / 扇出 / 断链） |
| `python/selfcheck_criticalpath.py` | 38 条断言（实跑全绿） |
| `go/spantree.go` | 同口径 Go 实现；显式排序以消除 map 遍历顺序带来的 tie 不确定性 |

运行：

```bash
cd python && python main.py && python selfcheck_criticalpath.py
```

## 实测现象摘录

```text
A dur=100.0ms  self= 10.0ms   B self= 30.0ms   C self= 60.0ms   合计=100ms
B(CLIENT) 90ms vs C(SERVER) 60ms -> gap=30ms   （两侧 profiler 都看不见）
R duration=100ms 子 span 之和=130ms -> self_time(R)=-30ms  parallel_wait(R)=40ms
关键路径: ['R','a']  自耗合计=30ms
roots=['D','R']  orphans=['D']  anomalies=['multi_root:2', "orphan:['D']"]
```

## 工程启示

1. **先检查树再归因**。多根/孤儿会让 self time 与关键路径全部失真；把异常检测做成看板的第一列。
2. **self time 为负时立刻切 `parallel_wait`**。看到负数不要"裁剪到 0"，那会把并发度信息抹掉。
3. **关键路径的缺口是优化空间的上界**。让某条不在关键路径上的分支变快，端到端时间一毫秒都不会少。
4. **跨进程的 gap 要单独监控**。它既不在 client 的 CPU profile 里，也不在 server 的，只有 trace 能给出；它是"两边都很健康但端到端很慢"的唯一定位入口。
5. **配对必须校验 SpanKind 方向**，否则进程内调用会被当成网络开销，归因结论直接反向。

## 参考资料（实际阅读过的来源）

- [OpenTelemetry Specification — Trace API（SpanKind / Link）](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/trace/api.md) — 五值 SpanKind 与"调用方向 / 通信形态"二维表；`CLIENT` 上下文传播后通常成为远端 `SERVER` 的父；`INTERNAL` 为默认值；"一个 span 不应身兼数职"与"注入 SpanContext 前应先新建 span"的约束
- [OpenTelemetry 官方文档 — Traces](https://opentelemetry.io/docs/concepts/signals/traces/) — trace/span 的数据模型、`parent_id` 为空即根 span、span context 作为跨进程传播载体的说明
- [W3C Trace Context Level 1](https://www.w3.org/TR/trace-context/) — `traceparent` 里被传播的正是 `parent-id`，即"远端 SERVER span 的父是谁"的线上载体（与 [W3C-TraceContext传播](../W3C-TraceContext传播/) 互为上下游）
- [opentelemetry-collector-contrib — Tail Sampling Processor README](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/tailsamplingprocessor/README.md) — "All spans for a given trace MUST be received by the same collector instance"，即本 demo 中孤儿 span 最常见的成因

> 口径声明：`self_time` 与 `parallel_wait` 是本 demo 按 span 树的语义给出的定义；`critical_path` 的"点权取 self time"亦为本 demo 选定（另一种常见取法是点权取 duration，README 中已说明为何不采用）。规范只定义了数据模型，不定义这些度量。
