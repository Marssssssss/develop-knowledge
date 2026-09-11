# 分布式追踪与 W3C Trace Context

## 简介

- 分布式追踪把一次跨服务请求的全部 Span 关联成一棵树；**OpenTelemetry 定义 Trace 为 Span 的有向无环图（DAG）**，边即 parent/child 关系。
- 关键概念：
  - **Span**：一次操作单元（name、trace_id、span_id、parent_id、起止时间、attributes、status）
  - **traceparent**：W3C Trace Context 必选传播头 `version-trace-id-parent-id-trace-flags`
  - **tracestate**：可选的厂商扩展键值列表（最多 32 项，最左侧对应 traceparent 的书写方）
  - **上下文传播（Context Propagation）**：OTel 分布式追踪的核心——Context 携带 ID 关联信息，Propagation 负责跨进程序列化/反序列化
- OpenTelemetry 的 SpanContext 明确遵循 W3C TraceContext 规范（TraceId 非零 u128 / SpanId 非零 u64 / sampled 标志位）。

## 原理详解

### traceparent 头（W3C REC trace-context-1, 2020-02-06）

ABNF（version 00）：

```text
value    = version "-" version-format
version  = 2HEXDIGLC            ; 假定 00，ff 禁止
version-format = trace-id "-" parent-id "-" trace-flags
trace-id   = 32HEXDIGLC         ; 16 字节，全零非法
parent-id  = 16HEXDIGLC         ; 8 字节，全零非法
trace-flags = 2HEXDIGLC         ; 目前仅最低位 sampled 有定义
```

四个字段：

| 字段 | 长度 | 语义 | 非法值 |
| --- | --- | --- | --- |
| version | 2 hex | 格式版本，当前 00 | `ff`（255）禁止 |
| trace-id | 32 hex | 整条 trace 的全局唯一 ID，**全链路不变** | 全零 |
| parent-id | 16 hex | 当前请求的 span ID（调用方视角），**每跳更新** | 全零 |
| trace-flags | 2 hex | 位域：bit0 = sampled | — |

### 校验规则（规范 MUST 条款）

1. `version` 为 `ff` → 整个 traceparent 非法。
2. `trace-id` 全零 / 含非小写 hex 字符 → **vendors MUST ignore**（忽略整个 traceparent，重新开新 trace）。
3. `parent-id` 全零 / 非法字符 → 同上 MUST ignore。
4. version > 00 且后续字段符合 `32HD-16HD-2HD` 布局 → **按 version 0 处理**（向前兼容）。
5. 校验失败时，IBM MQ 等实现的做法是**同时剥离 traceparent 与 tracestate**，且不产生 span。

### 逐跳传播流程（本 demo 的模拟链路）

```text
client ──HTTP──► svc-A ──HTTP──► svc-B ──HTTP──► svc-C
  │ 注入            │ 提取+校验       │ 提取+校验      │ 提取+校验
  │ tp: 00-T-P0-01 │ 开 span A      │ 开 span B     │ 开 span C
  ▼                ▼ 注入 tp:00-T-Pa-01 (parent-id 换成 A 的 span_id)
收集端拿到 Span 列表 {trace_id, span_id, parent_id} → 按 parent_id 建树还原调用链

client(root)                       [trace_id=T 全程不变]
└── svc-A  (parent=P0, span=Pa)
    └── svc-B (parent=Pa, span=Pb)
        └── svc-C (parent=Pb, span=Pc)
```

### trace-flags 位域掩码（规范给出的正解）

```java
static final byte FLAG_SAMPLED = 1;   // 00000001
boolean sampled = (traceFlags & FLAG_SAMPLED) == FLAG_SAMPLED;
```

- 常见错误：`traceFlags == 0x01` 判等——`0x03`（sampled + random bit）会被误判为未采样。
- 规范强调 flags 是**调用方建议而非强制规则**（防滥用、调用方 bug、负载差异降采样三种动机）。

## 对比 / 选型

| 传播格式 | 形态 | 现状 |
| --- | --- | --- |
| W3C Trace Context | `traceparent` + `tracestate` 双头 | **W3C 正式推荐标准**（2020-02 REC），OTel/主流云平台/服务网格原生支持 |
| B3 (Zipkin) | `X-B3-TraceId` 等多头 | 存量系统，逐步被 W3C 取代 |
| X-Cloud-Trace-Context / X-Amzn-Trace-Id | 单头专有 | Google/AWS 专有，网关侧做转换 |

