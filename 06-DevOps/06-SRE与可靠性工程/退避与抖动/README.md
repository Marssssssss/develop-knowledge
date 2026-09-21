# 退避与抖动

## 简介

重试不加退避,会把一次抖动放大成一场雪崩:N 个客户端同时失败、同时重试、再同时
失败。加**指数退避**只能让重试变稀疏,却不能打散它们——失败的客户端仍然**同步**,
只是同步得慢一点。真正解决问题的是**抖动(jitter)**:把重试时刻随机化,让它们
从"每一轮都有一簇"变成"近似恒定速率"。

本 demo 逐行转写 AWS 官方模拟器的四种策略,并用一个可复现的 OCC 竞争模型量化
"到底省了多少工"。

关键概念:

| 概念 | 一句话 |
| --- | --- |
| capped exponential backoff | `expo(n) = min(cap, 2ⁿ × base)`,退避按 2 的幂增长到上限 |
| Full Jitter | `uniform(0, v)` —— 可以睡到接近 0,铺开最宽 |
| Equal Jitter | `v/2 + uniform(0, v/2)` —— 至少保留一半退避,铺开只有一半宽 |
| Decorrelated Jitter | `min(cap, uniform(base, sleep × 3))` —— **有状态**,与尝试次数无关 |

历史背景:Marc Brooker 2015 年发表于 AWS Architecture Blog 的
《Exponential Backoff And Jitter》,配的模拟器代码至今仍在
`aws-samples/aws-arch-backoff-simulator`。博客自述"8 年后这套方案仍是 Amazon
构建远程客户端库的基石"。

## 原理详解

### 1. 四种策略(逐行转写自模拟器源码)

```python
def expo(self, n): return min(self.cap, pow(2, n) * self.base)   # 基类

NoBackoff.backoff(n)  = 0
ExpoBackoff.backoff(n)= expo(n)
EqualJitter: v = expo(n); return v/2 + random.uniform(0, v/2)
FullJitter:  v = expo(n); return random.uniform(0, v)
Decorr:      self.sleep = min(self.cap, random.uniform(self.base, self.sleep * 3))
```

源码里的客户端构造参数是 `backoff_cls(5, 2000)`,即 **base = 5ms、cap = 2000ms**;
网络延迟模型是 `Net(mean=10, sd=2)`(博客正文: "mean of 10ms and variance of 4ms")。

实测(E1):`expo` 在 n=9 撞到 cap(2⁹×5 = 2560 → 2000),之后恒为 2000。

实测(E2,n=3 时 `expo = 40`,各 20000 次采样):

| 策略 | 最小 | 最大 | 均值 | 铺开宽度 |
| --- | --- | --- | --- | --- |
| expo | 40.000 | 40.000 | 40.000 | 0(确定值) |
| equal | 20.000 | 39.999 | 29.956 | v/2 = 20 |
| full | 0.005 | 39.998 | 20.045 | v = 40 |
| decorr | 5.000 | 14.999 | 9.980 | 由状态决定 |

### 2. Decorrelated Jitter 是**有状态**的,`n` 被忽略

它的下界恒为 `base`、上界是上一次睡眠的 3 倍。两个直接后果:

- **尝试次数 `n` 完全不起作用**:同一实例、同一随机种子下 `backoff(0)` 与
  `backoff(99)` 返回同一个值(实测 E3,均为 9.523796)
- **它不是单调的**:下界不随 `sleep` 增长,所以可以突然掉回 base 附近。实测(E4)
  30 次调用里有 7 次下降,而纯指数退避严格递增

### 3. OCC 竞争:为什么工作量是 N²

博客正文:

> With N clients contending, the total amount of work done by the system increases
> with N². ... one client succeeds every round, so it takes N rounds for all N
> clients to succeed.

本 demo 的轮次模型给出**闭式**:不做退避时总调用次数 = `N + (N−1) + … + 1 =
N(N+1)/2`。实测(E5)完全吻合:N=10 → 55,N=100 → 5050;N 翻倍时工作量约 4 倍。

### 4. 抖动省了多少(实测 E6,100 个客户端,种子 20260921)

| 策略 | 调用次数 | 相对 none | 完成时间 |
| --- | --- | --- | --- |
| none | 5050 | 100% | 1980 ms |
| expo | 5050 | 100% | **186530 ms** |
| equal | 454 | 8.99% | 649 ms |
| full | 387 | 7.66% | 317 ms |
| decorr | 367 | 7.27% | 306 ms |

两条和博客一致的定性结论:

- **纯指数退避(无 jitter)工作量一点没省**,只是把完成时间拖长 94 倍 —— 对应
  博客 "there are still clusters of calls"
