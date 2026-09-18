# PromQL 范围向量与 rate/increase 外推

## 简介

PromQL 是 Prometheus 的函数式查询语言。它把「当前值」与「一段时间窗内的样本」分成两种
向量类型——**即时向量**与**范围向量**——而 `rate()` / `increase()` 这类函数正是把范围向量
折算成一个数的桥梁。

- **即时向量（instant vector）**：每条序列一个样本，全部共享同一时间戳。
- **范围向量（range vector）**：每条序列带一串样本，是 `[5m]` 这类后缀的产物。
- **lookback（默认 5m）**：即时选择器取「求值时刻之前或恰在该时刻」的最新样本，但该样本
  若比 lookback 更早，整条序列就不返回。
- **staleness 标记**：目标不再暴露某序列时被显式标记，标记后的查询不再返回该序列。
- **外推（extrapolation）**：`rate` 把窗口内的增量按比例放大到窗口两端，用来补偿漏抓
  （missed scrapes）与抓取周期和窗口不对齐。

这三条合起来解释了一个常被吐槽的现象：**同一个 0.1/s 的计数器，因末样本离窗口右边界远近
不同，`rate()` 可以给出 0.1 或 0.07**。本 demo 把这套语义拆成可实跑的最小实现。

## 原理详解

### 1. 采样时刻与实测样本无关

查询在给定时间戳上求值，而样本只可能落在固定抓取周期上。两者不重合时，Prometheus 只能
「取该时刻之前最新的、且不过期的那一个样本」。因此：

- 取的样本满足 `t <= eval_t`（未来样本不可见）；
- 还要求 `eval_t - t < lookback`，**严格小于**——恰好等于 300s 时不返回。

### 2. 范围向量是左开右闭区间

官方原文：`The range is a left-open and right-closed interval, i.e. samples with
timestamps coinciding with the left boundary of the range are excluded, while samples
coinciding with the right boundary of the range are included.`

即 `[5m]` 在 `t=300` 求值得区间 `(0, 300]`：`t=0` 的样本被排除、`t=300` 的被包含。这条
规则直接决定了外推里的 `durationToStart` 至少要等于一个抓取周期。

### 3. 外推算法（`extrapolatedRate`）

```text
rangeStart        first.t        last.t        rangeEnd
     |                |            |              |
     |<--dStart------->|<--sampled-->|<---dEnd---->|
     |                                              |
     |<------------ 被外推到的区间 ----------------->|
```

```text
sampledInterval           = last.t - first.t
avgDurationBetweenSamples = sampledInterval / (n - 1)
durationToStart           = first.t - rangeStart
durationToEnd             = rangeEnd - last.t
extrapolationThreshold    = avgDurationBetweenSamples * 1.1

# 任一端距边界过远（>= 阈值）→ 最多只外推「平均样本间隔的一半」
extrapolateToInterval = sampledInterval + f(durationToStart) + f(durationToEnd)
factor                = extrapolateToInterval / sampledInterval
rate     = rawDelta * factor / (rangeEnd - rangeStart)
increase = rawDelta * factor            # 官方：increase 是 rate × 窗口秒数的语法糖
```

`1.1` 这个系数是给噪声留的余量：只要样本距边界不那么远，就认为「本来还会再有一个样本」。

### 4. 计数器重置的补偿

计数器在进程重启时归零，会让 `last - first` 变成负数。做法是遍历窗口，遇到
`v[i] < v[i-1]` 就把重置前的高度 `v[i-1]` 加回去，等价于假设「重置瞬间归零后重新计数」。
实测 `5, 10, 2, 7` 得 `raw = (7-5) + 10 = 12`，而不补偿只有 `2`——**差 6 倍**。

### 5. 计数器零点截断

计数器不可能为负。若序列有上升趋势，可反推「零点」位置
`durationToZero = sampledInterval * (first.v / rawDelta)`；若它比 `durationToStart` 更近，
就把序列起点挪到零点，避免把结果外推到负值区。

## 对比 / 选型

| 维度 | `rate` / `increase` | `irate` / `idelta` | `delta` |
| --- | --- | --- | --- |
| 输入样本 | 窗口内全部 | 仅最后两个 | 窗口内全部 |
| 边界外推 | 有（覆盖到窗口两端） | 无 | 有 |
| 计数器重置 | 自动修正 | 自动修正 | 不适用（gauge） |
| 结果类型 | 每秒速率 / 总增量 | 瞬时每秒速率 | 首尾差 |
| 官方定位 | 告警、缓慢变化的计数器 | 绘制剧烈波动的计数器 | gauge |
| 关键陷阱 | 窗口不足 2 点时无值 | 短暂波动会重置 `FOR` 子句 | 用错在计数器上会得出负数 |

## 环境准备

- 操作系统：任意（纯标准库）
- Python 3.9+（本机 3.13.12 实测）
- Go 1.21+（本机无 go 工具链，Go 版走人工代码审查 + 结构校验）
- C99（本机无 gcc，C 版走人工审查；仅用到 `math.h`）

## 运行方式

### Python（本机实跑，51 项断言全绿）

```bash
cd python && python demo.py     # 退出码 0 = 全部通过
```

### Go

```bash
cd go && go run .
```

### C

```bash
cd c && gcc -O2 -Wall -Wextra -pedantic promql_demo.c -lm -o promql_demo && ./promql_demo
```

## 关键代码片段

