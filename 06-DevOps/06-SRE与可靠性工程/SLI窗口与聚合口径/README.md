# SLI 窗口与聚合口径

## 简介

SLO 的所有分歧几乎都能追溯到两个"看起来只是配置"的选择:**窗口怎么切**、
**比值怎么算**。同一个服务、同一份监控数据,`rolling 30d` 与 `calendar 1M`
会给出完全不同的可靠性结论,而 `rawType` 写错一位能让 99.7% 的 SLI 被读成 0.3%。

本 demo 把这两件事做成可执行的模型:

- **窗口**:OpenSLO v1 的 `timeWindow`(rolling / calendar-aligned)+ Prometheus
  区间向量选择器的**左开右闭**语义
- **聚合**:Prometheus `extrapolatedRate` 的外推算法(逐行转写自 `promql/functions.go`)

关键概念:

| 概念 | 一句话 |
| --- | --- |
| SLI | Service Level Indicator,服务水平的量化测量,标准形态是"好事件 / 总事件" |
| rolling window | 长度恒定为 `duration`,终点永远贴着 `now` |
| calendar-aligned window | 边界锚在日历上(`startTime` + `timeZone`),长度随月份/季度变化 |
| extrapolatedRate | `rate`/`increase` 的共同内核:把观测到的增量外推到整个区间 |
| rawType | `raw` 形态 SLI 的元数据,`success` 表示存的是 good/total,`failure` 表示存的是 bad/total |

历史背景:SLI/SLO 由 Google SRE 体系推广;OpenSLO 是云原生社区(Nobl9 等发起)为
"跨工具描述 SLO"提出的开放规范,Prometheus 则是事实上的指标抓取标准。

## 原理详解

### 1. 区间向量是左开右闭的

Prometheus 文档原文:

> The range is a **left-open and right-closed** interval, i.e. samples with
> timestamps coinciding with the left boundary of the range are excluded from the
> selection, while samples coinciding with the right boundary of the range are
> included.

即 `m[60s]` 在时刻 `ts` 选的是 `(ts-60, ts]`。落在 `ts-60` 上的样本**不算**。

### 2. `rate` 与 `increase` 是同一条公式,只差一个除法

`promql/functions.go` 的 `extrapolatedRate` 流程(本 demo 逐行转写):

```text
rangeStart = ts - range          rangeEnd = ts
firstT/lastT   = 选中样本的首尾时间戳
result         = lastV - firstV   (counter 时逐段修正回绕)
durationToStart = firstT - rangeStart
durationToEnd   = rangeEnd - lastT
sampledInterval = lastT - firstT
averageDuration = sampledInterval / (n-1)
extrapolationThreshold = averageDuration * 1.1      # 关键常数 1.1

if durationToStart >= threshold: durationToStart = averageDuration / 2
    # counter 专属:再退回到"计数器值为 0 的时刻"
    durationToZero = sampledInterval * (firstV / result)
    durationToStart = min(durationToStart, durationToZero)
if durationToEnd >= threshold:   durationToEnd   = averageDuration / 2

factor = (sampledInterval + durationToStart + durationToEnd) / sampledInterval
if isRate: factor /= range.Seconds()
result *= factor
```

