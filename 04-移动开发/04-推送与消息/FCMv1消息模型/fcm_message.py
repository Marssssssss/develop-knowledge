#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FCM HTTP v1 消息模型的构造与校验 —— 复刻官方 Admin SDK 的编码语义。

权威来源（本轮实读源码，firebase.google.com 不可达，故以官方仓库源码为依据）：
- firebase_admin/_messaging_encoder.py（MessageEncoder / _Validators）
  https://github.com/firebase/firebase-admin-python/blob/master/firebase_admin/_messaging_encoder.py
- firebase_admin/messaging.py（FCM_URL / FCM_ERROR_TYPES）
  https://github.com/firebase/firebase-admin-python/blob/master/firebase_admin/messaging.py

本模块用普通 dict 代替 SDK 的类，校验与编码规则与 SDK 逐条对齐。
"""
from __future__ import annotations

import datetime
import math
import numbers
import re

#: 官方 SDK：_MessagingService.FCM_URL
FCM_URL = "https://fcm.googleapis.com/v1/projects/{0}/messages:send"
FCM_BATCH_URL = "https://fcm.googleapis.com/batch"

#: 目标的四个字段，**必须恰好指定一个**
TARGET_FIELDS = ("fid", "token", "topic", "condition")

#: 分析标签：可打印的 URL-safe 字符，长度 1~50
ANALYTICS_LABEL_RE = re.compile(r"^[a-zA-Z0-9-_.~%]{1,50}$")

#: topic 名：去掉 /topics/ 前缀后只允许这些字符
TOPIC_NAME_RE = re.compile(r"^[a-zA-Z0-9-_\.~%]+$")

#: 颜色：#RRGGBB（通知）；#RRGGBB 或 #RRGGBBAA（呼吸灯）
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
COLOR_RE_WITH_ALPHA = re.compile(r"^#[0-9a-fA-F]{6}$|^#[0-9a-fA-F]{8}$")

ANDROID_PRIORITIES = ("high", "normal")
NOTIFICATION_PRIORITIES = ("min", "low", "default", "high", "max")
VISIBILITIES = ("private", "public", "secret")
PROXIES = ("allow", "deny", "if_priority_lowered")


class FcmMessageError(ValueError):
    """消息不合法。"""


# ---------------------------------------------------------------- 校验器

def remove_null_values(d: dict) -> dict:
    """官方 SDK：剔除值为 None / [] / {} 的键（注意 False 与 0 会被保留）。"""
    return {k: v for k, v in d.items() if v not in [None, [], {}]}


def check_string(label: str, value, non_empty: bool = False):
    if value is None:
        return None
    if not isinstance(value, str):
        raise FcmMessageError("%s must be a string." % label)
    if non_empty and not value:
        raise FcmMessageError("%s must be a non-empty string." % label)
    return value


def check_string_dict(label: str, value):
    """FCM 的 data 只能是字符串到字符串的映射。"""
    if value is None or value == {}:
        return None
    if not isinstance(value, dict):
        raise FcmMessageError("%s must be a dictionary." % label)
    if [k for k in value if not isinstance(k, str)]:
        raise FcmMessageError("%s must not contain non-string keys." % label)
    if [v for v in value.values() if not isinstance(v, str)]:
        raise FcmMessageError("%s must not contain non-string values." % label)
    return value


def check_analytics_label(label: str, value):
    value = check_string(label, value)
    if value is not None and not ANALYTICS_LABEL_RE.match(value):
        raise FcmMessageError("Malformed %s." % label)
    return value


def check_boolean(label: str, value):
    if value is None:
        return None
    if not isinstance(value, bool):
        raise FcmMessageError("%s must be a boolean." % label)
    return value


def check_number(label: str, value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, numbers.Number):
        raise FcmMessageError("%s must be a number." % label)
    return value


# ---------------------------------------------------------------- 编码器

def encode_ttl(ttl) -> str | None:
    """
    官方 SDK：timedelta / 秒数 → duration 字符串。
    整数秒 -> "3s"；带小数 -> "3.500000000s"（纳秒固定 9 位）。
    """
    if ttl is None:
        return None
    if isinstance(ttl, numbers.Number):
        ttl = datetime.timedelta(seconds=ttl)
    if not isinstance(ttl, datetime.timedelta):
        raise FcmMessageError(
            "AndroidConfig.ttl must be a duration in seconds or an instance of datetime.timedelta."
        )
    total_seconds = ttl.total_seconds()
    if total_seconds < 0:
        raise FcmMessageError("AndroidConfig.ttl must not be negative.")
    seconds = int(math.floor(total_seconds))
    nanos = int((total_seconds - seconds) * 1e9)
    if nanos:
        return "%d.%ss" % (seconds, str(nanos).zfill(9))
    return "%ds" % seconds


def sanitize_topic_name(topic):
    """去掉 /topics/ 前缀，再校验字符集。"""
    if not topic:
        return None
    prefix = "/topics/"
    if topic.startswith(prefix):
        topic = topic[len(prefix):]
    if not TOPIC_NAME_RE.match(topic):
        raise FcmMessageError("Malformed topic name.")
    return topic


def encode_android_notification(n: dict) -> dict | None:
    if n is None:
        return None
    result = {
        "title": check_string("AndroidNotification.title", n.get("title")),
        "body": check_string("AndroidNotification.body", n.get("body")),
        "icon": check_string("AndroidNotification.icon", n.get("icon")),
        "color": check_string("AndroidNotification.color", n.get("color")),
        "tag": check_string("AndroidNotification.tag", n.get("tag")),
        "click_action": check_string("AndroidNotification.click_action",
                                     n.get("click_action")),
        "channel_id": check_string("AndroidNotification.channel_id",
                                   n.get("channel_id")),
        "notification_priority": check_string("AndroidNotification.priority",
                                              n.get("priority"), non_empty=True),
        "visibility": check_string("AndroidNotification.visibility",
                                   n.get("visibility")),
        "proxy": check_string("AndroidNotification.proxy", n.get("proxy")),
    }
    result = remove_null_values(result)
    color = result.get("color")
    if color and not COLOR_RE.match(color):
        raise FcmMessageError("AndroidNotification.color must be in the form #RRGGBB.")
    priority = result.get("notification_priority")
    if priority:
        if priority not in NOTIFICATION_PRIORITIES:
            raise FcmMessageError(
                'AndroidNotification.priority must be "default", "min", "low", "high" or "max".'
            )
        result["notification_priority"] = "PRIORITY_" + priority.upper()
    visibility = result.get("visibility")
    if visibility:
        if visibility not in VISIBILITIES:
            raise FcmMessageError(
                'AndroidNotification.visibility must be "private", "public" or "secret".'
            )
        result["visibility"] = visibility.upper()
    proxy = result.get("proxy")
    if proxy:
        if proxy not in PROXIES:
            raise FcmMessageError(
                'AndroidNotification.proxy must be "allow", "deny" or "if_priority_lowered".'
            )
        result["proxy"] = proxy.upper()
    return result


def encode_android(a: dict) -> dict | None:
    if a is None:
        return None
    result = {
        "collapse_key": check_string("AndroidConfig.collapse_key", a.get("collapse_key")),
        "data": check_string_dict("AndroidConfig.data", a.get("data")),
        "notification": encode_android_notification(a.get("notification")),
        "priority": check_string("AndroidConfig.priority", a.get("priority"),
                                 non_empty=True),
        "restricted_package_name": check_string(
            "AndroidConfig.restricted_package_name", a.get("restricted_package_name")),
        "ttl": encode_ttl(a.get("ttl")),
        "direct_boot_ok": check_boolean("AndroidConfig.direct_boot_ok",
                                        a.get("direct_boot_ok")),
        "bandwidth_constrained_ok": check_boolean(
            "AndroidConfig.bandwidth_constrained_ok", a.get("bandwidth_constrained_ok")),
        "restricted_satellite_ok": check_boolean(
            "AndroidConfig.restricted_satellite_ok", a.get("restricted_satellite_ok")),
    }
    result = remove_null_values(result)
    priority = result.get("priority")
    if priority and priority not in ANDROID_PRIORITIES:
        raise FcmMessageError('AndroidConfig.priority must be "high" or "normal".')
    return result


def encode_aps(aps: dict) -> dict:
    result = {
        "alert": aps.get("alert"),
        "badge": check_number("Aps.badge", aps.get("badge")),
        "sound": aps.get("sound"),
        "category": check_string("Aps.category", aps.get("category")),
        "thread-id": check_string("Aps.thread-id", aps.get("thread_id")),
    }
    # 官方 SDK：True 才写，且写成数值 1（不是 true）
    if aps.get("content_available") is True:
        result["content-available"] = 1
    if aps.get("mutable_content") is True:
        result["mutable-content"] = 1
    return remove_null_values(result)


def encode_apns(a: dict) -> dict | None:
    if a is None:
        return None
    payload = a.get("payload") or {}
    aps = payload.get("aps")
    result = {
        "headers": check_string_dict("APNSConfig.headers", a.get("headers")),
        "fcm_options": {"analytics_label": check_analytics_label(
            "APNSFCMOptions.analytics_label",
            (a.get("fcm_options") or {}).get("analytics_label"))}
        if a.get("fcm_options") else None,
        "live_activity_token": check_string("APNSConfig.live_activity_token",
                                            a.get("live_activity_token")),
    }
    if aps is not None:
        encoded = {"aps": encode_aps(aps)}
        for k, v in (payload.get("custom_data") or {}).items():
            encoded[k] = v
        result["payload"] = remove_null_values(encoded)
    result = remove_null_values(result)
    return result or None


def encode_message(msg: dict) -> dict:
    result = {
        "android": encode_android(msg.get("android")),
        "apns": encode_apns(msg.get("apns")),
        "condition": check_string("Message.condition", msg.get("condition"),
                                  non_empty=True),
        "data": check_string_dict("Message.data", msg.get("data")),
        "notification": msg.get("notification"),
        "fid": check_string("Message.fid", msg.get("fid"), non_empty=True),
        "token": check_string("Message.token", msg.get("token"), non_empty=True),
        "topic": check_string("Message.topic", msg.get("topic"), non_empty=True),
        "webpush": msg.get("webpush"),
        "fcm_options": msg.get("fcm_options"),
    }
    result["topic"] = sanitize_topic_name(result.get("topic"))
    result = remove_null_values(result)
    target_count = sum(t in result for t in TARGET_FIELDS)
    if target_count != 1:
        raise FcmMessageError(
            "Exactly one of fid, token, topic or condition must be specified."
        )
    return result


def fcm_send_url(project_id: str) -> str:
    return FCM_URL.format(project_id)


def classify_error(code: str) -> str:
    """官方 SDK 的错误码 → 异常类型映射（节选）。"""
    return {
        "APNS_AUTH_ERROR": "ThirdPartyAuthError",
        "QUOTA_EXCEEDED": "QuotaExceededError",
        "SENDER_ID_MISMATCH": "SenderIdMismatchError",
        "THIRD_PARTY_AUTH_ERROR": "ThirdPartyAuthError",
        "UNREGISTERED": "UnregisteredError",
    }.get(code, "UnknownError")



if __name__ == "__main__":
    from selfcheck_fcm_message import _self_check

    print("OK: %d assertions passed" % _self_check())
