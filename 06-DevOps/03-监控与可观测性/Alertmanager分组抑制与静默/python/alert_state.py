"""告警状态机与通知节奏。

Prometheus 侧(官方 alerting rules 文档):
  inactive --(表达式命中)--> pending --(连续满足 for)--> firing
  任一评估表达式不命中 -> for 计时器归零
  keep_firing_for(默认 0):条件不再满足后仍保持 firing 的时长,用于防抖
  合成序列 `ALERTS{alertname, alertstate}` 活跃期间恒为 1,不再活跃即 stale;
  另一条 `ALERTS_FOR_STATE` 携带告警转 active 的时刻,重启后靠它恢复 for 计时
  (只要还在 5 分钟 lookback 窗口内)

Alertmanager 侧(官方 configuration 文档):
  新 group:等 group_wait 后发首条通知;若 group_wait 未走完告警就已 resolved,
           **不发通知**(这就是它天然抑制抖动的原因)
  已有 group:每个 group_interval 检查一次;有告警新 firing / 有告警 resolved 就发,
             否则再看距上次发送是否已过 repeat_interval
"""

from __future__ import annotations

from dataclasses import dataclass, field

from alert_route import effective_repeat_interval

INACTIVE, PENDING, FIRING = "inactive", "pending", "firing"


@dataclass
class AlertState:
    """单条告警的状态机。每次规则评估调用一次 evaluate()。"""

    state: str = INACTIVE
    active_at: object = None
    firing_at: object = None
    last_met_at: object = None
    evaluations: int = 0

    def evaluate(self, now, matched, for_s=0.0, keep_firing_for_s=0.0):
        self.evaluations += 1
        if matched:
            self.last_met_at = now
            if self.state == INACTIVE:
                self.state = PENDING
                self.active_at = now
            # for_s=0 时首次评估即转 firing(官方:没有 for 子句的规则首次评估就激活)
            if self.state == PENDING and now - self.active_at >= for_s:
                self.state = FIRING
                self.firing_at = now
            return self.state
        if self.state == PENDING:
            self.state = INACTIVE  # 计时器归零,下次命中重新计时
            self.active_at = None
            return self.state
        if self.state == FIRING:
            if keep_firing_for_s > 0 and now - self.last_met_at < keep_firing_for_s:
                return self.state  # 条件已不满足,但仍在 keep_firing_for 窗口内
            self.state = INACTIVE
            self.firing_at = None
        return self.state

    @property
    def active(self):
        return self.state in (PENDING, FIRING)

    def sample_value(self):
        """`ALERTS` 的样本值:活跃期间为 1;不活跃时返回 None(序列 stale)。"""
        return 1 if self.active else None


def alerts_labels(alertname, state):
    """`ALERTS` 合成序列的标签;不活跃时返回 None(不输出样本)。"""
    if state.sample_value() is None:
        return None
    return {"alertname": alertname, "alertstate": state.state}


# ---------------------------------------------------------------- 通知节奏


@dataclass
class Group:
    key: str
    first_seen: float = 0.0
    last_sent: object = None
    last_fps: frozenset = field(default_factory=frozenset)
    sends: list = field(default_factory=list)


class NotificationEngine:
    """按 group_interval 节拍检查每个 group 是否需要发送通知。"""

    def __init__(self, group_wait=30.0, group_interval=300.0, repeat_interval=14400.0):
        self.group_wait = group_wait
        self.group_interval = group_interval
        self.repeat_interval = repeat_interval

    @property
    def effective_repeat(self):
        """repeat_interval 不是 group_interval 的倍数时向上取整。"""
        return effective_repeat_interval(self.repeat_interval, self.group_interval)

    def next_check_at(self, group):
        """该 group 下一次被检查的时刻。"""
        if group.last_sent is None:
            return group.first_seen + self.group_wait
        return group.last_sent + self.group_interval

    def decide(self, group, now, current_fps):
        """返回 None 或 first / changed / repeat。"""
        fps = set(current_fps)
        if group.last_sent is None:
            if not fps:
                return None  # group_wait 内全部 resolved:不发通知
            return "first" if now >= group.first_seen + self.group_wait else None
        if now - group.last_sent < self.group_interval:
            return None
        if fps != set(group.last_fps):
            return "changed"
        if now - group.last_sent >= self.effective_repeat:
            return "repeat"
        return None

    def dispatch(self, group, now, current_fps):
        """判定发送并记账;返回 reason 或 None。"""
        reason = self.decide(group, now, current_fps)
        if reason is None:
            return None
        group.last_sent = now
        group.last_fps = frozenset(current_fps)
        group.sends.append((now, reason))
        return reason