- **三种 jitter 里 Equal Jitter 最差**(工作更多且耗时更久),对应博客
  "Equal Jitter is the loser. It does slightly more work than Full Jitter, and
  takes much longer"

换 8 个随机种子重跑,`equal > full > decorr` 的排序**恒成立**,波动幅度均小于
均值的 1/4 —— 结论不是单个种子的巧合。

## 对比 / 选型

| 策略 | 铺开宽度 | 最短睡眠 | 有状态 | 适合 |
| --- | --- | --- | --- | --- |
| 无退避 | — | 0 | 否 | 只在确认无竞争时用 |
| 纯指数 | 0 | 固定 | 否 | **不推荐**:不省工,只拖时间 |
| Equal Jitter | v/2 | v/2 | 否 | 想保证最小退避量时 |
| Full Jitter | v | ~0 | 否 | 默认选择:工作量最小 |
| Decorrelated | 动态 | base | **是** | 想让不同客户端的退避彼此去相关 |

## 环境准备

- Python 3.9+ / Go 1.21+,零第三方依赖
- 操作系统不限

## 运行方式

### Python

```bash
cd python
python main.py                 # E1..E8 实测数字
python selfcheck_backoff.py    # 断言自检,期望末行 PASS 61 / FAIL 0
```

### Go

```bash
cd go
go run .                       # 同包多文件必须用 `go run .`
```

## 关键代码片段

`backoff.py` 里唯一的"有状态"策略(对应原理详解第 2 步):

```python
class ExpoBackoffDecorr(Backoff):
    def __init__(self, base, cap, rng=None):
        Backoff.__init__(self, base, cap)
        self.rng = rng or _random
        self.sleep = self.base          # 状态从 base 起步

    def backoff(self, n):               # n 进来了但**完全没被使用**
        self.sleep = min(self.cap, self.rng.uniform(self.base, self.sleep * 3))
        return self.sleep
```

`contention.py` 里让模型有分辨力的那一行:

```python
wake = now + 2 * rtt + c["bo"].backoff(c["attempt"])
c["next"] = math.ceil(wake / slot - 1e-12) * slot   # 向上取整到时间槽
```

## 性能与边界

- `simulate` 是 O(调用次数),N=100 时约 5000 次迭代;自检跑 8 个种子 × 5 种策略
  约需数秒
- `max_time = 5e6 ms` 是兜底:纯指数退避下 N=100 耗时 186530ms,仍在界内
- 本模型**刻意简化**:固定 rtt(不含方差)、同刻到达按 client id 定序。绝对值与
  AWS 博客的图不同,只用于比较**相对关系**
- 时间槽 `slot` 是模型参数,默认 1ms。它决定"多接近算同时",直接影响绝对数值,
  但**不改变排序**(自检里 8 个种子都验了排序)

## 注意事项与常见坑

1. **不加 jitter 的指数退避是个陷阱**:它给人的错觉是"已经在退避了",实测工作量
   和完全不退避**一模一样**(都是 5050),只是把完成时间拖长两个数量级。
2. **Decorr 的 `n` 是摆设**:把它当成"第 n 次尝试的退避"来推理会得出错误结论 ——
   它只看上一次睡了多久。想按尝试次数增长,应该用 `expo` 系。
3. **Decorr 会后退**:`sleep` 可以掉回 base 附近。若上层假设"退避单调变长"(例如
   用它估算重试超时),会被打脸。
4. **模型必须离散化**:连续随机数几乎永不相等,不设时间槽时四种策略会给出**同一个
   数字**(实测都是 199 = 2N−1),看起来"都很好",实则模型没有分辨力。自检里把这条
   做成了负控断言。
5. **Full Jitter 可以睡到 0**:这正是它铺得最开的原因,但也意味着"重试间隔"没有
   下界。对有严格最小间隔要求的场景用 Equal Jitter。
6. **cap 不是"最多退避 2000ms"而是"退避上界"**:配合 jitter 后实际睡眠是
   `uniform(0, cap)`,期望只有 cap/2。

## 参考资料(实际阅读过的权威来源)

- [AWS Architecture Blog — Exponential Backoff And Jitter](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/) —
  OCC 竞争问题、`N²` 工作量的论断、Full / Equal / Decorrelated 三种 jitter 的命名
  与定性对比、网络延迟参数(mean 10ms、variance 4ms)
- [aws-samples/aws-arch-backoff-simulator — `src/backoff_simulator.py`](https://github.com/aws-samples/aws-arch-backoff-simulator) —
  四种策略的**源码实现**(本 demo 的转写对象):`expo()` 的封顶公式、
  `ExpoBackoffEqualJitter` / `ExpoBackoffFullJitter` / `ExpoBackoffDecorr` 的具体
  表达式、`backoff_cls(5, 2000)` 与 `Net(10, 2)` 的实际取值
