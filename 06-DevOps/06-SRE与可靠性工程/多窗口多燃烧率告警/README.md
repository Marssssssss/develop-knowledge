# 多窗口多燃烧率告警

## 简介

"错误率超过 1% 就告警"这类静态阈值有个致命缺陷:它既抓不住慢燃(0.12% 的错误率
25 天就能烧光预算,但永远到不了 1%),又会在故障恢复后继续响(长窗里的旧账还没滑出去)。

多窗口多燃烧率(**multiwindow multi-burn-rate,MWMB**)用两个窗口解决这两个问题:

- **长窗**决定"这个问题有多严重"(对应消耗了多少预算)
- **短窗**决定"这个问题还在不在"(故障恢复了就立刻停)

关键概念:

| 概念 | 一句话 |
| --- | --- |
| 短窗 / 长窗 | 同一档位配两个窗口,**都要破线**才告警 |
| quick / slow | 快燃(page)与慢燃(ticket)两个分支,之间是 **or** |
| 告警阈值 | `燃烧率 × (1 − SLO)`,比的是**错误率**不是燃烧率 |
| `alertAfter` | OpenSLO 里等价的短窗概念:条件需持续这么久才告警,默认 `0m` |

## 原理详解

### 1. 告警表达式:`(短 and 长) or (短 and 长)`

`slok/sloth` 的 `internal/plugin/slo/core/alert_rules_v1/plugin.go` 里
`mwmbAlertTpl` 的模板(本 demo 照它渲染):

```promql
(
    max(slo:sli_error:ratio_rate[5m]  > (14.4 * 0.001)) without (sloth_window)
    and
    max(slo:sli_error:ratio_rate[1h]  > (14.4 * 0.001)) without (sloth_window)
)
or
(
    max(slo:sli_error:ratio_rate[30m] > (6 * 0.001)) without (sloth_window)
    and
    max(slo:sli_error:ratio_rate[6h]  > (6 * 0.001)) without (sloth_window)
)
```

三个必须记住的结构性事实:

1. **短窗与长窗是 `and`** —— 只破一个窗不告警
2. **quick 与 slow 是 `or`** —— 一条规则同时覆盖快燃与慢燃
3. **右侧是 `燃烧率 × 错误预算率`** —— 比的是错误率,不是燃烧率

### 2. 短窗为什么存在

sloth 源码注释原文:

> ShortWindow is the small window used on the alerting part to **stop alerting during
> a long window because we consumed a lot of error budget but the problem is already
> gone**.

实测(E3):一次 10 分钟 100% 故障,之后完全恢复

| 告警方式 | 告警区间 |
| --- | --- |
| MWMB(5m 且 1h) | 第 0 ~ 13 分钟 |
| 只看 1h 长窗的静态阈值 | 第 0 ~ 68 分钟 |

**提前 55 分钟叫停**。负控:把短窗拉长到与长窗同宽,MWMB 立刻退化成静态(停止时刻
变回 68)—— 证明这 55 分钟确实来自短窗,不是别的地方。

### 3. 四档阈值(SLO 99.9% / 30d)

| 档位 | 预算% | 短窗 | 长窗 | 燃烧率 | 错误率阈值 |
| --- | --- | --- | --- | --- | --- |
| page_quick | 2 | 5m | 1h | 14.4 | 0.0144 |
| page_slow | 5 | 30m | 6h | 6 | 0.006 |
| ticket_quick | 10 | 2h | 1d | 3 | 0.003 |
| ticket_slow | 10 | 6h | 3d | 1 | 0.001 |

### 4. 慢燃:静态阈值的盲区

实测(E4):持续 0.12% 的错误率,燃烧率 1.2,25 天烧光整期预算

| 判据 | 结果 |
| --- | --- |
| 静态阈值 1% | **不告警(漏报)** |
| page_quick / page_slow / ticket_quick | 不告警 |
| ticket_slow(阈值 0.001) | **告警** |

### 5. 分界点可手算

10 分钟故障要多狠才 page?长窗 1h 的均值是 `r × 10/60`,破线条件是
`r × 10/60 > 0.0144`,即 **`r > 0.0864`**。实测(E5):

| 故障错误率 | 5m 均值 | 1h 均值 | 1h 内耗预算 | 判定 |
| --- | --- | --- | --- | --- |
| 0.0800 | 0.064 | 0.013333 | 1.85% | 不告警 |
| 0.0864 | 0.069 | 0.014400 | 2.00% | **不告警**(严格 `>`) |
| 0.0900 | 0.072 | 0.015000 | 2.08% | page |

注意 `0.0864` 那一行:5m 短窗早已破线,**卡住的是长窗**;且因为比较是严格 `>`,
恰好等于阈值时不告警。这也说明"1 分钟内全站挂掉"确实会 page —— 它在 1 小时窗口里
花掉了 2.3% 预算,超过了 2% 的门槛,是设计使然。

