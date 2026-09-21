# 错误预算与燃烧率

## 简介

错误预算把"要多可靠"变成一条可以花的账:预算 = `1 − SLO`,花完就得停下来修可靠性。
燃烧率(burn rate)则是这张账的**花销速度** —— 它把"现在的错误率"翻译成"多久会把
一整个周期的预算烧光",从而让告警不再依赖拍脑袋的静态阈值。

本 demo 拆开两件容易被混为一谈的事:

- **预算怎么算**:OpenSLO v1 的三种 `budgetingMethod`(Occurrences / Timeslices /
  RatioTimeslices)在同一份数据上可以差出 0.989 与 0.083 这种量级
- **速度怎么算**:`slok/sloth` 从 Google SRE workbook 落地的 `getBurnRateFactor`

关键概念:

| 概念 | 一句话 |
| --- | --- |
| error budget | `1 − SLO`,允许"不可靠"的比例;99.9% / 30 天 = 43.2 分钟 |
| burn rate | 实际错误率 ÷ 错误预算率;`1` 表示恰好按 SLO 的速度花预算 |
| time to exhaustion | `周期 ÷ 燃烧率`,按当前速度烧光整期预算所需时间 |
| budgetingMethod | 把"事件流"聚合成一个 SLI 数字的三种口径 |

## 原理详解

### 1. 预算的算术:每多一个九,少一个数量级

`预算 = (1 − SLO) × 周期`。实测(本 demo E1):

| SLO | 30 天 | 28 天 | 7 天 |
| --- | --- | --- | --- |
| 99% | 432 min | 403.2 min | 100.8 min |
| 99.9% | **43.2 min** | 40.32 min | 10.08 min |
| 99.99% | 4.32 min | 4.032 min | 1.008 min |

### 2. 燃烧率:SLO 无关的速度,SLO 有关的阈值

```text
burn_rate = 实际错误率 / (1 − SLO)
```

这条式子**不含 SLO 周期**,所以"燃烧率 14.4"在任何周期下都是同一个意思:按 14.4 倍
的速度花预算。但**告警阈值**必须换算回错误率空间:

```text
告警阈值(错误率) = burn_rate × (1 − SLO)
```

实测(E6):page_quick 的燃烧率恒为 14.4,但 SLO 99% 时阈值是 0.144、99.9% 时是
0.0144、99.99% 时是 0.00144 —— 差 100 倍。

### 3. `getBurnRateFactor`:把"预算百分比 + 长窗"翻译成燃烧率

`slok/sloth` 的 `internal/alert/window.go`(逐行转写):

```text
hoursRequiredConsumption = errorBudgetPercent × totalWindow.Hours() / 100
speed = hoursRequiredConsumption / consumptionWindow.Hours()
```

源码注释原文:

> Error budget speeds based on a full time window, however once we have the factor
> (speed) the value can be used with any time window.

同文件记录了 Google 在 SRE workbook 给的默认值:`Page quick 2% / Page slow 5% /
Ticket quick 10% / Ticket slow 10%`。配 sloth 自带的 `google-30d.yaml`
(长窗 1h / 6h / 1d / 3d)算出来:

| 档位 | 预算% | 长窗 | 燃烧率(30d) | 燃烧率(28d) |
| --- | --- | --- | --- | --- |
| page_quick | 2 | 1h | **14.4** | 13.44 |
| page_slow | 5 | 6h | **6** | 5.6 |
| ticket_quick | 10 | 1d | **3** | 2.8 |
| ticket_slow | 10 | 3d | **1** | 28/30 ≈ 0.9333 |

注意 28 天周期不是"约等于"30 天:燃烧率整体按 28/30 缩放。**周期改了必须重算因子。**

### 4. 烧光时间:两条独立推导必须合上

```text
TTE = 周期 / 燃烧率                (定义式)
TTE = 长窗 / (预算百分比 / 100)    (由"花 pct% 用了长窗这么久"反推)
```

实测(E5):page_quick 两条路都给出 50 小时;page_slow 120h;ticket_quick 240h;
ticket_slow 720h(= 整整一个周期,因为它的燃烧率就是 1)。

### 5. 三种 budgetMethod 的差别是**加权方式**

OpenSLO v1 原文:

> Occurrences method uses a ratio of counts of good events to the total count of the
> events. Timeslices method uses a ratio of good time slices to total time slices in
> a budgeting period. RatioTimeslices method uses an average of all time slices'
> success ratios in a budgeting period.

实测(E7):12 个切片,11 个是 1/2、1 个是 1000/1000,`timeSliceTarget = 0.99`:

| 口径 | 结果 | 为什么 |
| --- | --- | --- |
| Occurrences | 0.989237 | 按事件数加权(1011/1022),大切片把烂切片稀释掉 |
| Timeslices | 0.083333 | 先按 target 二值化,11 个切片不过线,只有 1/12 |
| RatioTimeslices | 0.541667 | 切片比值等权平均(6.5/12) |

## 对比 / 选型