因此 `increase` **不是**裸差值:`increase = rate × range_seconds` 恒等,而 rate 里
带着外推。整数计数器也能给出非整数结果(文档原文: "it is possible to get a
non-integer result even if a counter increases only by integer increments")。

### 3. 计数器零点钳制是一条独立规则,不是细节

当 `durationToStart >= threshold` 触发半间隔外推后,counter 分支还会再做一次比较:
`durationToZero = sampledInterval × (firstV / result)`。这条规则的作用是**不允许把
计数器外推成负数**。它有闭式结果:钳制生效且 `durationToEnd = 0` 时

```text
increase = result + firstV
```

本 demo E4b 实测:裸差 45、首点值 10 → `increase = 55`,而不是"按 avg/2 外推"的 67.5。

### 4. 两种窗口的实质差别是长度

| | rolling 30d | calendar 1M |
| --- | --- | --- |
| 长度 | 恒定 30 天 | 28 / 30 / 31 天不定 |
| 边界 | 随 `now` 滑动 | 锚在 `startTime` 的日历倍数上 |
| 故障后的恢复 | 满 `duration` 才滑出 | 跨月即整段清零 |
| 适合 | 与"最近表现"绑定的决策 | 与自然结算周期绑定的考核 |

E8 实测(2026-03-20 10:00–12:00 一次 2 小时全站故障):

| 日期 | rolling 30d | calendar 1M |
| --- | --- | --- |
| 2026-03-21 | 0.997222 | 0.995842 |
| 2026-04-01 | 0.997222 | **1.000000** |
| 2026-04-15 | 0.997222 | 1.000000 |
| 2026-04-20 | **1.000000** | 1.000000 |

同一个故障,calendar 窗口在 4/1 就"看不见"了,rolling 要拖到 4/20。

## 对比 / 选型

| 函数 | 取点 | 外推 | 官方建议用途 |
| --- | --- | --- | --- |
| `rate` | 区间内全部 | 有 | 告警、慢变计数器(官方:"best suited for alerting") |
| `increase` | 区间内全部 | 有(同 rate) | 人类可读;`rate × 窗口秒数` 的语法糖 |
| `irate` | **仅最后两点** | 无 | 快速变化的画图;官方明确"brief changes can reset the FOR clause" |

E6 实测:尾部 2 秒突发时 `rate = 3.8/s` 而 `irate = 85/s`,相差 22.4 倍。

## 环境准备

- 操作系统:Windows / Linux / macOS 均可(纯标准库)
- Python:3.9+(用到 `datetime.fromtimestamp`,无第三方依赖)
- Go:1.21+(可选,无外部依赖)

## 运行方式

### Python

```bash
cd python
python main.py               # 打印 E1..E9 的实测数字
python selfcheck_window.py   # 断言自检,期望末行 PASS 64 / FAIL 0
```

### Go

```bash
cd go
go run .                     # 同包多文件必须用 `go run .`
```

## 关键代码片段

`prom_window.py` 中计数器回绕与零点钳制两处(对应原理详解第 2、3 步):

```python
result = last_v - first_v
if is_counter:
    for i in range(1, len(points)):
        if points[i][1] < points[i - 1][1]:
            result += points[i - 1][1]        # 回绕:加回前一个点的整个值

if duration_to_start >= extrapolation_threshold:
    duration_to_start = average_duration / 2
    if is_counter:
        duration_to_zero = duration_to_start
        if result > 0 and first_v >= 0:
            duration_to_zero = sampled_interval * (first_v / result)
        if duration_to_zero < duration_to_start:
            duration_to_start = duration_to_zero    # 不许外推成负数
```

`openslo.py` 中 calendar 窗口的月份推进(长度不恒定的来源):

```python
def _add_months(dt, months):
    y = dt.year + (dt.month - 1 + months) // 12
    m = (dt.month - 1 + months) % 12 + 1
    d = min(dt.day, _LAST_DAY[m - 1])   # 1/31 + 1M = 2/28
    return dt.replace(year=y, month=m, day=d)
```

## 性能与边界

- `select_range` 是 O( len(series) ) 线性扫描,未做二分;真实 Prometheus 用内存索引
- 窗口内样本数 < 2 时 `rate`/`increase` **返回空**(源码 `return enh.Out, annos`),
  本模型返回 `None`。单样本 + 无可用 start timestamp 同样是空,不是 0
- `Duration` 只接受规范列出的 7 个后缀;`M/Q/Y` 本实现按日历月算,但规范明确
  "does not put requirements on how (or whether) to implement each postfix",
  换实现时数值可能不同 —— 这是**口径**不是 bug
- 日历运算用 naive 墙上时间 + 月末钳制;跨 DST 的绝对时长需自行换算

## 注意事项与常见坑

1. **`rawType` 写反是最贵的一个错**:存 0.003 的 failure 比值按 success 读,SLI 从
   0.997 变成 0.003。规范把它做成必填字段正是为此 —— 但读取侧不看元数据照样会错。
2. **左开右闭会让窗口少一个点**:查询边界与采样周期对齐时,第一个样本被排除,
   外推随之变化。不要假设"窗口里有 `range/interval` 个点"。
3. **`increase` 的非整数不是 bug**:外推所致。等距采样时 factor 恰好整除会掩盖它,
   因此"我看过是整数"不能作为"没有外推"的证据(见 E3 负控)。
4. **计数器零点钳制会压低结果**:E4b 里它让结果从 67.5 降到 55。手算核对时必须
   带上这一步,否则会以为实现写错了。
5. **`irate` 不能进告警**:官方明确它会因瞬时抖动重置 `FOR` 子句。
6. **rolling 与 calendar 不可互换**:前者是"最近 30 天",后者是"本月"。拿 calendar
   的月度 SLI 去做发布门禁,会在每月 1 号凭空获得一整份预算。
7. **总量为 0 时 SLI 无定义**,本模型返回 `None`;不要静默当成 0 或 1。

## 参考资料(实际阅读过的权威来源)

- [OpenSLO v1 规范(仓库 README 即规范正文)](https://github.com/OpenSLO/OpenSLO) —
  `duration-shorthand`、`timeWindow` 的 rolling / calendar-aligned 双语分支、
  `ratioMetric` 的 `{good,total}` / `{bad,total}` / `raw` 三形态与 `rawType` 注释
- [Prometheus — Querying basics: Range Vector Selectors](https://prometheus.io/docs/prometheus/latest/querying/basics/) —
  "left-open and right-closed" 原文、`offset` 与 `@` 修饰符
- [Prometheus — Query functions](https://prometheus.io/docs/prometheus/latest/querying/functions/) —
  `rate()` / `increase()` / `irate()` 的官方定义与用途建议
- [Prometheus 源码 `promql/functions.go`](https://github.com/prometheus/prometheus/blob/main/promql/functions.go) —
  `extrapolatedRate` 全文(本 demo 的转写对象,含 `1.1` 阈值、半间隔退化、零点钳制)
