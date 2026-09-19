#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fcm_message.py 的自检。

由 split_source.py 从实现文件**整体搬来**，内容逐字节不变。运行：python selfcheck_fcm_message.py
"""
import datetime

from fcm_message import *  # noqa: F401,F403  自检需要实现文件的全部公开符号
# ---------------------------------------------------------------- 自检

def _self_check() -> int:
    n = 0

    def ok(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    def bad(fn, msg):
        nonlocal n
        try:
            fn()
            raise AssertionError("should have failed: " + msg)
        except FcmMessageError:
            n += 1

    # --- 目标四选一 ---
    ok(encode_message({"token": "tk"}) == {"token": "tk"}, "token-only message encodes")
    ok(encode_message({"topic": "news"}) == {"topic": "news"}, "topic-only message encodes")
    ok(encode_message({"topic": "/topics/news"}) == {"topic": "news"},
       "/topics/ prefix is stripped")
    ok(encode_message({"condition": "'a' in topics"}) == {"condition": "'a' in topics"},
       "condition-only message encodes")
    bad(lambda: encode_message({}), "no target")
    bad(lambda: encode_message({"token": "tk", "topic": "news"}), "two targets")
    bad(lambda: encode_message({"token": "tk", "condition": "c", "topic": "n"}),
        "three targets")
    bad(lambda: encode_message({"topic": "news!"}), "topic with illegal character")
    bad(lambda: encode_message({"topic": "/topics/"}), "empty topic after prefix strip")

    # --- Android 优先级 ---
    ok(encode_android({"priority": "high"})["priority"] == "high", "android high kept verbatim")
    ok(encode_android({"priority": "normal"})["priority"] == "normal", "android normal kept")
    bad(lambda: encode_android({"priority": "HIGH"}), "android priority is case-sensitive")
    bad(lambda: encode_android({"priority": "max"}), "'max' belongs to notification, not config")
    bad(lambda: encode_android({"priority": ""}), "empty priority rejected")

    # --- 通知优先级 / 可见性 / 代理的枚举映射 ---
    for p, want in (("min", "PRIORITY_MIN"), ("low", "PRIORITY_LOW"),
                    ("default", "PRIORITY_DEFAULT"), ("high", "PRIORITY_HIGH"),
                    ("max", "PRIORITY_MAX")):
        got = encode_android_notification({"priority": p})["notification_priority"]
        ok(got == want, "notification priority %s -> %s" % (p, want))
    for v in VISIBILITIES:
        ok(encode_android_notification({"visibility": v})["visibility"] == v.upper(),
           "visibility %s -> %s" % (v, v.upper()))
    for x in PROXIES:
        ok(encode_android_notification({"proxy": x})["proxy"] == x.upper(),
           "proxy %s -> %s" % (x, x.upper()))
    ok(encode_android_notification({"proxy": "if_priority_lowered"})["proxy"]
       == "IF_PRIORITY_LOWERED", "if_priority_lowered maps to IF_PRIORITY_LOWERED")
    bad(lambda: encode_android_notification({"priority": "urgent"}), "bad notification priority")
    bad(lambda: encode_android_notification({"visibility": "hidden"}), "bad visibility")
    bad(lambda: encode_android_notification({"proxy": "block"}), "bad proxy")

    # --- 颜色 ---
    ok(encode_android_notification({"color": "#A1B2C3"})["color"] == "#A1B2C3", "#RRGGBB ok")
    bad(lambda: encode_android_notification({"color": "#A1B2C3FF"}),
        "notification color does not accept alpha")
    bad(lambda: encode_android_notification({"color": "A1B2C3"}), "color needs leading #")

    # --- TTL ---
    ok(encode_ttl(0) == "0s", "0 -> '0s'")
    ok(encode_ttl(3) == "3s", "3 -> '3s'")
    ok(encode_ttl(datetime.timedelta(seconds=3.5)) == "3.500000000s",
       "3.5s -> '3.500000000s' (9-digit nanos)")
    ok(encode_ttl(datetime.timedelta(milliseconds=1500)) == "1.500000000s", "1.5s via timedelta")
    ok(encode_ttl(datetime.timedelta(seconds=3600)) == "3600s", "1h -> '3600s'")
    bad(lambda: encode_ttl(-1), "negative ttl")
    bad(lambda: encode_ttl(datetime.timedelta(seconds=-0.1)), "negative timedelta")
    bad(lambda: encode_ttl("3s"), "ttl must be a number or timedelta")
    ok(encode_android({"ttl": 10})["ttl"] == "10s", "ttl encoded inside android config")

    # --- data 必须是字符串字典 ---
    ok(encode_android({"data": {"k": "v"}})["data"] == {"k": "v"}, "string dict accepted")
    bad(lambda: encode_android({"data": {"k": 1}}), "non-string data value")
    bad(lambda: encode_android({"data": {1: "v"}}), "non-string data key")
    ok(remove_null_values({"a": None, "b": [], "c": {}, "d": 0, "e": False})
       == {"d": 0, "e": False}, "remove_null_values keeps 0 and False")

    # --- 布尔位 ---
    ok(encode_android({"direct_boot_ok": True})["direct_boot_ok"] is True,
       "direct_boot_ok True kept")
    ok(encode_android({"direct_boot_ok": False})["direct_boot_ok"] is False,
       "direct_boot_ok False is kept, not dropped")
    bad(lambda: encode_android({"direct_boot_ok": "yes"}), "direct_boot_ok must be boolean")
    ok(encode_android({"bandwidth_constrained_ok": True})["bandwidth_constrained_ok"] is True,
       "bandwidth_constrained_ok supported")
    ok(encode_android({"restricted_satellite_ok": True})["restricted_satellite_ok"] is True,
       "restricted_satellite_ok supported")

    # --- APNs 覆写 ---
    aps = encode_aps({"alert": "hi", "badge": 3, "content_available": True})
    ok(aps["content-available"] == 1, "content_available True -> numeric 1")
    ok("mutable-content" not in aps, "mutable-content omitted when not set")
    ok(encode_aps({"mutable_content": True})["mutable-content"] == 1,
       "mutable_content True -> numeric 1")
    ok(encode_aps({"content_available": False}).get("content-available") is None,
       "False does not emit content-available")
    ok(encode_aps({"thread_id": "g1"})["thread-id"] == "g1", "thread_id -> 'thread-id'")
    bad(lambda: encode_aps({"badge": "3"}), "badge must be a number")
    bad(lambda: encode_aps({"badge": True}), "bool is not a valid badge")
    m = encode_message({"token": "tk",
                        "apns": {"payload": {"aps": {"alert": "hi"},
                                             "custom_data": {"gameID": "1"}}}})
    ok(m["apns"]["payload"] == {"aps": {"alert": "hi"}, "gameID": "1"},
       "custom_data becomes peers of aps")
    ok(encode_message({"token": "t", "apns": {"live_activity_token": "la"}})
       ["apns"]["live_activity_token"] == "la", "live_activity_token supported")

    # --- analytics label ---
    ok(check_analytics_label("x", "a-b_c.d~e%f") is not None, "url-safe label accepted")
    bad(lambda: check_analytics_label("x", ""), "empty label rejected")
    bad(lambda: check_analytics_label("x", "a" * 51), "51-char label rejected")
    ok(check_analytics_label("x", "a" * 50) == "a" * 50, "50-char label accepted")
    bad(lambda: check_analytics_label("x", "has space"), "space rejected")
    bad(lambda: check_analytics_label("x", "a/b"), "slash rejected")

    # --- URL 与错误码 ---
    ok(fcm_send_url("proj-1") == "https://fcm.googleapis.com/v1/projects/proj-1/messages:send",
       "send URL template")
    ok(classify_error("UNREGISTERED") == "UnregisteredError", "UNREGISTERED mapping")
    ok(classify_error("QUOTA_EXCEEDED") == "QuotaExceededError", "QUOTA_EXCEEDED mapping")
    ok(classify_error("SENDER_ID_MISMATCH") == "SenderIdMismatchError", "sender mismatch mapping")
    ok(classify_error("NOPE") == "UnknownError", "unknown code falls through")

    return n


if __name__ == "__main__":
    total = _self_check()
    print("OK: %d assertions passed" % total)
