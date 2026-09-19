#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apns_request.py 的自检。

由 split_source.py 从实现文件**整体搬来**，内容逐字节不变（OPTIMIZATION.md §1.1
的 300 行硬约束）。运行：python selfcheck_apns_request.py
"""
import json
import time

from apns_request import *  # noqa: F401,F403  自检需要实现文件的全部公开符号
# ---------------------------------------------------------------- 自检

def _self_check() -> int:
    n = 0

    def ok(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    # --- 请求头 ---
    h = build_headers("ab" * 32, topic="com.example.MyApp", push_type="alert")
    ok(h[":method"] == "POST", ":method must be POST")
    ok(h[":path"] == "/3/device/" + "ab" * 32, ":path is /3/device/<token>")
    ok(h["apns-priority"] == "10", "priority defaults to 10")
    ok(h["apns-expiration"] == "0", "expiration defaults to 0")
    ok(CANONICAL_UUID_RE.match(h["apns-id"]) is not None, "apns-id is canonical UUID")
    ok("apns-collapse-id" not in h, "collapse-id omitted by default")

    # 固定 apns-id 走校验
    fixed = "eabeae54-14a8-11e5-b60b-1697f925ec7b"
    ok(build_headers("cd" * 32, topic="t", push_type="alert", apns_id=fixed)["apns-id"] == fixed,
       "explicit canonical apns-id preserved")
    for bad in ("EABEAE54-14A8-11E5-B60B-1697F925EC7B", "eabeae5414a811e5b60b1697f925ec7b", "x"):
        try:
            canonical_uuid(bad)
            raise AssertionError("should reject %r" % bad)
        except ApnsRequestError:
            n += 1

    # priority 语义
    ok(build_headers("t1", topic="t", push_type="alert", priority=1)["apns-priority"] == "1",
       "priority 1 = power over all, no wake")
    ok(build_headers("t1", topic="t", push_type="alert", priority=5)["apns-priority"] == "5",
       "priority 5 = power considerations")
    for bad in (0, 2, 6, 11):
        try:
            build_headers("t1", topic="t", push_type="alert", priority=bad)
            raise AssertionError("should reject priority %d" % bad)
        except ApnsRequestError:
            n += 1

    # collapse-id 64 字节
    ok(build_headers("t1", topic="t", push_type="alert",
                     collapse_id="c" * 64)["apns-collapse-id"] == "c" * 64,
       "collapse-id of exactly 64 bytes is allowed")
    try:
        build_headers("t1", topic="t", push_type="alert", collapse_id="c" * 65)
        raise AssertionError("65-byte collapse-id must be rejected")
    except ApnsRequestError:
        n += 1
    # 64 字节是字节数不是字符数：中文 3 字节
    try:
        build_headers("t1", topic="t", push_type="alert", collapse_id="中" * 22)  # 66 bytes
        raise AssertionError("collapse-id limit counts bytes not chars")
    except ApnsRequestError:
        n += 1

    # push type 枚举
    ok(set(PUSH_TYPES) >= {"alert", "background", "voip", "liveactivity", "pushtotalk"},
       "push type set covers documented values")
    try:
        build_headers("t1", topic="t", push_type="silent")
        raise AssertionError("unknown push type must be rejected")
    except ApnsRequestError:
        n += 1

    # 载荷上限
    ok(PAYLOAD_LIMIT == 4096 and PAYLOAD_LIMIT_VOIP == 5120, "4KB / 5KB(VoIP) limits")
    check_payload_size(b'{"aps":{"alert":"x"}}' + b" " * (4096 - 22), "alert")
    n += 1
    try:
        check_payload_size(b"x" * 4097, "alert")
        raise AssertionError("4097 bytes must exceed alert limit")
    except ApnsRequestError:
        n += 1
    check_payload_size(b"x" * 5000, "voip")       # VoIP 放宽到 5120
    n += 1
    try:
        check_payload_size(b"x" * 5121, "voip")
        raise AssertionError("5121 bytes must exceed voip limit")
    except ApnsRequestError:
        n += 1

    # push type 与载荷一致性
    check_push_type_matches_payload("alert", {"alert": "hi"})
    n += 1
    check_push_type_matches_payload("background", {"content-available": 1})
    n += 1
    for bad_aps in ({"alert": "hi"}, {"sound": "default"}, {"badge": 1},
                    {"content-available": 1, "alert": "hi"}):
        try:
            check_push_type_matches_payload("background", bad_aps)
            raise AssertionError("background must reject %r" % bad_aps)
        except ApnsRequestError:
            n += 1
    try:
        check_push_type_matches_payload("background", {})
        raise AssertionError("background requires content-available")
    except ApnsRequestError:
        n += 1
    try:
        check_push_type_matches_payload("alert", {"content-available": 1})
        raise AssertionError("alert needs alert/sound/badge")
    except ApnsRequestError:
        n += 1

    # --- 存储语义 ---
    s = ApnsStore()
    ok(s.submit("tk", "com.a", apns_id="1", expiration=0, payload=b"{}", seq=1) == "not_stored",
       "expiration 0 => deliver once, do not store")
    ok(s.count() == 0, "expiration 0 leaves nothing in store")
    ok(s.submit("tk", "com.a", apns_id="2", expiration=time.time() + 3600,
                payload=b"{}", seq=2) == "stored", "nonzero expiration is stored")
    ok(s.submit("tk", "com.a", apns_id="3", expiration=time.time() + 3600,
                payload=b"{}", seq=3) == "stored_replaced", "newer replaces older")
    ok(s.pending("tk", "com.a")["apns_id"] == "3", "only latest is kept per bundle ID")
    s2 = ApnsStore()  # keep_latest=True：最新者胜
    s2.submit("tk", "com.a", apns_id="9", expiration=1, payload=b"{}", seq=9)
    ok(s2.submit("tk", "com.a", apns_id="4", expiration=1, payload=b"{}", seq=4)
       == "stored_kept_old", "older seq does not replace the newest")
    ok(s2.pending("tk", "com.a")["apns_id"] == "9", "newest by seq survives")
    s3 = ApnsStore(keep_latest=False)  # 文档：短时多条时「不保证保留最新」
    s3.submit("tk", "com.a", apns_id="9", expiration=1, payload=b"{}", seq=9)
    ok(s3.submit("tk", "com.a", apns_id="4", expiration=1, payload=b"{}", seq=4)
       == "stored_kept_old", "non-guaranteed mode keeps the first arrival")
    ok(s3.pending("tk", "com.a")["apns_id"] == "9",
       "stored notification is not necessarily the latest one")
    # 每个 bundle ID 独立存一条
    s.submit("tk", "com.b", apns_id="5", expiration=1, payload=b"{}", seq=5)
    ok(s.count() == 2, "storage is per (token, bundle ID), not per token")
    ok(s.flush("tk", "com.a")["apns_id"] == "3", "flush returns stored item")
    ok(s.pending("tk", "com.a") is None, "storage cleared after flush")
    ok(len(s.delivered) == 2, "one-shot delivery + 1 flush recorded")
    ok(s.delivered[0]["mode"] == "one-shot", "expiration 0 is delivered one-shot")
    ok(s.delivered[1]["mode"] == "from_storage", "stored item is delivered on flush")

    # --- 响应分派 ---
    ok(classify_response(200) == "success", "200 => success")
    ok(classify_response(500) == "retry_5xx", "500 => retry as 5xx")
    ok(classify_response(503) == "retry_5xx", "503 => retry as 5xx")
    ok(classify_response(429, "TooManyRequests") == "retry_later", "429/TooManyRequests => delayed retry")
    ok(classify_response(400, "BadDeviceToken") == "drop_token", "BadDeviceToken => drop token")
    ok(classify_response(410, "Unregistered") == "drop_token", "Unregistered => drop token")
    ok(classify_response(400, "DeviceTokenNotForTopic") == "drop_token", "DeviceTokenNotForTopic => drop")
    ok(classify_response(403, "ExpiredToken") == "drop_token", "ExpiredToken => drop")
    ok(classify_response(400, "PayloadTooLarge") == "never_retry", "PayloadTooLarge => never retry")
    ok(classify_response(403, "Forbidden") == "never_retry", "Forbidden => never retry")
    ok(classify_response(400, "BadTopic") == "fix_then_retry", "BadTopic => fix then retry")
    ok(classify_response(404, "BadPath") == "fix_then_retry", "404 => fix :path then retry")
    ok(NEVER_RETRY_REASONS == {"BadDeviceToken", "DeviceTokenNotForTopic", "Forbidden",
                               "ExpiredToken", "Unregistered", "PayloadTooLarge"},
       "never-retry set matches the doc verbatim")
    ok(DELAY_RETRY_REASONS == {"TooManyRequests"}, "only TooManyRequests is delay-retryable")

    # 错误体解析
    ok(parse_error_body(b'{"reason":"BadDeviceToken"}') == {"reason": "BadDeviceToken"},
       "error body parses to reason")
    ok(parse_error_body(b'{"reason":"Unregistered","timestamp":1454949552}')["timestamp"]
       == 1454949552, "Unregistered carries timestamp in ms")
    for body in (b'{"reason":"Nope"}', b'{"foo":1}', b'{"reason":"BadTopic","timestamp":1}'):
        try:
            parse_error_body(body)
            raise AssertionError("should reject %r" % body)
        except (ApnsRequestError, json.JSONDecodeError):
            n += 1

    # 410 不算 error condition
    ok(is_error_condition(410) is False, "410 is not an error condition")
    ok(is_error_condition(400) is True, "400 is an error condition")
    ok(is_error_condition(200) is False, "200 is not an error condition")

    # 状态码表完整性
    ok(set(STATUS_DESC) == {200, 400, 403, 404, 405, 410, 413, 429, 500, 503},
       "status code table has exactly the documented 10 codes")
    ok(len(REASON_STRINGS) == 32, "reason string table has 32 entries")

    return n


if __name__ == "__main__":
    total = _self_check()
    print("OK: %d assertions passed" % total)
