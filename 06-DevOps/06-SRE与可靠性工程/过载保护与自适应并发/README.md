# 过载保护与自适应并发

## 简介

固定并发上限是个两难:设小了浪费容量,设大了过载时雪崩。Envoy 的
**Adaptive Concurrency** 用一个反馈环解决:持续采样请求延迟,把实测延迟与"理想
往返时间(minRTT)"比较,据此**动态调整**并发上限。配合 **Overload Manager** 的
资源监视器与动作,可以在内存/连接压力升高时分级降载。

本 demo 把这两个机制的公式从 Envoy 官方文档落成可执行模型,并给出**稳态闭式**。

关键概念:

| 概念 | 一句话 |
| --- | --- |
| minRTT | 上游在**无排队**时的理想往返时间,周期性实测 |
| gradient | `(minRTT + B) / sampleRTT`,延迟越高越小 |
| headroom | `sqrt(limit)`,**不可配置**,驱动上限向上试探 |
| trigger | Overload Manager 里把"资源压力"映射到"动作状态"的两段/三段函数 |

## 原理详解

### 1. 梯度控制器(公式原文)

```text
gradient = (minRTT + B) / sampleRTT
B = minRTT * buffer_pct
limit_new = gradient * limit_old + headroom
```

文档原文:

> The gradient value has a useful property, such that it decreases as the sampled
> latencies increase. Notice that `B`, the buffer value added to the minRTT, allows
> for normal variance in the sampled latencies by requiring the sampled latencies the
> exceed the minRTT by some configurable threshold before decreasing the gradient value.

> Because the headroom value is so necessary to the proper function for the gradient
> controller, the headroom value is unconfigurable and pinned to the square-root of
> the concurrency limit.

**buffer 的量纲是个坑**:文档写 `B = minRTT * buffer_pct` 又说 buffer 是 percentage。
配置里填 10 表示 10%,所以实现必须除以 100;按字面把 10 当 0.10 用会让 B 变成
minRTT 的 10 倍、梯度恒 > 1,控制器会把并发放大到天文数字(第一版就栽在这里)。

实测(minRTT=50ms、buffer=10% → B=5ms):

| sampleRTT | gradient | 含义 |
| --- | --- | --- |
| 25 | 2.200 | 放宽 |
| 50 | 1.100 | 放宽 |
| **55** | **1.000** | **临界点** |
| 60 | 0.917 | 收紧 |
| 100 | 0.550 | 收紧 |

临界点有闭式:**`gradient = 1` ⟺ `sampleRTT = minRTT × (1 + buffer/100)`**。

### 2. 稳态闭式:`L = 1/(1−g)²`

令 `L = g·L + sqrt(L)`,得 `sqrt(L) = 1/(1−g)`,即 `L = 1/(1−g)²`。只在 `g < 1`
时存在。实测(E4)闭式与 400 步迭代完全吻合:

| sampleRTT | g | 闭式稳态 | 迭代 400 步 | 差 |
| --- | --- | --- | --- | --- |
| 60 | 0.916667 | 144.0000 | 144.0000 | 6.9e−06 |
| 80 | 0.687500 | 10.2400 | 10.2400 | 5.3e−15 |
| 100 | 0.550000 | 4.9383 | 4.9383 | 1.8e−15 |

`g ≥ 1` 时 headroom 恒为正,上限**无限增长**(E3:25 → 280 → 3096 → … 第 12 步已到
262,无稳态)。

### 3. headroom 让控制器过冲后阻尼收敛

实测(E10):延迟模型 `sampleRTT = 50 + max(0, limit−120) × 0.5`,从 25 起步的轨迹是
`32.5 → 41.45 → … → 137.29 → 140.47(峰值) → 140.11 → 140.149 → …`。峰值高出稳态约
0.33 后阻尼收敛。**它不是单调爬升的** —— 断言写成"单调"是错的,过冲才是真实行为。

### 4. minRTT 怎么测:踩到下限连续 5 个窗口才重算

文档原文:

> The minRTT is periodically measured by pinning the concurrency limit to the
> configured `min_concurrency` ... triggered in scenarios where the concurrency limit
> is determined to be the minimum configured value for **5 consecutive sampling
> windows**.

`min_concurrency` 缺省 3(原文 "having a concurrency limit of 3 by default")。
实测(E6):连着 5 个窗口 `limit = 3` 才触发一次重算并清零;中间出现一次 10 会打断
连击重新计数。

副作用:**测量期间并发被压到 3,会有明显 503**。文档承认这点并建议开启重试:

> It is possible that there is a noticeable increase in request 503s during the minRTT
> measurement window because of the potentially significant drop in the concurrency
> limit. This is expected and it is recommended to enable retries for resets/503s.

### 5. jitter 防止整个集群同时进入测量窗口

> The jitter ... is used to randomly delay the start of the minRTT calculation window
> to prevent all hosts in a cluster from being in a minRTT calculation window ... at
> the same time.

