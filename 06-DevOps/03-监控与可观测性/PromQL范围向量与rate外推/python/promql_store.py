"""迷你内存 TSDB：样本存储、标签匹配、lookback 与 staleness。

严格照 Prometheus 官方 "Querying basics" 的 time series selectors / gotchas 实现：

* 即时向量选择器返回「求值时刻**之前或恰在**该时刻的最新样本」；该样本若比
  lookback 周期更早，则整条序列不返回 —— 原文：
  ``Time series are only returned if their most recent sample is less than the
  lookback period ago.``  lookback 默认 **5m**，可用 ``--query.lookback-delta``
  或单次查询的 ``lookback_delta`` 参数覆盖。
* 正则 matcher **完全锚定**：``env=~"foo"`` 等价于 ``env=~"^foo$"``。
* 「匹配空值的 matcher 也会选中根本没有该标签的序列」—— 缺失标签与空值标签
  在匹配语义上等价。
* 选择器必须指定指标名，或至少含一个**不匹配空值**的 matcher，否则非法
  （官方例子：``{job=~".*"}`` 非法，``{job=~".+"}`` 合法）。
* 目标不再暴露某序列时，该序列被标记为 stale；stale 之后的求值不返回该序列，
  直到有新样本写入。
"""

import re
from collections import namedtuple

#: staleness 标记（Prometheus 内部是一个特殊的 NaN；这里用哨兵对象表示）
STALE = object()

Sample = namedtuple("Sample", "t v")

#: 默认 lookback 周期（秒），对应 --query.lookback-delta 默认 5m
DEFAULT_LOOKBACK = 300.0


class Matcher:
    """单个标签匹配器。op ∈ {'=', '!=', '=~', '!~'}。"""

    __slots__ = ("label", "op", "value", "_re")

    def __init__(self, label, op, value):
        if op not in ("=", "!=", "=~", "!~"):
            raise ValueError("非法匹配运算符: %r" % (op,))
        self.label = label
        self.op = op
        self.value = value
        # 正则完全锚定（官方：A match of env=~"foo" is treated as env=~"^foo$"）
        self._re = re.compile(value) if op in ("=~", "!~") else None

    def matches(self, labels):
        actual = labels.get(self.label, "")
        if self.op == "=":
            return actual == self.value
        if self.op == "!=":
            return actual != self.value
        hit = self._re.fullmatch(actual) is not None
        return hit if self.op == "=~" else not hit

    def matches_empty(self):
        """该 matcher 是否接受空值（等价于「不指定该标签也能通过」）。"""
        if self.op == "=":
            return self.value == ""
        if self.op == "!=":
            return self.value != ""
        hit = self._re.fullmatch("") is not None
        return hit if self.op == "=~" else not hit

    def __repr__(self):
        return "%s%s%r" % (self.label, self.op, self.value)


def selector_is_legal(name, matchers):
    """向量选择器必须给出指标名，或至少一个不匹配空值的 matcher。"""
    if name:
        return True
    return any(not m.matches_empty() for m in matchers)


class Series:
    __slots__ = ("name", "labels", "samples")

    def __init__(self, name, labels):
        self.name = name
        self.labels = dict(labels)
        self.samples = []  # list[Sample] 或 (t, STALE)

    def add(self, t, v):
        # 时序存储要求按时间递增写入；乱序样本由 out-of-order 通道处理，
        # 本 demo 只演示顺序写入。
        if self.samples and t <= self.samples[-1][0]:
            raise ValueError("样本必须按时间递增写入: %r" % (t,))
        self.samples.append(Sample(t, v))

    def mark_stale(self, t):
        self.samples.append((t, STALE))


class MemStore:
    """极简内存时序库。"""

    def __init__(self, lookback=DEFAULT_LOOKBACK):
        self.lookback = lookback
        self._series = {}

    def series(self, name, labels):
        key = (name, tuple(sorted(labels.items())))
        s = self._series.get(key)
        if s is None:
            s = Series(name, labels)
            self._series[key] = s
        return s

    def add(self, name, labels, t, v):
        self.series(name, labels).add(t, v)

    def mark_stale(self, name, labels, t):
        self.series(name, labels).mark_stale(t)

    def all_series(self):
        return list(self._series.values())

    def _matching(self, name, matchers):
        out = []
        for s in self._series.values():
            if name and s.name != name:
                continue
            if all(m.matches(s.labels) for m in matchers):
                out.append(s)
        return sorted(out, key=lambda s: sorted(s.labels.items()))

    def select(self, name, matchers, eval_t):
        """即时向量：返回 [(labels, value)]，不含因 lookback / stale 被排除的序列。"""
        out = []
        for s in self._matching(name, matchers):
            s = self._latest(s, eval_t)
            if s is not None:
                out.append(s)
        return out

    def _latest(self, series, eval_t):
        """取 at or before eval_t 的最新样本，并施加 lookback 与 staleness 规则。"""
        found = None
        for entry in series.samples:
            if entry[0] > eval_t:
                break
            found = entry
        if found is None:
            return None
        t, v = found[0], found[1]
        if v is STALE:
            return None
        # 严格小于：恰好等于 lookback 周期时**不**返回（原文 less than）
        if eval_t - t >= self.lookback:
            return None
        return (series.labels, v)

    def range_select(self, name, matchers, range_start, range_end):
        """范围向量：区间为**左开右闭** (range_start, range_end]。

        Prometheus 原文：``The range is a left-open and right-closed interval,
        i.e. samples with timestamps coinciding with the left boundary of the
        range are excluded, while samples coinciding with the right boundary of
        the range are included.``
        """
        out = []
        for s in self._matching(name, matchers):
            pts = []
            for t, v in s.samples:
                if t <= range_start or t > range_end:
                    continue
                if v is STALE:
                    # staleness 标记把序列切断：标记之前的样本不再参与求值
                    pts = []
                    continue
                pts.append(Sample(t, v))
            if pts:
                out.append((s.labels, pts))
        return out
