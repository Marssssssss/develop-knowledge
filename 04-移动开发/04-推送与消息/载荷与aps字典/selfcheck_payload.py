#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""payload.py 的自检。

由 split_source.py 从实现文件**整体搬来**，内容逐字节不变。运行：python selfcheck_payload.py
"""
from payload import *  # noqa: F401,F403  自检需要实现文件的全部公开符号
# ---------------------------------------------------------------- 自检

def _self_check() -> int:
    n = 0

    def ok(cond, msg):
        nonlocal n
        assert cond, msg
        n += 1

    # --- 拆分：自定义键必须是 aps 的同级 ---
    p = {"aps": {"alert": "Hello"}, "gameID": "12345678"}
    aps, custom = split_payload(p)
    ok(aps == {"alert": "Hello"}, "aps keeps only Apple-defined keys")
    ok(custom == {"gameID": "12345678"}, "custom keys live as peers of aps")
    ok(dropped_aps_keys(p) == [], "no key dropped in the well-formed case")

    p2 = {"aps": {"alert": "Hi", "gameID": "1"}, "acme": 42}
    aps2, custom2 = split_payload(p2)
    ok("gameID" not in aps2, "custom key inside aps is ignored by APNs")
    ok(dropped_aps_keys(p2) == ["gameID"], "dropped_aps_keys reports the ignored key")
    ok(custom2 == {"acme": 42}, "peer custom key survives")

    try:
        split_payload({"noaps": 1})
        raise AssertionError("payload without aps must be rejected")
    except PayloadError:
        n += 1

    # --- alert 归一化 ---
    ok(normalize_alert("Bob") == {"body": "Bob"}, "string alert == body")
    ok(normalize_alert({"title": "T", "body": "B"}) == {"title": "T", "body": "B"},
       "dict alert preserved")
    try:
        normalize_alert({"title": "T", "nope": 1})
        raise AssertionError("unknown alert key must be rejected")
    except PayloadError:
        n += 1
    try:
        normalize_alert(42)
        raise AssertionError("alert must be string or dict")
    except PayloadError:
        n += 1

    # --- 本地化：按出现顺序替换 %@ ---
    ok(localize("Hello %@ and %@", ["A", "B"]) == "Hello A and B", "sequential %@ replacement")
    ok(localize("%@ %@ %@", ["1", "2", "3"]) == "1 2 3", "three placeholders")
    ok(localize("Hi %@", ["A", "B"]) == "Hi A", "extra args ignored")
    ok(localize("Hi %@ and %@", ["A"]) == "Hi A and %@",
       "insufficient args leave %@ as-is (implementation choice)")
    ok(localize("No placeholder", ["A"]) == "No placeholder", "no-op when no placeholder")

    strings = {
        "GAME_PLAY_REQUEST_FORMAT": "%@ wants to play with %@",
        "TITLE": "Game Request",
    }
    r = resolve_alert({"loc-key": "GAME_PLAY_REQUEST_FORMAT",
                       "loc-args": ["Shelly", "Rick"]}, strings)
    ok(r["body"] == "Shelly wants to play with Rick", "loc-key + loc-args resolves body")
    r2 = resolve_alert({"title-loc-key": "TITLE",
                        "loc-key": "GAME_PLAY_REQUEST_FORMAT",
                        "loc-args": ["A", "B"]}, strings)
    ok(r2["title"] == "Game Request", "title-loc-key resolves title")
    ok(r2["body"] == "A wants to play with B", "body still localized")
    r3 = resolve_alert({"title": "Explicit",
                        "loc-key": "GAME_PLAY_REQUEST_FORMAT",
                        "loc-args": ["A", "B"]}, strings)
    ok(r3["title"] == "Explicit", "explicit title wins over localization")
    try:
        resolve_alert({"loc-key": "MISSING"}, strings)
        raise AssertionError("missing localized string must be reported")
    except PayloadError:
        n += 1

    # --- 校验 ---
    ok(validate_payload({"aps": {"alert": "Hello"}}) == [], "minimal alert payload is valid")
    ok(validate_payload({"aps": {"badge": 0}}) == [], "badge 0 clears the badge")
    ok(validate_payload({"aps": {"badge": 9, "sound": "bingbong.aiff"},
                         "messageID": "ABCDEFGHIJ"}) == [], "Listing-2 style payload valid")
    ok(validate_payload({"aps": {"sound": "default"}}) == [], "'default' plays system sound")
    ok(validate_payload({"aps": {"sound": {"critical": 1, "name": "s.caf", "volume": 0.8}}}) == [],
       "critical alert sound dict is valid")
    ok("volume" in str(validate_payload({"aps": {"sound": {"critical": 1, "volume": 1.5}}})),
       "volume must be within 0.0..1.0")
    ok(validate_payload({"aps": {"content-available": 1}}) == [], "content-available 1 ok")
    ok(validate_payload({"aps": {"content-available": 0}}) != [], "content-available must be 1")
    ok(validate_payload({"aps": {"mutable-content": 1}}) == [], "mutable-content 1 ok")
    ok(validate_payload({"aps": {"mutable-content": 0}}) != [], "mutable-content must be 1")
    ok(validate_payload({"aps": {"badge": "9"}}) != [], "badge must be a number")
    ok(validate_payload({"aps": {"alert": {"title": 1}}}) != [], "alert title must be a string")
    for lvl in INTERRUPTION_LEVELS:
        ok(validate_payload({"aps": {"alert": "x", "interruption-level": lvl}}) == [],
           "interruption-level %s accepted" % lvl)
    ok(validate_payload({"aps": {"alert": "x", "interruption-level": "urgent"}}) != [],
       "unknown interruption-level rejected")
    ok(validate_payload({"aps": {"alert": "x", "relevance-score": 0.75}}) == [],
       "relevance-score 0..1 accepted")
    ok(validate_payload({"aps": {"alert": "x", "relevance-score": 1.5}}) != [],
       "relevance-score above 1 rejected")
    ok(validate_payload({"aps": {"relevance-score": 100}, "attributes-type": "A"},
                        is_live_activity=True) == [],
       "Live Activity may set any Double relevance-score")
    ok(validate_payload({"aps": {}, "obj": object()}) != [],
       "custom value must be a primitive type")
    for v in ("s", 1, 1.5, True, [1, 2], {"a": "b"}):
        ok(validate_payload({"aps": {"alert": "x"}, "k": v}) == [],
           "primitive custom value %r accepted" % (v,))

    # --- 大小按字节 ---
    ok(payload_bytes({"aps": {"alert": "a"}}) < payload_bytes({"aps": {"alert": "中"}}),
       "size is counted in UTF-8 bytes, not characters")
    ok(payload_bytes({"aps": {"alert": "中"}}) == payload_bytes({"aps": {"alert": "中"}}),
       "serialization is deterministic")

    # --- 键表完整性 ---
    ok({"alert", "badge", "sound", "category", "thread-id"} <= APS_KEYS,
       "Table 1 core keys present")
    ok({"loc-key", "loc-args", "title-loc-key", "title-loc-args"} <= ALERT_KEYS,
       "Table 2 localization keys present")
    ok(SOUND_KEYS == {"critical", "name", "volume"}, "Table 3 sound dict keys")

    return n


if __name__ == "__main__":
    total = _self_check()
    print("OK: %d assertions passed" % total)