实测(E7,20 个 host、容差 ±5%):jitter=0 时 20 个全部对齐;jitter=10% 时降到 12;
jitter=50% 时只剩 5 个。

### 6. Overload Manager:方波 vs 斜坡

| trigger 类型 | 行为 |
| --- | --- |
| `threshold` | 压力 > 阈值 → 1(saturated),否则 0。**严格大于**:恰好等于阈值不触发 |
| `scaled` | 压力 ≤ scaling → 0;中间线性插值;≥ saturation → 1 |

实测(E8,threshold=0.7、scaled=(0.5, 0.9)):

| pressure | 0.50 | 0.60 | 0.70 | 0.71 | 0.80 | 0.90 |
| --- | --- | --- | --- | --- | --- | --- |
| threshold | 0 | 0 | **0** | 1 | 1 | 1 |
| scaled | 0 | 0.25 | 0.50 | 0.525 | 0.75 | 1 |

### 7. cgroup 内存压力:没配 limit 就永远是 0

文档原文:> "When no memory limit is set in cgroup (indicated by -1 in v1 or "max" in
v2), the pressure is reported as 0." —— 实测(E9):`limit = None / 0 / -1` 三种写法
都得到压力 0,基于内存的降载动作因此永远不会触发。

## 对比 / 选型

| | 固定并发上限 | 自适应并发 | Overload Manager |
| --- | --- | --- | --- |
| 依据 | 压测拍板 | 实测延迟 vs minRTT | 进程内资源压力(内存/连接) |
| 粒度 | 一个常数 | 每 `concurrency_update_interval` 调整 | 每资源监视器周期 |
| 代价 | 容量浪费或雪崩 | 测量期有 503、需重试 | 需要正确配置 limit |
| 适合 | 上游容量已知且稳定 | 上游容量未知/波动 | 本机资源快撑不住时降载 |

## 环境准备

- Python 3.9+ / Go 1.21+,零第三方依赖
- 操作系统不限

## 运行方式

### Python

```bash
cd python
python main.py                   # E1..E10 实测数字
python selfcheck_adaptive.py     # 断言自检,期望末行 PASS 68 / FAIL 0
```

### Go

```bash
cd go
go run .                         # 同包多文件必须用 `go run .`
```

## 关键代码片段

`adaptive.py` 里的更新式(对应原理详解第 1 步):

```python
def next_limit(limit, min_rtt, buffer_pct, sample_rtt, min_limit):
    g = gradient(min_rtt, buffer_pct, sample_rtt)
    raw = g * limit + headroom(limit)     # headroom 取**旧值**
    return max(min_limit, raw)
```

`scaled` 触发器的三段式(对应第 6 步):

```python
if pressure <= scaling_threshold:
    return 0.0
if pressure >= saturation_threshold:
    return 1.0
return (pressure - scaling_threshold) / (saturation_threshold - scaling_threshold)
```

## 性能与边界

- 单次更新是 O(1);迭代 400 步求稳态约 0.1 ms 级
- `gradient` 要求 `sampleRTT > 0`;`scaled` 要求 `saturation > scaling`,否则抛错
- `min_concurrency` 缺省 3、minRTT 触发需 5 个连续窗口,均为文档给出的常量
- 三处文档未明确、本 demo 自行选择的口径(buffer 量纲 / headroom 取旧值 / jitter
  取均匀分布)已在代码注释与 `NOTES.md` §8 标注

## 注意事项与常见坑

1. **buffer 是百分数不是小数**:填 10 表示 10%。按 `B = minRTT × 10` 实现会让梯度恒 > 1,并发无上限增长。
2. **稳态只在 `g < 1` 时存在**,否则上限被压到 `min_concurrency` 并触发 minRTT 重算 —— 这是设计好的回环。
3. **minRTT 测量期会有 503**(并发被钉到缺省的 3),务必对 503 / reset 开启重试;**jitter 别设成 0**,否则整个集群同时进入测量窗口。
4. **`headroom` 让系统过冲**:稳态附近有约 0.3 的振荡。不要用"单调爬升"做断言。
5. **cgroup 没配内存 limit 时压力恒为 0**;**threshold 触发器是严格大于**,恰好等于阈值时状态为 0。

> 完整版(含每条的现象 → 原因 → 规避)见 [`NOTES.md`](./NOTES.md)。

## 参考资料(实际阅读过的权威来源)

- [Envoy — Adaptive Concurrency filter](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/adaptive_concurrency_filter.html) —
  梯度控制器的三个公式、`minRTT` 的测量方式与 5 窗口触发条件、jitter 的用途、
  headroom 不可配置且等于 sqrt(limit)、测量期 503 的官方说明、
  `min_concurrency_limit` 与 `min_concurrency` 的分工、示例配置数值
- [Envoy — Overload manager](https://www.envoyproxy.io/docs/envoy/latest/configuration/operations/overload_manager/overload_manager.html) —
  `threshold` 与 `scaled` 两类 trigger 的原文定义、cgroup 内存监视器的压力计算与
  "no memory limit → pressure 0" 规则、`envoy.overload_actions.*` 动作清单
