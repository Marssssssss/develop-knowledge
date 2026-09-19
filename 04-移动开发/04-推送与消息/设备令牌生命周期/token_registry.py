#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
设备令牌（device token）的生命周期与失效清理。

权威来源（本轮实读全文）：
- Registering your app with APNs
  https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns
- Handling notification responses from APNs（哪些 reason 意味着令牌失效）
  https://developer.apple.com/documentation/usernotifications/handling-notification-responses-from-apns

FCM 侧的令牌失效错误码来自官方 Admin SDK 源码（firebase.google.com 本轮不可达）：
- firebase_admin/messaging.py 的 _MessagingService.FCM_ERROR_TYPES
  https://github.com/firebase/firebase-admin-python/blob/master/firebase_admin/messaging.py
"""
from __future__ import annotations

# APNs：这些 reason 意味着令牌已经失效，必须清理（文档明列不可重试）
APNS_DEAD_TOKEN_REASONS = {
    "BadDeviceToken", "DeviceTokenNotForTopic", "ExpiredToken", "Unregistered",
}

# FCM：官方 SDK 里映射到 UnregisteredError 的错误码
FCM_DEAD_TOKEN_CODES = {"UNREGISTERED"}

# 官方 SDK 的错误码 → 异常类型映射（节选，用于对照表）
FCM_ERROR_TYPES = {
    "APNS_AUTH_ERROR": "ThirdPartyAuthError",
    "QUOTA_EXCEEDED": "QuotaExceededError",
    "SENDER_ID_MISMATCH": "SenderIdMismatchError",
    "THIRD_PARTY_AUTH_ERROR": "ThirdPartyAuthError",
    "UNREGISTERED": "UnregisteredError",
}


class TokenRegistryError(ValueError):
    """令牌注册/清理失败。"""


class TokenRegistry:
    """
    服务端令牌库。

    文档规定的几条硬约束：
    1. **每次 App 启动都要注册并取 token**，不要缓存到本地 ——
       APNs 会在「从备份恢复」「装到新设备」「重装系统」时发新 token；
    2. token 对「设备 + 应用」唯一，**同一个设备上的不同 App 不能复用 token**；
    3. 一个用户可能有多台设备，因此**一个用户对应多个 token**；
    4. 收到失效类 reason 必须清理，否则形成重试风暴。
    """

    def __init__(self):
        # (device_id, app_id) -> token
        self._by_device_app: dict[tuple[str, str], str] = {}
        # token -> metadata
        self._tokens: dict[str, dict] = {}
        self.dead: list[dict] = []
        self.registrations = 0

    # -- 注册 ----------------------------------------------------------

    def register(self, *, user_id: str, device_id: str, app_id: str,
                 token: str, now: float) -> str:
        """
        返回 'new' / 'unchanged' / 'rotated'。
        幂等：重复上报同一个 token 不产生新记录（App 每次启动都会上报）。
        """
        if not token:
            raise TokenRegistryError("device token must not be empty")
        # 文档原文：同一个 device token 不能给多个 app 用，即使在同一台设备上。
        owner = self._tokens.get(token)
        if owner is not None and (owner["device_id"], owner["app_id"]) != (device_id, app_id):
            raise TokenRegistryError(
                "device token is already bound to (%s, %s)"
                % (owner["device_id"], owner["app_id"])
            )
        self.registrations += 1
        key = (device_id, app_id)
        old = self._by_device_app.get(key)
        if old == token:
            self._tokens[token]["last_seen"] = now
            return "unchanged"
        if old is not None:
            # 同一个 (设备, 应用) 换了 token：旧的立刻作废
            self._tokens.pop(old, None)
            self.dead.append({"token": old, "reason": "rotated", "at": now})
        self._by_device_app[key] = token
        self._tokens[token] = {"user_id": user_id, "device_id": device_id,
                               "app_id": app_id, "last_seen": now}
        return "new" if old is None else "rotated"

    # -- 查询 ----------------------------------------------------------

    def token_for(self, device_id: str, app_id: str) -> str | None:
        return self._by_device_app.get((device_id, app_id))

    def tokens_for_user(self, user_id: str) -> list:
        return sorted(t for t, m in self._tokens.items() if m["user_id"] == user_id)

    def __len__(self) -> int:
        return len(self._tokens)

    # -- 失效清理 ------------------------------------------------------

    @staticmethod
    def is_dead_reason(reason: str) -> bool:
        """APNs 的 reason 或 FCM 的错误码是否意味着令牌已失效。"""
        return reason in APNS_DEAD_TOKEN_REASONS or reason in FCM_DEAD_TOKEN_CODES

    def invalidate(self, token: str, reason: str, now: float = 0.0) -> bool:
        """清理一个已失效的令牌；返回是否真删掉了。"""
        meta = self._tokens.pop(token, None)
        if meta is None:
            return False
        self._by_device_app.pop((meta["device_id"], meta["app_id"]), None)
        self.dead.append({"token": token, "reason": reason, "at": now})
        return True

    def prune(self, failures: list, now: float = 0.0) -> int:
        """
        批量清理：failures 是 [(token, reason)]。
        只有"令牌失效类"的 reason 才删；其余（如 TooManyRequests）保留。
        """
        removed = 0
        for token, reason in failures:
            if self.is_dead_reason(reason) and self.invalidate(token, reason, now):
                removed += 1
        return removed

    def sendable(self, user_id: str) -> list:
        """实际可发送的令牌（已清理的会自动缺席，从而避免重试风暴）。"""
        return self.tokens_for_user(user_id)


# ---------------------------------------------------------------- 自检

def _self_check() -> int:
    n = 0

    def ok(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    r = TokenRegistry()

    # --- 注册与幂等 ---
    ok(r.register(user_id="u1", device_id="d1", app_id="com.a", token="t1", now=0) == "new",
       "first registration is new")
    ok(r.register(user_id="u1", device_id="d1", app_id="com.a", token="t1", now=60) == "unchanged",
       "re-reporting the same token on every launch is idempotent")
    ok(len(r) == 1, "duplicate report does not create a second record")
    ok(r.registrations == 2, "both launch reports are counted")

    # --- token 轮换：从备份恢复 / 新设备 / 重装系统 ---
    ok(r.register(user_id="u1", device_id="d1", app_id="com.a", token="t2", now=120) == "rotated",
       "new token for the same (device, app) rotates")
    ok(r.token_for("d1", "com.a") == "t2", "registry holds the newest token")
    ok(len(r) == 1, "old token removed on rotation")
    ok(r.dead[-1] == {"token": "t1", "reason": "rotated", "at": 120},
       "rotation is recorded as a dead token")

    # --- 同一设备、不同 App 的 token 互不复用 ---
    r.register(user_id="u1", device_id="d1", app_id="com.b", token="t3", now=130)
    ok(len(r) == 2, "a second app on the same device gets its own token")
    ok(r.token_for("d1", "com.a") == "t2" and r.token_for("d1", "com.b") == "t3",
       "tokens are scoped per (device, app)")
    # 把 com.a 的 token 报给 com.b：文档明令禁止跨 app 复用
    try:
        r.register(user_id="u1", device_id="d1", app_id="com.b", token="t2", now=140)
        raise AssertionError("cross-app token reuse must be rejected")
    except TokenRegistryError:
        n += 1
    ok(r.token_for("d1", "com.b") == "t3", "rejected reuse leaves com.b untouched")
    ok(len(r) == 2, "still two (device, app) slots")

    # --- 一个用户多设备 ---
    r2 = TokenRegistry()
    r2.register(user_id="u1", device_id="d1", app_id="com.a", token="a1", now=0)
    r2.register(user_id="u1", device_id="d2", app_id="com.a", token="a2", now=0)
    r2.register(user_id="u1", device_id="d3", app_id="com.a", token="a3", now=0)
    ok(len(r2.tokens_for_user("u1")) == 3, "one user may hold tokens for several devices")
    ok(r2.tokens_for_user("u1") == ["a1", "a2", "a3"], "tokens are returned sorted")

    # --- 失效清理 ---
    ok(TokenRegistry.is_dead_reason("Unregistered") is True, "APNs Unregistered kills the token")
    ok(TokenRegistry.is_dead_reason("BadDeviceToken") is True, "BadDeviceToken kills the token")
    ok(TokenRegistry.is_dead_reason("ExpiredToken") is True, "ExpiredToken kills the token")
    ok(TokenRegistry.is_dead_reason("DeviceTokenNotForTopic") is True, "topic mismatch kills it")
    ok(TokenRegistry.is_dead_reason("TooManyRequests") is False, "TooManyRequests is retryable")
    ok(TokenRegistry.is_dead_reason("PayloadTooLarge") is False, "payload size is not a token issue")
    ok(TokenRegistry.is_dead_reason("UNREGISTERED") is True, "FCM UNREGISTERED kills the token")
    ok(TokenRegistry.is_dead_reason("QUOTA_EXCEEDED") is False, "FCM quota error is not token-level")

    r3 = TokenRegistry()
    r3.register(user_id="u1", device_id="d1", app_id="com.a", token="x1", now=0)
    r3.register(user_id="u1", device_id="d2", app_id="com.a", token="x2", now=0)
    r3.register(user_id="u2", device_id="d3", app_id="com.a", token="x3", now=0)
    removed = r3.prune([("x1", "Unregistered"), ("x2", "TooManyRequests"),
                        ("x3", "PayloadTooLarge")], now=200)
    ok(removed == 1, "only the token-level failure is pruned")
    ok(len(r3) == 2, "retryable failures keep their tokens")
    ok(r3.token_for("d1", "com.a") is None, "pruned token is gone from the device slot")
    ok(r3.tokens_for_user("u1") == ["x2"], "user loses exactly the dead token")
    ok(r3.dead[-1]["reason"] == "Unregistered", "prune reason recorded")

    # 重复清理同一个已失效 token 不报错、也不重复计数
    ok(r3.invalidate("x1", "Unregistered") is False, "re-invalidating a removed token is a no-op")
    ok(len(r3.dead) == 1, "no duplicate dead entry")

    # 清理后不会再被选中发送 —— 重试风暴的防线
    ok("x1" not in r3.sendable("u1"), "dead token never re-enters the send set")

    # --- 空 token ---
    try:
        r3.register(user_id="u", device_id="d", app_id="a", token="", now=0)
        raise AssertionError("empty token must be rejected")
    except TokenRegistryError:
        n += 1

    # --- FCM 错误码映射（官方 SDK） ---
    ok(FCM_ERROR_TYPES["UNREGISTERED"] == "UnregisteredError", "FCM maps UNREGISTERED")
    ok(FCM_ERROR_TYPES["SENDER_ID_MISMATCH"] == "SenderIdMismatchError", "FCM maps sender mismatch")
    ok(FCM_DEAD_TOKEN_CODES == {"UNREGISTERED"}, "only UNREGISTERED is token-level in FCM")

    return n


if __name__ == "__main__":
    total = _self_check()
    print("OK: %d assertions passed" % total)
