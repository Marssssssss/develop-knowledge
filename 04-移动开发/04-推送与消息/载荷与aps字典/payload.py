#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
APNs 远程通知载荷（JSON payload）的构造 / 拆分 / 校验 / 本地化。

权威来源（本轮实读全文）：
- Generating a remote notification
  https://developer.apple.com/documentation/usernotifications/generating-a-remote-notification
  其中 Table 1 = aps 字典键、Table 2 = alert 字典键、Table 3 = critical alert 的 sound 字典键。
"""
from __future__ import annotations

import json

# ---------------------------------------------------------------- 键表（Table 1/2/3）

#: Table 1：aps 字典里 Apple 定义的键
APS_KEYS = {
    "alert", "badge", "sound", "thread-id", "category",
    "content-available", "mutable-content", "target-content-id",
    "interruption-level", "relevance-score", "filter-criteria",
    "stale-date", "content-state", "timestamp", "event",
    "dismissal-date", "attributes-type", "attributes",
}

#: Table 2：alert 字典的键
ALERT_KEYS = {
    "title", "subtitle", "body", "launch-image",
    "loc-key", "loc-args",
    "title-loc-key", "title-loc-args",
    "subtitle-loc-key", "subtitle-loc-args",
}

#: Table 3：critical alert 用的 sound 字典的键
SOUND_KEYS = {"critical", "name", "volume"}

#: interruption-level 的四个字符串取值
INTERRUPTION_LEVELS = {"passive", "active", "time-sensitive", "critical"}

#: 自定义键的值只允许 primitive 类型
PRIMITIVE_TYPES = (dict, list, str, int, float, bool)


class PayloadError(ValueError):
    """载荷不合法。"""


# ---------------------------------------------------------------- 拆分

def split_payload(payload: dict) -> tuple[dict, dict]:
    """
    把载荷拆成 (aps, custom)。

    文档原文：不要把自定义键放进 aps 字典，**APNs 会忽略它们**；
    自定义键必须作为 aps 的同级（peers）出现。

    因此本函数对 aps 内的未知键采取"丢弃"而非报错 —— 这正是 APNs 的行为。
    """
    if "aps" not in payload:
        raise PayloadError("payload must contain an 'aps' dictionary")
    aps_in = payload["aps"]
    if not isinstance(aps_in, dict):
        raise PayloadError("aps must be a dictionary")
    aps = {k: v for k, v in aps_in.items() if k in APS_KEYS}
    custom = {k: v for k, v in payload.items() if k != "aps"}
    return aps, custom


def dropped_aps_keys(payload: dict) -> list:
    """返回被 APNs 忽略掉的 aps 内自定义键（用于开发期自检）。"""
    aps_in = payload.get("aps", {})
    return [k for k in aps_in if k not in APS_KEYS]


# ---------------------------------------------------------------- alert 归一化

def normalize_alert(alert) -> dict:
    """
    alert 可以是 String 或 Dictionary。
    文档：指定字符串时，该字符串作为 body 文本展示。
    """
    if isinstance(alert, str):
        return {"body": alert}
    if isinstance(alert, dict):
        unknown = set(alert) - ALERT_KEYS
        if unknown:
            raise PayloadError("unknown alert keys: %s" % sorted(unknown))
        # Table 2：title / subtitle / body / launch-image 是字符串，*-loc-args 是字符串数组
        for k in ("title", "subtitle", "body", "launch-image"):
            if k in alert and not isinstance(alert[k], str):
                raise PayloadError("alert.%s must be a string" % k)
        for k in ("loc-args", "title-loc-args", "subtitle-loc-args"):
            if k in alert:
                if not isinstance(alert[k], list) or \
                        not all(isinstance(x, str) for x in alert[k]):
                    raise PayloadError("alert.%s must be an array of strings" % k)
        return dict(alert)
    raise PayloadError("aps.alert must be a string or a dictionary")


# ---------------------------------------------------------------- 本地化

def localize(template: str, args: list) -> str:
    """
    按文档中 *-loc-args 的语义做替换：

    "Each %@ character in the string specified by <key> is replaced by a value
     from this array. The first item in the array replaces the first instance
     of the %@ character ... and so on."

    即**按出现顺序**替换，而不是按参数下标寻址。参数多于占位符则多余参数被忽略；
    文档未规定参数不足时的行为，本实现保留剩余 %@ 原样（README 已标注为实现选择）。
    """
    out = []
    idx = 0
    i = 0
    while i < len(template):
        if template.startswith("%@", i):
            if idx < len(args):
                out.append(str(args[idx]))
                idx += 1
            else:
                out.append("%@")
            i += 2
        else:
            out.append(template[i])
            i += 1
    return "".join(out)


def resolve_alert(alert, strings: dict) -> dict:
    """
    用 App bundle 里的 Localizable.strings 解析 loc-* 系列键，得到最终 title/subtitle/body。

    解析顺序（文档语义）：
      - 有 loc-key / title-loc-key / subtitle-loc-key 时，从 strings 取模板；
      - 对应的 *-loc-args 提供替换参数；
      - 显式给的 title/subtitle/body 优先于本地化结果（此时不查 strings）。
    """
    a = normalize_alert(alert)
    out = {}
    for plain_key, loc_key, loc_args_key in (
        ("title", "title-loc-key", "title-loc-args"),
        ("subtitle", "subtitle-loc-key", "subtitle-loc-args"),
        ("body", "loc-key", "loc-args"),
    ):
        if plain_key in a:
            out[plain_key] = a[plain_key]
            continue
        if loc_key in a:
            name = a[loc_key]
            if name not in strings:
                raise PayloadError("missing localized string: %r" % name)
            out[plain_key] = localize(strings[name], a.get(loc_args_key, []))
    return out


# ---------------------------------------------------------------- 校验

def validate_payload(payload: dict, *, is_live_activity: bool = False) -> list:
    """返回问题描述列表；空列表表示合法。"""
    problems = []
    aps, custom = split_payload(payload)

    if not aps and not custom:
        problems.append("payload is empty")

    # alert
    if "alert" in aps:
        try:
            normalize_alert(aps["alert"])
        except PayloadError as e:
            problems.append(str(e))

    # badge：数字；0 表示清除
    if "badge" in aps and not isinstance(aps["badge"], int):
        problems.append("aps.badge must be a number")

    # sound：普通通知用字符串，critical alert 用字典
    if "sound" in aps:
        s = aps["sound"]
        if isinstance(s, str):
            pass
        elif isinstance(s, dict):
            unknown = set(s) - SOUND_KEYS
            if unknown:
                problems.append("unknown sound keys: %s" % sorted(unknown))
            vol = s.get("volume")
            if vol is not None and not (0.0 <= float(vol) <= 1.0):
                problems.append("sound.volume must be between 0.0 and 1.0")
        else:
            problems.append("aps.sound must be a string or a dictionary")

    # content-available / mutable-content 只能是 1
    for flag in ("content-available", "mutable-content"):
        if flag in aps and aps[flag] != 1:
            problems.append("aps.%s must be 1" % flag)

    # interruption-level 取值
    lvl = aps.get("interruption-level")
    if lvl is not None and lvl not in INTERRUPTION_LEVELS:
        problems.append("aps.interruption-level must be one of %s" % sorted(INTERRUPTION_LEVELS))

    # relevance-score：普通通知 0..1；Live Activity 可为任意 Double
    score = aps.get("relevance-score")
    if score is not None and not is_live_activity:
        if not (0.0 <= float(score) <= 1.0):
            problems.append("aps.relevance-score must be between 0.0 and 1.0")

    # 自定义键的值必须是 primitive，且不得与 aps 同名
    for k, v in custom.items():
        if k == "aps":
            problems.append("custom key must not be named 'aps'")
        if not isinstance(v, PRIMITIVE_TYPES):
            problems.append("custom value %r must be a primitive type" % k)
    return problems


def dropped_custom_keys(payload: dict) -> list:
    """被 APNs 忽略的、误放进 aps 里的自定义键。"""
    return dropped_aps_keys(payload)


def payload_bytes(payload: dict) -> int:
    """APNs 按**字节**判大小，序列化必须是最小无空格形式之外的任意形式 —— 这里按紧凑形式计。"""
    return len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))



if __name__ == "__main__":
    from selfcheck_payload import _self_check

    print("OK: %d assertions passed" % _self_check())
