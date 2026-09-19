#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
APNs 请求构造 / 校验 / 响应分派 —— 复刻 Apple 官方文档语义。

权威来源（本轮实读全文）：
- Sending notification requests to APNs
  https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns
- Handling notification responses from APNs
  https://developer.apple.com/documentation/usernotifications/handling-notification-responses-from-apns

本模块只做「语义复刻」，不发真实网络请求。所有常量都来自上述两篇文档。
"""
from __future__ import annotations

import json
import re
import uuid

# ---------------------------------------------------------------- 常量（文档原文）

#: 载荷上限：普通通知 4 KB(4096)，VoIP 5 KB(5120)
PAYLOAD_LIMIT = 4096
PAYLOAD_LIMIT_VOIP = 5120

#: apns-collapse-id 不得超过 64 字节
COLLAPSE_ID_MAX_BYTES = 64

#: apns-priority 合法取值与语义
PRIORITY_IMMEDIATE = 10   # 立即发送
PRIORITY_POWER = 5        # 按用户设备电量考量发送
PRIORITY_NO_WAKE = 1      # 电量考量优先于一切，且不唤醒设备
PRIORITY_DEFAULT = 10     # 省略该头部时 APNs 取 10

#: apns-push-type 合法取值（文档中列出的全部值）
PUSH_TYPES = {
    "alert", "background", "voip", "complication", "fileprovider",
    "mdm", "liveactivity", "pushtotalk",
}

#: 响应 :status 取值
STATUS_DESC = {
    200: "Success.",
    400: "Bad request.",
    403: "There was an error with the certificate or with the provider's authentication token.",
    404: "The request contained an invalid :path value.",
    405: "The request used an invalid :method value. Only POST requests are supported.",
    410: "The device token is no longer active for the topic.",
    413: "The notification payload was too large.",
    429: "The server received too many requests for the same device token.",
    500: "Internal server error.",
    503: "The server is shutting down and unavailable.",
}

#: reason 错误串全集（文档 "Response error strings" 表）
REASON_STRINGS = {
    "BadCollapseId", "BadDeviceToken", "BadExpirationDate", "BadMessageId",
    "BadPriority", "BadTopic", "DeviceTokenNotForTopic", "DuplicateHeaders",
    "IdleTimeout", "InvalidPushType", "MissingDeviceToken", "MissingTopic",
    "PayloadEmpty", "TopicDisallowed", "BadCertificate",
    "BadCertificateEnvironment", "ExpiredProviderToken", "Forbidden",
    "InvalidProviderToken", "MissingProviderToken", "UnrelatedKeyIdInToken",
    "BadEnvironmentKeyIdInToken", "BadPath", "MethodNotAllowed",
    "ExpiredToken", "Unregistered", "PayloadTooLarge",
    "TooManyProviderTokenUpdates", "TooManyRequests", "InternalServerError",
    "ServiceUnavailable", "Shutdown",
}

#: 文档原文：不要重试这些 reason
NEVER_RETRY_REASONS = {
    "BadDeviceToken", "DeviceTokenNotForTopic", "Forbidden",
    "ExpiredToken", "Unregistered", "PayloadTooLarge",
}

#: 文档原文：只有这个 reason 可以「延迟后重试」
DELAY_RETRY_REASONS = {"TooManyRequests"}

#: 只有 reason 为 Unregistered 时响应体才带 timestamp 键
REASON_WITH_TIMESTAMP = {"Unregistered"}

CANONICAL_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class ApnsRequestError(ValueError):
    """请求构造/校验失败。"""


# ---------------------------------------------------------------- 请求构造与校验

def canonical_uuid(value: str | None = None) -> str:
    """apns-id 必须是规范 UUID：32 个小写十六进制 + 连字符，形如 8-4-4-4-12。"""
    if value is None:
        return str(uuid.uuid4())
    if not CANONICAL_UUID_RE.match(value):
        raise ApnsRequestError(
            "apns-id must be a canonical UUID (32 lowercase hex digits, 8-4-4-4-12)"
        )
    return value


def build_headers(token: str, *, topic: str, push_type: str,
                  apns_id: str | None = None, expiration: int = 0,
                  priority: int = PRIORITY_DEFAULT,
                  collapse_id: str | None = None) -> dict:
    """
    构造 APNs 请求头。返回 dict 而非真实 HPACK 帧（HPACK 编码规则见 README）。
    """
    if push_type not in PUSH_TYPES:
        raise ApnsRequestError("invalid apns-push-type: %r" % push_type)
    if priority not in (PRIORITY_IMMEDIATE, PRIORITY_POWER, PRIORITY_NO_WAKE):
        raise ApnsRequestError("apns-priority must be 10, 5 or 1")
    if expiration < 0:
        raise ApnsRequestError("apns-expiration must not be negative")
    if collapse_id is not None and len(collapse_id.encode("utf-8")) > COLLAPSE_ID_MAX_BYTES:
        raise ApnsRequestError("apns-collapse-id exceeds %d bytes" % COLLAPSE_ID_MAX_BYTES)
    headers = {
        ":method": "POST",
        ":path": "/3/device/%s" % token,
        "apns-topic": topic,
        "apns-push-type": push_type,
        "apns-expiration": str(expiration),
        "apns-priority": str(priority),
        "apns-id": canonical_uuid(apns_id),
    }
    if collapse_id is not None:
        headers["apns-collapse-id"] = collapse_id
    return headers


def check_payload_size(payload: bytes, push_type: str = "alert") -> None:
    """普通 4 KB(4096)，VoIP 5 KB(5120)。"""
    limit = PAYLOAD_LIMIT_VOIP if push_type == "voip" else PAYLOAD_LIMIT
    if len(payload) > limit:
        raise ApnsRequestError(
            "payload too large: %d > %d (%s)" % (len(payload), limit, push_type)
        )


def check_push_type_matches_payload(push_type: str, aps: dict) -> None:
    """
    apns-push-type 必须如实反映载荷内容。
    文档原文：mismatch 时 APNs 可能报错、延迟投递，或者直接丢弃。
    """
    if push_type == "background":
        if "content-available" not in aps:
            raise ApnsRequestError("push-type background requires aps.content-available")
        for k in ("alert", "sound", "badge"):
            if k in aps:
                raise ApnsRequestError(
                    "background payload must not contain aps.%s" % k
                )
    elif push_type == "alert":
        if not ({"alert", "sound", "badge"} & set(aps)):
            raise ApnsRequestError(
                "push-type alert requires at least one of alert/sound/badge in aps"
            )
    else:
        # 其余 push type（voip / complication / mdm / liveactivity ...）
        # 文档只要求「如实反映」，本 demo 不做更强的断言。
        pass


# ---------------------------------------------------------------- 存储语义（best-effort）

class ApnsStore:
    """
    复刻文档的离线存储语义：

    - APNs 每个 bundle ID **只保留一条**通知；
    - 多数情况下保留最新一条，但「短时间内存储多条」时该行为不保证
      （文档原文: this behavior isn't always guaranteed ... in a short duration）；
    - 存储时长 30 天或更短，取决于 apns-expiration；
    - apns-expiration == 0 表示只投递一次、不存储。
    """

    def __init__(self, keep_latest: bool = True):
        self._store: dict[tuple[str, str], dict] = {}
        self.keep_latest = keep_latest
        self.delivered: list[dict] = []

    def submit(self, token: str, topic: str, *, apns_id: str,
               expiration: int, payload: bytes, seq: int) -> str:
        """
        seq 是本次提交的单调序号，用来演示「最新一条」与「不保证」两种策略的差别。
        返回 'stored' / 'not_stored' / 'discarded_by_expiration'
        """
        if expiration == 0:
            self.delivered.append({"token": token, "apns_id": apns_id, "mode": "one-shot"})
            return "not_stored"
        key = (token, topic)
        old = self._store.get(key)
        if old is None:
            self._store[key] = {"apns_id": apns_id, "seq": seq, "payload": payload}
            return "stored"
        if self.keep_latest and seq >= old["seq"]:
            self._store[key] = {"apns_id": apns_id, "seq": seq, "payload": payload}
            return "stored_replaced"
        # keep_latest == False 时先到者胜，用来演示文档所说的
        # 「短时间内存储多条时并不保证保留最新一条」
        return "stored_kept_old"

    def pending(self, token: str, topic: str):
        return self._store.get((token, topic))

    def count(self) -> int:
        return len(self._store)

    def flush(self, token: str, topic: str):
        item = self._store.pop((token, topic), None)
        if item is not None:
            self.delivered.append({"token": token, "apns_id": item["apns_id"],
                                   "mode": "from_storage"})
        return item


# ---------------------------------------------------------------- 响应分派

def classify_response(status: int, reason: str | None = None) -> str:
    """
    把 (status, reason) 映射成服务端应采取的动作：

    - 'success'        200，投递已受理
    - 'drop_token'     令牌类失效，必须清理设备令牌（不可重试）
    - 'never_retry'    文档明列的不可重试 reason
    - 'retry_later'    TooManyRequests，延迟后重试
    - 'retry_5xx'      5XX，文档建议「15 分钟后」重试并可退避
    - 'fix_then_retry' 其它 4XX，修好 reason 指明的问题后可重试
    """
    if status == 200:
        return "success"
    if reason in NEVER_RETRY_REASONS:
        return "drop_token" if reason in {
            "BadDeviceToken", "DeviceTokenNotForTopic", "ExpiredToken", "Unregistered"
        } else "never_retry"
    if reason in DELAY_RETRY_REASONS:
        return "retry_later"
    if 500 <= status < 600:
        return "retry_5xx"
    return "fix_then_retry"


def parse_error_body(body: bytes) -> dict:
    """错误响应体是 JSON 字典，含 reason；仅 Unregistered 时带 timestamp(毫秒)。"""
    data = json.loads(body.decode("utf-8"))
    if "reason" not in data:
        raise ApnsRequestError("error body without reason key")
    if data["reason"] not in REASON_STRINGS:
        raise ApnsRequestError("unknown reason: %r" % data["reason"])
    if "timestamp" in data and data["reason"] not in REASON_WITH_TIMESTAMP:
        raise ApnsRequestError(
            "timestamp key only allowed when reason is Unregistered"
        )
    return data


def is_error_condition(status: int) -> bool:
    """文档原文：status 410 不被当作 error condition。"""
    return status != 200 and status != 410



if __name__ == "__main__":
    from selfcheck_apns_request import _self_check

    print("OK: %d assertions passed" % _self_check())
