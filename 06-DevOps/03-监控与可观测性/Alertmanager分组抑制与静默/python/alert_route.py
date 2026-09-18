"""路由树、抑制规则、静默。

路由默认值来自官方 configuration 文档:group_wait=30s、group_interval=5m、
repeat_interval=4h;`continue` 默认 false(命中即停止同层匹配);子 route 未显式
设置的参数**继承父 route**;root 节点不允许带 matchers。

官方还给出两条容易忽略的时间语义:
  - `group_interval` 同时是通知管道的 context timeout —— 发送耗时超过它会被取消
  - `repeat_interval` 应为 `group_interval` 的倍数,否则**向上取整**到下一个倍数
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from alert_matcher import fingerprint, labels_match

DEFAULT_GROUP_WAIT = 30.0
DEFAULT_GROUP_INTERVAL = 300.0
DEFAULT_REPEAT_INTERVAL = 14400.0

_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, "w": 604800.0}
_DUR_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h|d|w)$")


def parse_duration(text):
    """`5m` -> 300.0。单位支持 ms/s/m/h/d/w,官方口径的时长子集。"""
    m = _DUR_RE.match(str(text).strip())
    if not m:
        raise ValueError("无法解析的时长: %r" % (text,))
    return float(m.group(1)) * _UNITS[m.group(2)]


# ---------------------------------------------------------------- 告警对象


@dataclass
class Alert:
    """Alertmanager API v2 的告警对象(简化)。ends_at 为 None 表示仍活跃。"""

    labels: dict
    annotations: dict = field(default_factory=dict)
    starts_at: float = 0.0
    ends_at: object = None
    generator_url: str = ""

    @property
    def fingerprint(self):
        return fingerprint(self.labels)

    @property
    def resolved(self):
        return self.ends_at is not None


# ---------------------------------------------------------------- 路由树


@dataclass
class Route:
    name: str = ""
    receiver: str = ""
    matchers: list = field(default_factory=list)
    group_by: object = None
    group_wait: object = None
    group_interval: object = None
    repeat_interval: object = None
    continue_: bool = False
    routes: list = field(default_factory=list)


def resolve_params(route, parent=None):
    """子 route 未设置的参数继承父 route,一路回退到官方默认值。"""
    base = parent or {
        "group_by": [],
        "group_wait": DEFAULT_GROUP_WAIT,
        "group_interval": DEFAULT_GROUP_INTERVAL,
        "repeat_interval": DEFAULT_REPEAT_INTERVAL,
    }
    return {
        "group_by": base["group_by"] if route.group_by is None else route.group_by,
        "group_wait": base["group_wait"] if route.group_wait is None else route.group_wait,
        "group_interval": (
            base["group_interval"] if route.group_interval is None else route.group_interval
        ),
        "repeat_interval": (
            base["repeat_interval"] if route.repeat_interval is None else route.repeat_interval
        ),
    }


def walk(route, parent_eff=None, path=()):
    """深度优先遍历,产出 (路径, route, 生效参数)。"""
    eff = resolve_params(route, parent_eff)
    here = path + ((route.name or route.receiver or "?"),)
    yield here, route, eff
    for child in route.routes:
        yield from walk(child, eff, here)


def dispatch(route, labels, eff=None, path=()):
    """返回命中的 [(receiver, 路径, 生效参数)],按官方 Match 语义。

    1. 本 route 的 matchers 不命中 -> 空
    2. 依次尝试子 route;某个子 route 命中后,若其 `continue` 为 false 则停止
       同层的后续匹配;为 true 则继续尝试兄弟 route
    3. 没有任何子 route 贡献结果 -> 由本 route 的 receiver 兜底
    """
    if route.matchers and not labels_match(route.matchers, labels):
        return []
    eff = resolve_params(route, eff)
    out = []
    for child in route.routes:
        if not labels_match(child.matchers, labels):
            continue
        out.extend(dispatch(child, labels, eff, path + (child.name,)))
        if not child.continue_:
            break
    if not out and route.receiver:
        out.append((route.receiver, path, eff))
    return out


# ---------------------------------------------------------------- 抑制


@dataclass
class InhibitRule:
    """source 命中且与 target 在 equal 标签上相等时,target 的通知被抑制。"""

    source_matchers: list
    target_matchers: list
    equal: list = field(default_factory=list)


def equal_labels_hold(source, target, equal):
    """equal 里的每个标签在两边都必须相等。

    缺失标签等价于空字符串,因此"源和目标是同一条告警"以外的情形里,
    **两边都缺该标签也算相等** —— 这是最容易配错的一条:把 instance 写进
    equal 后,没有 instance 标签的聚合告警之间会互相抑制。
    """
    for k in equal:
        if source.get(k, "") != target.get(k, ""):
            return False
    return True


def inhibited_by(target, active_alerts, rules):
    """返回抑制了 target 的 [(规则下标, 源告警指纹)];空表示未被抑制。

    告警不会抑制自己(同指纹跳过)。
    """
    out = []
    for idx, rule in enumerate(rules):
        if not labels_match(rule.target_matchers, target.labels):
            continue
        for src in active_alerts:
            if src.fingerprint == target.fingerprint:
                continue
            if not labels_match(rule.source_matchers, src.labels):
                continue
            if equal_labels_hold(src.labels, target.labels, rule.equal):
                out.append((idx, src.fingerprint))
                break
    return out


# ---------------------------------------------------------------- 静默


@dataclass
class Silence:
    """动态创建的静默:所有 matcher 命中且时间落在窗口内即生效。"""

    id: str
    matchers: list
    starts_at: float
    ends_at: float
    created_by: str = ""
    comment: str = ""

    def active_at(self, t):
        """左闭右开:窗口起始时刻起生效,结束时刻起失效。"""
        return self.starts_at <= t < self.ends_at

    def affects(self, alert, t):
        return self.active_at(t) and labels_match(self.matchers, alert.labels)


def silenced_by(alert, silences, t):
    """命中该告警的静默 id 列表(多个静默同时命中只表示"被静默",幂等)。"""
    return [s.id for s in silences if s.affects(alert, t)]


def effective_repeat_interval(repeat_interval, group_interval):
    """repeat_interval 不是 group_interval 的倍数时向上取整到下一个倍数。"""
    if group_interval <= 0:
        return repeat_interval
    return math.ceil(repeat_interval / group_interval) * group_interval
