#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
静默推送（background notification）的投递语义与后台唤醒预算。

权威来源（本轮实读全文）：
- Pushing background updates to your App
  https://developer.apple.com/documentation/usernotifications/pushing-background-updates-to-your-app
- Sending notification requests to APNs（apns-push-type / apns-priority）
  https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns
"""
from __future__ import annotations

from collections import deque

# 文档原文：系统把背景通知当作低优先级，**不保证投递**；
# 数量过多会被节流，允许条数随当前状况变化，但 "don't try to send more than
# two or three per hour"。本实现取上界 3，并在 README 标注口径。
THROTTLE_LIMIT_PER_HOUR = 3
THROTTLE_WINDOW_SECONDS = 3600

# 文档原文：App 被唤醒后有 30 秒完成工作并调用 completion handler。
BACKGROUND_BUDGET_SECONDS = 30

# 背景通知的 aps 里只允许 content-available，不得出现会触发用户交互的键
FORBIDDEN_IN_BACKGROUND = ("alert", "sound", "badge")


class BackgroundPayloadError(ValueError):
    """背景通知载荷不合法。"""


def validate_background_payload(payload: dict) -> None:
    """
    文档：背景通知的 aps 字典**只包含** content-available。
    payload 可以有自定义键，但 aps 里不得有任何触发用户交互的键。
    """
    aps = payload.get("aps")
    if not isinstance(aps, dict):
        raise BackgroundPayloadError("background payload must contain an aps dictionary")
    if aps.get("content-available") != 1:
        raise BackgroundPayloadError("aps.content-available must be 1")
    for k in FORBIDDEN_IN_BACKGROUND:
        if k in aps:
            raise BackgroundPayloadError(
                "background aps must not contain %r" % k
            )


def background_headers(**kw) -> dict:
    """背景通知要求的头部组合：push-type=background 且 priority=5。"""
    return {"apns-push-type": "background", "apns-priority": "5", **kw}


class BackgroundDelivery:
    """
    复刻文档"系统持有并延迟投递"的三条副作用：

    1. 收到新的背景通知时，**丢弃旧的，只保留最新一条**；
    2. App 被强制退出/杀掉时，**丢弃持有的通知**；
    3. 用户启动 App 时，**立即投递持有的通知**。

    外加：每小时 2~3 条的节流，与 30 秒的后台任务预算。
    """

    def __init__(self, throttle_limit: int = THROTTLE_LIMIT_PER_HOUR,
                 window: int = THROTTLE_WINDOW_SECONDS,
                 budget: int = BACKGROUND_BUDGET_SECONDS):
        self.throttle_limit = throttle_limit
        self.window = window
        self.budget = budget
        self.held: dict | None = None
        self._sent: deque[float] = deque()
        self.throttled = 0
        self.delivered: list[dict] = []
        self.killed = False
        self.discarded_by_kill = 0

    # -- 服务端侧：发送与节流 ------------------------------------------

    def _prune(self, now: float) -> None:
        while self._sent and now - self._sent[0] >= self.window:
            self._sent.popleft()

    def send(self, apns_id: str, now: float) -> str:
        """返回 'held' / 'throttled'。"""
        self._prune(now)
        if len(self._sent) >= self.throttle_limit:
            self.throttled += 1
            return "throttled"
        self._sent.append(now)
        self._deliver(apns_id)
        return "held"

    def _deliver(self, apns_id: str) -> None:
        """系统侧：只保留最新一条。"""
        replaced = self.held is not None
        self.held = {"apns_id": apns_id, "replaced": replaced}

    # -- 设备侧状态迁移 -------------------------------------------------

    def force_quit(self) -> None:
        """用户强制退出 / 系统杀掉 App：持有的通知被丢弃。"""
        self.killed = True
        if self.held is not None:
            self.held = None
            self.discarded_by_kill += 1

    def user_launch(self) -> dict | None:
        """用户主动启动 App：立即投递持有的通知。"""
        self.killed = False
        item = self.held
        if item is not None:
            self.held = None
            self.delivered.append(item)
        return item

    def run_task(self, duration: float) -> str:
        """后台任务预算：>30 秒会被系统判定超时。"""
        if duration > self.budget:
            return "expired"
        return "completed"


# ---------------------------------------------------------------- 自检

def _self_check() -> int:
    n = 0

    def ok(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    # --- 载荷校验 ---
    validate_background_payload({"aps": {"content-available": 1}, "acme1": "bar", "acme2": 42})
    n += 1  # 文档 Listing 里的样例载荷
    try:
        validate_background_payload({"aps": {"content-available": 1, "alert": "hi"}})
        raise AssertionError("background must reject alert")
    except BackgroundPayloadError:
        n += 1
    for k in ("sound", "badge"):
        try:
            validate_background_payload({"aps": {"content-available": 1, k: 1}})
            raise AssertionError("background must reject %s" % k)
        except BackgroundPayloadError:
            n += 1
    try:
        validate_background_payload({"aps": {}})
        raise AssertionError("background requires content-available")
    except BackgroundPayloadError:
        n += 1
    try:
        validate_background_payload({"aps": {"content-available": 0}})
        raise AssertionError("content-available must be 1")
    except BackgroundPayloadError:
        n += 1

    # --- 头部组合 ---
    h = background_headers()
    ok(h["apns-push-type"] == "background", "push-type is background")
    ok(h["apns-priority"] == "5", "priority is 5 (power considerations)")

    # --- 节流：每小时 2~3 条 ---
    d = BackgroundDelivery()
    ok(d.send("a", 0) == "held", "first notification is held")
    ok(d.send("b", 10) == "held", "second is held")
    ok(d.send("c", 20) == "held", "third is held")
    ok(d.send("d", 30) == "throttled", "fourth within an hour is throttled")
    ok(d.throttled == 1, "throttle counter increments")
    # 滑出 1 小时窗口后恢复：3620 时 0/10/20 三条都已出窗
    ok(d.send("e", 3600) == "held", "one window after the first send, capacity frees up")
    ok(len(d._sent) == 3, "sends at 10/20 are still inside the window at t=3600")
    d2w = BackgroundDelivery()
    for i, t in enumerate((0, 10, 20)):
        d2w.send("s%d" % i, t)
    ok(d2w.send("s3", 30) == "throttled", "fourth inside the hour is throttled")
    ok(d2w.send("s4", 3620) == "held", "after a full window elapses, sending resumes")
    ok(len(d2w._sent) == 1, "sliding window pruned everything older than an hour")

    # --- 只保留最新一条 ---
    d2 = BackgroundDelivery()
    d2.send("x", 0)
    ok(d2.held["apns_id"] == "x", "first is held")
    d2.send("y", 1)
    ok(d2.held["apns_id"] == "y", "newer replaces older")
    ok(d2.held["replaced"] is True, "replacement is recorded")
    ok(len(d2.delivered) == 0, "nothing delivered while app is in background")

    # --- force quit 丢弃 ---
    d3 = BackgroundDelivery()
    d3.send("z", 0)
    d3.force_quit()
    ok(d3.held is None, "force quit discards the held notification")
    ok(d3.discarded_by_kill == 1, "discard is counted")
    ok(d3.killed is True, "killed flag set")

    # --- 用户启动即投递 ---
    d4 = BackgroundDelivery()
    d4.send("w", 0)
    item = d4.user_launch()
    ok(item is not None and item["apns_id"] == "w", "user launch delivers held notification")
    ok(d4.held is None, "held slot cleared after delivery")
    ok(len(d4.delivered) == 1, "delivery recorded")
    ok(d4.user_launch() is None, "second launch has nothing to deliver")

    # 先 force quit 再启动：什么都收不到
    d5 = BackgroundDelivery()
    d5.send("v", 0)
    d5.force_quit()
    ok(d5.user_launch() is None, "killed-then-launched receives nothing")

    # --- 30 秒预算 ---
    d6 = BackgroundDelivery()
    ok(d6.run_task(1.0) == "completed", "short task completes")
    ok(d6.run_task(29.9) == "completed", "just under budget completes")
    ok(d6.run_task(30.0) == "completed", "exactly 30s is within budget")
    ok(d6.run_task(30.1) == "expired", "over 30s expires")
    ok(BACKGROUND_BUDGET_SECONDS == 30, "budget is 30 seconds per the doc")

    # 节流上限取自文档 "two or three per hour" 的上界
    ok(THROTTLE_LIMIT_PER_HOUR == 3, "throttle limit is the documented upper bound (3/h)")
    ok(THROTTLE_WINDOW_SECONDS == 3600, "throttle window is one hour")

    return n


if __name__ == "__main__":
    total = _self_check()
    print("OK: %d assertions passed" % total)