## 环境准备

- 操作系统：任意（in-memory 模拟 HTTP 头传递，无网络依赖）
- 语言版本：C99 / Python 3.8+ / Go 1.20+
- 依赖：无

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -o trace_demo c/trace_demo.c && ./trace_demo
```

### Python

```bash
python3 python/trace_demo.py
```

### Go

```bash
cd go && go run trace_demo.go
```

## 关键代码片段（Python 版）

```python
def parse_traceparent(tp: str):
    """校验 version-32hex-16hex-2hex；全零/ff/大写均非法（MUST ignore）。"""
    parts = tp.split("-")
    if len(parts) != 4 or len(parts[0]) != 2 or len(parts[1]) != 32 \
            or len(parts[2]) != 16 or len(parts[3]) != 2:
        return None
    for p in parts:
        if any(c not in "0123456789abcdef" for c in p):
            return None                       # HEXDIGLC：仅小写
    if parts[0] == "ff":
        return None                           # version ff 禁止
    if parts[1] == "0" * 32 or parts[2] == "0" * 16:
        return None                           # trace-id / parent-id 全零非法
    return parts[0], parts[1], parts[2], int(parts[3], 16)

def is_sampled(flags: int) -> bool:
    return (flags & 0x01) == 0x01             # 位掩码，禁止 flags == 1 判等
```

## 性能与边界

- traceparent 解析是 O(48) 常数操作，无性能敏感点；真正的成本在**采样**（未采样的 trace 不落存储）。
- trace-id 全局唯一性：规范建议**至少最右 7 字节随机生成**（兼顾隐私与碰撞概率）。
- tracestate 最多 32 个成员；成员总和超限时按规范丢弃最右（最老）项。
- 同一进程内 Span 上下文不该手动传递（OTel 用隐式 Context），跨进程才必须显式注入/提取。

## 注意事项与常见坑

1. **flags 判等 vs 掩码**：`flags == 0x01` 会漏掉 `0x03`；必须 `(flags & 0x01) == 0x01`（规范原文以 Java 示例强调）。
2. **hex 大小写**：ABNF 是 `HEXDIGLC`（小写）；发送方 MUST 小写，接收方遇大写按非法处理（IBM MQ 校验表明确 `0-9a-f`）。
3. **version > 00 的前向兼容**：不能直接丢弃未知版本；只要布局匹配 `2HD-32HD-16HD-2HD` 就按 version 0 解释，多余尾部内容可忽略。
4. **忽略 ≠ 报错**：traceparent 非法时 MUST 静默忽略并当作没有上游上下文（新建 trace），不能让请求失败。
5. **parent-id 语义**：它是"调用方 span 的 ID"，即下游 span 的 parent；每跳服务要用**自己的新 span_id** 替换后再传出。
6. **OTel span 命名低基数**：`get_account` 合理，`get_account/42` 高基数反模式（官方 API 文档明示）。
7. **summary 类分位数不可聚合，trace 不受此限**：trace 是原始事件级数据，聚合发生在查询端（与 metrics 的 histogram 对比见 demo 053）。

## 参考资料（实际阅读过的权威来源）

- [W3C Trace Context Level 1（REC 2020-02-06）](https://www.w3.org/TR/2020/REC-trace-context-1-20200206) — traceparent ABNF、全零非法、flags 位掩码 Java 示例、tracestate 语义全文
- [OpenTelemetry Overview Specification](https://opentelemetry.io/docs/specs/otel/overview/) — 信号模型、Trace=Span 的 DAG、TracerProvider/Tracer/Exporter 分层
- [OpenTelemetry Traces 概念文档](https://opentelemetry.io/docs/concepts/signals/traces) — Span 字段清单（name/parent_id/timestamps/context/attributes/links/status）、Context 与 Propagation 两子概念
- [IBM MQ OpenTelemetry trace context 文档](https://www.ibm.com/docs/sk/SSFKSJ_9.4.0/monitor/opentelemetry_tracecontext_zos.html) — 生产级 traceparent 校验规则表（HD 布局/ff/全零）与失败剥离行为
- [Traceparent — http.dev](https://http.dev/traceparent) — 与专有头（X-B3/X-Cloud-Trace-Context）的替代关系