```python
# promql_engine.py —— 外推核心（省略零点截断与分支顺序参数）
sampled_interval = points[-1].t - points[0].t
duration_to_start = points[0].t - range_start
duration_to_end = range_end - points[-1].t
avg_between = sampled_interval / (len(points) - 1)
threshold = avg_between * 1.1                    # 1.1 是噪声余量
extrapolate_to = sampled_interval
# 注意判据是 >=（恰等于阈值就走降级分支），见「常见坑」
extrapolate_to += duration_to_start if duration_to_start < threshold else avg_between / 2
extrapolate_to += duration_to_end if duration_to_end < threshold else avg_between / 2
factor = extrapolate_to / sampled_interval
if is_rate:
    factor /= (range_end - range_start)
return raw_delta(points, is_counter) * factor
```

```python
# promql_store.py —— lookback 是**严格小于**，恰好等于不返回
if eval_t - t >= self.lookback:
    return None
```

## 性能与边界

- **窗口内少于 2 个样本 → 不返回该元素**（不是返回 0）。抓取间隔 60s 配 `[1m]` 只剩 1～2 点，
  这是「`rate` 结果时有时无」的最常见原因。
- 端样本距边界 `>= 1.1 × 平均样本间隔` 时，外推量从「实际空隙」降级为「半个平均间隔」，
  结果系统性偏小。实测同一 0.1/s 计数器：末样本距右边界 60s → `0.1000`；距 120s → `0.0700`
  （低估 **30%**）。
- `increase` 与 `rate` 只差一个窗口秒数的因子，官方明确说 `increase` 是「为人类可读性」的
  语法糖，**记录规则里应写 `rate`**，以便按每秒一致地跟踪。
- 与聚合结合时必须「先 `rate` 再 `sum`」：否则目标重启造成的计数器重置无法被识别。

## 注意事项与常见坑

1. **判据是 `>=` 而非 `>`**：`durationToEnd` 恰好等于阈值的求值点会走「降级为 avg/2」分支。
   自检把末样本放在 `t=234`（`avg=60` → 阈值 `66`，`durationToEnd` 恰为 `66`）钉死此边界：
   `factor = 264/180` 而不是 `300/180`，`rate` 从 `0.10` 掉到 `0.088`。**平局规则必须显式写死**。
2. **两个分支的先后顺序在资料中存在分歧**。官方文档只说明「外推存在、用于补偿漏抓与不对齐」，
   **没有规定**「超阈值降级」与「计数器零点截断」谁先谁后。当
   `avg/2 < durationToZero < threshold` 时两者结论不同：实测同一组样本
   `rate` 得 `0.0888888889`（先降级）vs `0.0944444444`（先截断），差 **6.25%**。
   Go 版用 `order` 参数同时实现两条路径并断言二者确实不同。
3. **`offset` / `@` 必须紧贴选择器**：`sum(x) offset 5m` 非法，`sum(x offset 5m)` 合法；
   `rate(x[5m]) offset 5m` 非法，`rate(x[5m] offset 5m)` 合法。`@` 与 `offset` 可交换书写
   顺序（offset 相对 `@` 时刻生效），结果完全相同。
4. **负 offset 会看向未来**：`offset -1w` 允许查询求值时刻之后的数据，做同比很方便，但也
   意味着「历史」不一定已定稿。
5. **选择器合法性**：必须给出指标名，或至少一个**不匹配空值**的 matcher。
   故 `{job=~".*"}`、`{job!="x"}`、`{__name__=~".*"}` 全部非法；而 `{job=~".+"}`、
   `{job!=""}`、`{job=~".*",method="get"}` 合法。`job!="x"` 之所以非法，是因为空值 `""`
   也满足 `!= "x"`——「匹配空值」的判定要按**值语义**而不是字面量是否为空来想。
6. **正则完全锚定**：`env=~"foo"` 等价于 `env=~"^foo$"`，不会匹配 `foobar`。
7. **缺失标签 ≡ 空值标签**：`http_requests_total{environment=""}` 会命中所有**没有**
   `environment` 标签的序列，并排除 `environment="development"` 的那条。
8. **staleness 会切断范围向量**：序列被标记后再求 `rate` 不会把标记前的老样本算进来。

## 参考资料（实际阅读过的权威来源）

- [Querying basics | Prometheus](https://prometheus.io/docs/prometheus/latest/querying/basics/)
  — 四种表达式类型、即时/范围选择器、lookback 5m 与 staleness、左开右闭区间、`offset`/`@`
  修饰符、子查询语法、正则锚定与「不匹配空值」的选择器合法性规则（本 demo A～F 组的直接依据）。
- [Query functions | Prometheus](https://prometheus.io/docs/prometheus/latest/querying/functions/)
  — `rate` / `irate` / `increase` / `delta` / `idelta` / `deriv` / `predict_linear` / `resets` /
  `changes` 的官方定义，含「外推到时间范围两端」「计数器重置自动修正」「increase 是 rate ×
  窗口秒数的语法糖」「先 rate 再聚合」等原文。
- 经 WebSearch 检索到的 `promql/functions.go` 中 `extrapolatedRate` 源码摘录（三处独立转载互校：
  changchen.me 的《[源码分析] PromQL 中的 rate 与 irate 方法》、SegmentFault 的《Prometheus rate
  函数算法》、天翼云开发者社区的《promQL详解》）—— `extrapolationThreshold = avg * 1.1`、
  `durationToZero` 截断、`durationToStart/End >= threshold` 时替换为 `avg/2` 等分支细节。
  **注**：直接抓取 `raw.githubusercontent.com/prometheus/prometheus/main/promql/extrapolation.go`
  返回 404（该文件在 main 分支已改路径），故上述分支口径以三处互校的源码摘录为准，并在
  README 与代码注释中标注「分支顺序未在官方文档中规定」这一分歧。