## 对比 / 选型

| | 静态阈值 | MWMB |
| --- | --- | --- |
| 慢燃(0.12% 持续) | 漏报 | ticket 档抓住 |
| 故障恢复后 | 继续响到长窗滑完 | 短窗立刻叫停(本例早 55 分钟) |
| 阈值含义 | 拍脑袋的百分数 | "多长时间内花掉多少预算" |
| 配置项 | 1 个 | 4 档 × (预算%, 短窗, 长窗) |

## 环境准备

- Python 3.9+ / Go 1.21+,零第三方依赖
- 操作系统不限

## 运行方式

### Python

```bash
cd python
python main.py               # E1..E8 实测数字
python selfcheck_mwmb.py     # 断言自检,期望末行 PASS 67 / FAIL 0
```

### Go

```bash
cd go
go run .                     # 同包多文件必须用 `go run .`
```

## 关键代码片段

`mwmb.py` 里"短窗与长窗都要破线"的判定(对应原理详解第 1 步):

```python
def fires(rate_short, rate_long, threshold, op="gt"):
    """单档判定:短窗与长窗都破线才算。模板用的是严格 `>`。"""
    if op == "gt":
        return rate_short > threshold and rate_long > threshold
    if op == "gte":
        return rate_short >= threshold and rate_long >= threshold
    raise ValueError("op must be gt or gte")
```

窗口均值沿用 Prometheus 的左开右闭(与 demo 「SLI 窗口与聚合口径」同源):

```python
for i, rate in enumerate(timeline):
    tt = i * step_s
    if lo < tt <= t:          # (t-window, t]
        vals.append(rate)
```

## 性能与边界

- 判定是 O(档位数)= 4;时间线扫描是 O(时间线长度 × 窗口数),1 分钟步长、3 天
  时间线约 4320 × 7 次
- 窗口内**无样本**时本模型返回 `None` 表示"无法判定",不是"错误率为 0"。
  OpenSLO 为此专门给了 `alertWhenNoData` 开关,真实系统要显式选择
- 告警阈值是 `燃烧率 × (1 − SLO)`,浮点上 `14.4 × (1 − 0.999)` 得到
  `0.014400000000000013`,**不等于字面量 0.0144**。写断言时别用字面量比相等
- 周期改了必须重算:28d 的 page_quick 阈值是 0.01344,7d 是 0.00336

## 注意事项与常见坑

1. **短窗不是"更灵敏",是"会叫停"**。很多人以为加短窗是为了更快发现,实际它主要
   作用是让告警在恢复后尽快消失;灵敏度由长窗与预算百分比共同决定。
2. **`and` 写错成 `or` 会让告警量翻倍**:故障恢复后长窗还在破线,短窗已归零,`or`
   会继续响满整个长窗。
3. **阈值乘的是预算率不是预算百分比**:`14.4 × 0.001` 不是 `14.4 × 0.1`。
4. **严格 `>` 意味着"恰好等于阈值不告警"**。做边界测试时要拿算出来的阈值本身比,
   拿字面量 `0.0144` 比会因为浮点偏小而永远不相等 —— 边界根本没被验到。
5. **短脉冲也会 page**:1 分钟全挂 = 1 小时窗口里花掉 2.3% 预算。若不接受,应调
   整 `page_quick` 的预算百分比或长窗,而不是加"最少持续 N 分钟"的额外条件。
6. **无数据不等于健康**:本模型返回 `None` 且不参与判定;如果直接当 0,监控本身
   挂掉时反而"一切正常"。

## 参考资料(实际阅读过的权威来源)

- [slok/sloth — `internal/plugin/slo/core/alert_rules_v1/plugin.go`](https://github.com/slok/sloth/blob/main/internal/plugin/slo/core/alert_rules_v1/plugin.go) —
  `mwmbAlertTpl` 模板全文、`defaultSLOAlertGenerator` 的 `ErrorBudgetRatio` 取值、
  page/ticket 两组规则的生成分支
- [slok/sloth — `internal/alert/window.go`](https://github.com/slok/sloth/blob/main/internal/alert/window.go) —
  `ShortWindow` 的用途注释原文、`Windows` 的 page/ticket × quick/slow 四档结构
- [slok/sloth — `internal/alert/windows/google-30d.yaml` / `google-28d.yaml`](https://github.com/slok/sloth/tree/main/internal/alert/windows) —
  四档长短窗数值,文件头注明取自 `sre.google/workbook/alerting-on-slos`
- [OpenSLO v1 规范 — `AlertCondition`](https://github.com/OpenSLO/OpenSLO) —
  `kind: burnrate` 时的 `lookbackWindow`(长窗)与 `alertAfter`(默认 `0m`)字段,
  以及 `AlertPolicy` 的 `alertWhenNoData` / `alertWhenBreaching` / `alertWhenResolved`
