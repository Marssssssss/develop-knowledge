# W3C Trace Context：traceparent 布局、采样标志与 tracestate 变更

> 全链路追踪能成立的前提只有一个：**跨进程的上下文能无损地传下去**。W3C Trace Context 就是把这件事定死的规范——`traceparent` 用**定长、可快速解析**的格式携带"这条请求在 trace 图里的位置"，`tracestate` 用**厂商无关的列表**携带附加信息。
>
> 本 demo 把规范里最容易被写错的条款（sampled 是位不是数、大写十六进制非法、高版本按位置解析、修改 tracestate 必须把 key 移到最左、截断有固定顺序）落成可运行的实现与断言。

## 核心研究主题

- `traceparent` 的四字段布局与逐字段校验（哪些非法值对应"忽略"，哪些对应"重开 trace"）
- `trace-flags` 的**位语义**：sampled = bit 0，为什么 `flags == 1` 是错的
- 高版本 `traceparent` 的**按位置解析**与 55 字符下限
- 四种允许的变更（更新 parent-id / 更新 sampled / 重开 / 降级）与一条禁止项
- `tracestate` 的列表语义、变更时的**左移**规则、超限时的**两阶段截断**
- `baggage` 的传播下限（64 成员 / 8192 字节）

## 原理详解

### 1. 布局：四个定长字段，一个都不能猜

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
             │  │                                │                └─ trace-flags (8 bit)
             │  └─ trace-id  (16 字节, 32 hex)    └─ parent-id (8 字节, 16 hex)
             └─ version (1 字节, 00；ff 是禁止值)
```

规范用 ABNF 把长度钉死：`version = 2HEXDIGLC`、`trace-id = 32HEXDIGLC`、`parent-id = 16HEXDIGLC`、`trace-flags = 2HEXDIGLC`。`HEXDIGLC` 的 `LC` 是 **lowercase**，所以 `4BF92F…` 这种大写写法**非法**——这是跨语言实现最常见的分歧点之一。

两类非法值的**处置方式不同**，这点必须在实现里区分开：

| 情况 | 处置 | 规范措辞 |
| --- | --- | --- |
| `version = ff` | 忽略整个头 | "Version ff is invalid" |
| trace-id 非法/全零 | 忽略整个头 | "vendors MUST ignore the traceparent" |
| parent-id 非法/全零 | 忽略整个头 | "MUST ignore the traceparent when the parent-id is invalid" |
| version 前缀不可解析 | **重开 trace** | "the implementation should restart the trace" |
| 高版本且短于 55 字符 | **重开 trace** | "should not parse the header and should restart the trace" |

"忽略"与"重开"的差别在工程上很实在：忽略意味着这条请求**不参与**上游的 trace，也不许去解析 `tracestate`（"If the vendor failed to parse `traceparent`, it MUST NOT attempt to parse `tracestate`"）；重开意味着**自己新起一条** trace。把它们都写成"返回 nil"会把两种语义混成一种。

### 2. sampled 是 bit 0，不是"等于 1"

规范专门点名了这个坑：

> "a flag `00000001` could be encoded as `01` in hex, or `09` in hex if present with the flag `00001000`. A common mistake in bit fields is forgetting to mask when interpreting flags."

所以正确写法是 `(traceFlags & FLAG_SAMPLED) == FLAG_SAMPLED`。本 demo 实测：`flags = 09 / 0f / ff` 时掩码判定为 **sampled**，而 `flags == 1` 会全判成 False；`flags = 08 / 02 / fe` 时掩码判定为 **未采样**。

要注意 sampled 只是**建议**不是命令。规范给了三条理由：信任与滥用、调用方可能有 bug、**调用方与被调用方的负载不同**（被调用方可能必须降采样）。同时 sampled 有一个硬约束：**只有在 parent-id 被更新时才能改它**——因为改了它就必须换掉 parent-id。

### 3. 高版本：按位置解析，短于 55 字符直接重开

规范对未来的高版本采取了"乐观但保守"的策略：不看版本先按**位置**切。这给出了一套可计算的解析规则：

```text
下标:  0  1  2  3 ────────────── 34 35 36 ───────── 51 52 53 54
       v  v  -  trace-id(32)          -  parent-id(16)   -  flags(2)
