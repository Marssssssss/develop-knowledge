"""Envoy 自适应并发(梯度控制器)与过载管理器模型。

来源是**实读过的 Envoy 官方文档**:

1. Adaptive Concurrency filter(gradient controller),公式原文:

       gradient = (minRTT + B) / sampleRTT
       B = minRTT * buffer_pct
       limit_new = gradient * limit_old + headroom

   原文还明确:
   - "the headroom value is unconfigurable and pinned to the square-root of the
     concurrency limit"
   - minRTT "is periodically measured by pinning the concurrency limit to the
     configured min_concurrency"
   - 重算触发条件:"determined to be the minimum configured value for 5 consecutive
     sampling windows"
   - jitter "is used to randomly delay the start of the minRTT calculation window"
     to prevent all hosts in a cluster entering it at once
   - "having a concurrency limit of 3 by default"(min_concurrency 缺省值)

2. Overload Manager 的 trigger 两种类型,原文:
   - threshold: "Sets the action state to 1 (= saturated) when the resource pressure
     is above a threshold, and to 0 otherwise."
   - scaled: "Sets the action state to 0 when the resource pressure is below the
     scaling_threshold, (pressure - scaling_threshold)/(saturation_threshold -
     scaling_threshold) when scaling_threshold < pressure < saturation_threshold,
     and to 1 (= saturated) when the pressure is above the saturation_threshold."
"""

import math

# 文档给出的 min_concurrency 缺省值(原文 "a concurrency limit of 3 by default")
DEFAULT_MIN_CONCURRENCY = 3
# 重算 minRTT 所需的连续采样窗口数(原文 "5 consecutive sampling windows")
MIN_RTT_TRIGGER_WINDOWS = 5


# ---------------------------------------------------------------- 梯度控制器


def buffer_value(min_rtt, buffer_pct):
    """B = minRTT × buffer_pct / 100。

    口径说明:文档给的式子是 `B = minRTT * buffer_pct`,同时又说 "The buffer will be
    a percentage of the measured minRTT value whose value is modified via the buffer
    field"。配置里填的是**百分数**(示例配置未直接给 buffer,但字段语义是 percent),
    故本实现把入参 `buffer_pct` 当成"10 表示 10%",在公式里除以 100。
    若按文档字面把 10 当 0.10 用,B 会变成 minRTT 的 10 倍 —— 梯度恒 >1,控制器
    会一路把并发放大到天文数字(第一版就栽在这里)。
    """
    return min_rtt * buffer_pct / 100.0


def gradient(min_rtt, buffer_pct, sample_rtt):
    """gradient = (minRTT + B) / sampleRTT,其中 B = minRTT * buffer_pct。

    注意分子里的 minRTT 与 B 同量纲,故 buffer 越大分子越大 → 梯度越大 → 限流越松。
    """
    if sample_rtt <= 0:
        raise ValueError("sampleRTT must be positive")
    return (min_rtt + buffer_value(min_rtt, buffer_pct)) / sample_rtt


def headroom(limit):
    """headroom = sqrt(concurrency limit),**不可配置**。

    口径说明:文档只说"pinned to the square-root of the concurrency limit",没写
    是旧值还是新值。本实现取**旧值**(更新前),这也是迭代式实现的惯例。
    """
    return math.sqrt(limit)


def next_limit(limit, min_rtt, buffer_pct, sample_rtt, min_limit):
    """limit_new = max(min_limit, gradient * limit_old + headroom)。"""
    g = gradient(min_rtt, buffer_pct, sample_rtt)
    raw = g * limit + headroom(limit)
    return max(min_limit, raw)


def fixed_point(g):
    """稳态闭式:L = g·L + sqrt(L) → sqrt(L) = 1/(1−g) → L = 1/(1−g)²。

    只在 g < 1 时有意义;g >= 1 时 headroom 恒为正,极限会无限增长。
    """
    if g >= 1:
        return None
    return 1.0 / ((1.0 - g) ** 2)


def iterate(limit, min_rtt, buffer_pct, sample_rtt_fn, min_limit, steps):
    """迭代若干步。sample_rtt_fn(step, limit) -> sampleRTT,便于建模"并发越高越慢"。"""
    traj = []
    cur = limit
    for s in range(steps):
        srtt = sample_rtt_fn(s, cur)
        cur = next_limit(cur, min_rtt, buffer_pct, srtt, min_limit)
        traj.append(cur)
    return traj


# ---------------------------------------------------------------- minRTT 重算


class MinRttController(object):
    """minRTT 测量窗口的触发:连续 N 个采样窗口都处在最小并发时才启动。"""

    def __init__(self, min_concurrency=DEFAULT_MIN_CONCURRENCY,
                 trigger_windows=MIN_RTT_TRIGGER_WINDOWS):
        self.min_concurrency = min_concurrency
        self.trigger_windows = trigger_windows
        self.at_min_streak = 0

    def observe(self, limit):
        """每个采样窗口结束时调用;返回是否应当触发一次 minRTT 重算。"""
        if limit <= self.min_concurrency:
            self.at_min_streak += 1
        else:
            self.at_min_streak = 0
        if self.at_min_streak >= self.trigger_windows:
            self.at_min_streak = 0
            return True
        return False


def jittered_start(base_interval, jitter_pct, rng):
    """jitter 随机推迟 minRTT 窗口的起点:在 [0, interval * jitter_pct] 上加随机量。

    文档只说 "randomly delay the start",未给出具体分布;本实现取均匀分布并标注口径。
    """
    if jitter_pct < 0:
        raise ValueError("jitter must be non-negative")
    return base_interval + rng.uniform(0.0, base_interval * jitter_pct / 100.0)


def all_hosts_aligned(n_hosts, jitter_pct, rng, tolerance=0.05):
    """估算 N 个 host 的 minRTT 窗口起点落在同一小段时间内的比例(抖动越小越对齐)。"""
    starts = [jittered_start(60.0, jitter_pct, rng) for _ in range(n_hosts)]
    lo = min(starts)
    aligned = sum(1 for s in starts if s - lo <= tolerance * 60.0)
    return aligned


# ---------------------------------------------------------------- 过载管理器


def threshold_trigger(pressure, threshold):
    """threshold 型:pressure > threshold 时为 1(saturated),否则 0。"""
    return 1.0 if pressure > threshold else 0.0


def scaled_trigger(pressure, scaling_threshold, saturation_threshold):
    """scaled 型:三段式线性斜坡。

    取值:pressure <= scaling → 0;scaling < pressure < saturation → 线性插值;
    pressure >= saturation → 1。
    """
    if saturation_threshold <= scaling_threshold:
        raise ValueError("saturation must be greater than scaling")
    if pressure <= scaling_threshold:
        return 0.0
    if pressure >= saturation_threshold:
        return 1.0
    return (pressure - scaling_threshold) / (saturation_threshold - scaling_threshold)


def memory_pressure(usage, limit):
    """cgroup 内存压力 = usage / limit。

    文档原文: "When no memory limit is set in cgroup (indicated by -1 in v1 or
    'max' in v2), the pressure is reported as 0."
    """
    if limit is None or limit <= 0:
        return 0.0
    return usage / limit