| 口径 | 加权 | 对突发流量 | 适合 |
| --- | --- | --- | --- |
| Occurrences | 按事件数 | 大流量切片主导 | 请求数稳定、关心"用户请求"的比例 |
| Timeslices | 切片等权 + 二值化 | 每个切片一票 | 关心"有多少时间是不可用的" |
| RatioTimeslices | 切片等权 + 连续值 | 每个切片一票但不二值化 | 想避开 target 的悬崖效应 |

**三种口径收敛的唯一条件是切片比值只取 0 或 1**(E8a 实测:99 个全好切片 + 1 个
全坏切片,三者都等于 0.99)。把同样的 99% 摊到每个切片(每个 99/100),Timeslices
立刻跳到 **1.0** —— 因为每个切片都过线了。反过来,流量完全均匀但比值在 target 附近
抖动(98/99/100/97/99)时,Timeslices 是 0.6 而另两者是 0.986。

**结论:决定 Timeslices 的不是流量是否均匀,而是 target 与切片比值的相对位置。**

## 环境准备

- Python 3.9+ / Go 1.21+,均**零第三方依赖**
- 操作系统不限

## 运行方式

### Python

```bash
cd python
python main.py               # E1..E9 实测数字
python selfcheck_budget.py   # 断言自检,期望末行 PASS 79 / FAIL 0
```

### Go

```bash
cd go
go run .                     # 同包多文件必须用 `go run .`
```

## 关键代码片段

`budget.py` 里燃烧率因子的转写(对应原理详解第 3 步):

```python
def burn_rate_factor(error_budget_percent, slo_period_s, consumption_window_s):
    if consumption_window_s <= 0:
        raise ValueError("consumption window must be positive")
    hours_required = error_budget_percent * (slo_period_s / HOUR) / 100.0
    return hours_required / (consumption_window_s / HOUR)
```

`method.py` 里 Timeslices 的二值化(三种口径分道扬镳的地方):

```python
ratios = slice_ratios(good_counts, total_counts)
usable = [r for r in ratios if r is not NO_DATA]   # 0 事件的切片直接跳过
good = sum(1 for r in usable if r >= time_slice_target)
return good / len(usable)
```

## 性能与边界

- 全部 O(切片数),无外部 IO;真实系统里切片数 = 周期 / `timeSliceWindow`
- `budget_consumed` 是**比例**不是百分比,取值可以远大于 1(预算超支)
- `time_to_exhaustion(0)` 返回 `+Inf`;Go 侧必须写 `math.Inf(1)`,写 `1.0/0.0` 是
  编译期错误(常量除零)
- SLO 取值域 `[0, 1)`:`1.0` 无预算(除零),负数无意义,均抛 `ValueError`
- `timeSliceTarget` 取值域 `(0.0, 1.0]`(OpenSLO 规范给的开闭区间)
- 切片内 0 个事件时比值**无定义**,本模型用 `NO_DATA` 跳过 —— 既不按 0 也不按 1 计

## 注意事项与常见坑

1. **不要把燃烧率直接当告警阈值**:告警比的是错误率,必须乘 `(1 − SLO)`。SLO 从
   99.9% 提到 99.99%,同一个燃烧率对应的阈值差 10 倍。
2. **改周期要重算因子**:30d 与 28d 的 page_quick 分别是 14.4 与 13.44。很多团队
   照抄 14.4 到 28 天周期上,等于把告警放宽了 7%。
3. **三种 budgetMethod 不能互相验证**:拿 Occurrences 的 0.989 去核对 Timeslices
   的 0.083 会以为某一方算错了 —— 它们本来就在回答不同的问题。
4. **Timeslices 有悬崖效应**:`target` 从 0.5 提到 0.9,本例结果从 1.0 直接掉到
   0.083。选 target 时要看切片比值的实际分布,不要照抄。
5. **0 事件的切片别当 0 处理**:既不能算失败也不能算成功。本模型跳过它,但真实系统
   里如果跳过,一个"完全没流量"的时段会从分母里消失,静默抬高 SLI。
6. **`time_to_exhaustion` 只在燃烧率恒定外推时成立**,真实故障是突发的,它给的是
   "如果继续这样烧"的下限参考,不是承诺。

## 参考资料(实际阅读过的权威来源)

- [slok/sloth — `internal/alert/window.go`](https://github.com/slok/sloth/blob/main/internal/alert/window.go) —
  `getBurnRateFactor` 全文、`Window`/`Windows` 结构与 `Validate()` 三条硬校验、
  Google 默认预算百分比的源码注释
- [slok/sloth — `internal/alert/windows/google-30d.yaml` 与 `google-28d.yaml`](https://github.com/slok/sloth/tree/main/internal/alert/windows) —
  四档的长窗/短窗实测数值,文件头注明来源为
  `https://sre.google/workbook/alerting-on-slos/#recommended_parameters_for_an_slo_based_a`
- [OpenSLO v1 规范(仓库 README 即规范正文)](https://github.com/OpenSLO/OpenSLO) —
  `budgetingMethod` 三种口径的原文定义、`timeSliceTarget` / `timeSliceWindow` 取值域
- [Google SRE Workbook — Implementing SLOs / Alerting on SLOs](https://sre.google/workbook/implementing-slos/) —
  sloth 窗口 YAML 标注的原始出处(本轮该站不可达,数值经 sloth 源码与其 YAML 转引)