```

即 dash 落在下标 **2 / 35 / 52**，flags 之后（下标 55）必须是串尾或又一个 dash。由此反推出那条"短于 55 字符就重开"的下限：一个最小合法的高版本头恰好 55 字符。本 demo 实测 58 字符的 `01-…-01-x9` 能解析并**降级**到 v00 语义，54 字符的则判为 restart。

### 4. 只有四种变更是允许的

§3.4 明确列出：更新 parent-id、更新 sampled（**必须同时换 parent-id**）、重开 trace（三个字段全部重生成，安全网关入口的典型用法，同时应清掉 tracestate）、降级版本。最后一句是 "Vendors MUST NOT make any other mutations to the traceparent header."

还有一条容易漏的联动：**traceparent 没改，tracestate 就不能改**（"If the value of the traceparent field wasn't changed before propagation, tracestate MUST NOT be modified as well"）。纯透传的代理靠这条把开销压到零。

### 5. tracestate：左 = 最新，同 key 只允许一条

`tracestate` 是逗号分隔的 `key=value` 列表，**最多 32 个成员**。语义上有三条：

1. **最左的位置告诉下游"写 traceparent 的是哪个系统"**——所以任何被修改的 key 都要移到列表最左（"Modified keys SHOULD be moved to the beginning (left) of the list"），未修改的相对顺序必须保持不变；
2. **同一 key 只能有一条**："Only one entry per key is allowed because the entry represents that last position in the trace." 重进自己系统时是**覆写**而非追加，所以 `congo=A,rojo=B,congo=C` 这种写法是错的，正确结果是 `congo=C,rojo=B`；
3. 截断有固定顺序：**先删长度 > 128 字符的条目，仍不够再从尾部删整条**。规范明确排斥"按大小随便切"的做法，因为那会破坏互操作；而且**不能切半个成员**。

key 的合法性也值得单独说：必须以**小写字母或数字**开头，只能含 `a-z 0-9 _ - * /`；多租户形态是 `tenant-id@system-id`，tenant-id ≤ 241、system-id ≤ 14（这样 `搜索所有 @xyz=` 就能快速定位租户）。value 是 0x20–0x7E 的可打印 ASCII，**逗号与等号被排除**（它们分别是成员分隔符和键值分隔符），最长 256 字符。

### 6. baggage：传播下限是 64 成员 / 8192 字节

配套的 W3C baggage 规范给的是**下限**而非上限：只要组合后不超过 64 个成员且不超过 8192 字节，平台就 **MUST** 全部传播；超了才 MAY 丢弃，且"MUST NOT propagate any partial list-members"（不能传播半个）。多条 `baggage` 头合并后再套用这些限额，不是逐条算。

## 代码结构

| 文件 | 说明 |
| --- | --- |
| `python/traceparent.py` | 解析 / 校验 / 格式化 / 四种变更；区分 `ignore` 与 `restart` |
| `python/tracestate.py` | 列表解析、key/value 合法性、左移变更、两阶段截断、baggage 限额 |
| `python/main.py` | 六组现象的驱动脚本 |
| `python/selfcheck_tracecontext.py` | 62 条断言（实跑全绿） |
| `go/tracecontext.go` | 同口径的 Go 实现；额外强调 `flags == 1` 在 Go 里同样没有编译期保护 |

运行：

```bash
cd python && python main.py && python selfcheck_tracecontext.py
```

## 实测现象摘录

```text
trace-flags=09 -> sampled=True   (朴素比较 flags==1 会给出 False)
trace-flags=ff -> sampled=True   (朴素比较 flags==1 会给出 False)
trace-flags=fe -> sampled=False
len=54 -> restart（重开 trace，并清掉 tracestate）
改 congo: congo=00f067aa0ba902b8,rojo=00f067aa0ba902b7   <- 修改过的 key 移到最左
原始 13 条 -> 截断后 11 条，最长条目 44 字符   <- 先删 200 字符的长条目
```

## 工程启示

1. **判定 sampled 必须掩码**。`flags == 1` 在 v00 下看似能用，一旦有其他位被置上就静默全判 False——而 sampled 位恰恰是用来跨厂商传递"上游可能已记录"这一信息的，判错会导致下游漏采，trace 断链。
2. **区分"忽略"与"重开"**。二者都会让这条请求脱离上游 trace，但重开会**新造一个 trace_id**，在排查时会看到"为什么这里突然多了一条根"。把 reason 传给调用方，日志里就能说清。
3. **改 tracestate 就要改 traceparent**。透传路径（代理、网关）如果顺手在 tracestate 里加了自己的条目却没换 parent-id，就违反了 §3.4 的联动约束，下游会把"哪个系统写了这个头"认错。
4. **截断按规范顺序做**。先删长条目再删尾部，能让最容易挤爆 header 的那一两条先走，同时保住最左侧"当前系统"的条目——这是唯一能保住互操作性的切法。

## 参考资料（实际阅读过的来源）

- [W3C Trace Context Level 1 — TR/trace-context](https://www.w3.org/TR/trace-context/) — `traceparent` 四字段的 ABNF 与逐字段校验、`version ff` 禁止、trace-id/parent-id 全零非法、sampled 位语义与"忘记掩码"的常见错误、高版本的 55 字符下限与按位置解析、§3.4 四种允许变更与"traceparent 未改则 tracestate 不得改"、§3.3 tracestate 的 32 成员上限/key 与 value 字符集/左移规则/两阶段截断
- [W3C Baggage — TR/baggage](https://www.w3.org/TR/baggage/) — 64 成员 / 8192 字节的传播下限、"不得传播半个成员"、value 的 ASCII 限制与百分号编码、`;property` 语法
- [OpenTelemetry Specification — Trace API: SpanKind](https://github.com/open-telemetry/opentelemetry-specification/blob/main/specification/trace/api.md) — `CLIENT` 传播后通常成为远端 `SERVER` 的父，用以确认"parent-id 在跨进程时到底指向谁"
