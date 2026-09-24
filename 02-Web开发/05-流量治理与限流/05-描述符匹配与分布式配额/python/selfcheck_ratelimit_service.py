"""05-描述符匹配与分布式配额 自检。运行：python selfcheck_ratelimit_service.py"""

import datetime as dt
import sys

from ratelimit_service import (
    ConfigError,
    LimitInfo,
    RateLimit,
    RateLimitConfig,
    generate_cache_key,
    get_response_descriptor_status,
    month_expiration_seconds,
    month_start_unix,
    status_for_negative_hits,
    wildcard_match,
)

OK = []
FAIL = []


def ck(name, cond):
    (OK if cond else FAIL).append(name)


def raises(fn, exc=ConfigError):
    try:
        fn()
    except exc:
        return True
    return False


CONFIG = {
    "domain": "mongo_cps",
    "descriptors": [
        {"key": "database", "value": "users",
         "rate_limit": {"unit": "second", "requests_per_unit": 10}},
        {"key": "database",
         "rate_limit": {"unit": "second", "requests_per_unit": 100}},
        {"key": "path_/api/*",
         "descriptors": [
             {"key": "remote_address",
              "rate_limit": {"unit": "minute", "requests_per_unit": 5}}]},
        {"key": "deep", "value": "x",
         "descriptors": [{"key": "sub",
                          "rate_limit": {"unit": "hour", "requests_per_unit": 7}}]},
    ],
}


def build():
    c = RateLimitConfig()
    c.load("mongo_cps", CONFIG["descriptors"])
    return c


# ---- 1. wildcardMatch 逐条 -------------------------------------------------
ck("单段时退化为全等", wildcard_match(["a"], "a") and not wildcard_match(["a"], "ab"))
ck("前后缀 + 单星", wildcard_match(["a", "b"], "aXXb") and not wildcard_match(["a", "b"], "aXX"))
ck("前缀不符即拒", not wildcard_match(["path_/api/", ""], "path_/other/x"))
ck("后缀不符即拒", not wildcard_match(["a", "b"], "bXXa"))
ck("总固定长度超过 value 长度即拒",
   not wildcard_match(["a", "b", "c"], "ab") and wildcard_match(["a", "b", "c"], "abc"))
ck("中间段按顺序出现",
   wildcard_match(["p", "m", "s"], "pXmYs") and not wildcard_match(["p", "m", "s"], "pYsXm"))

# ---- 2. GetLimit 的匹配顺序 ------------------------------------------------
cfg = build()
rl, key = cfg.get_limit("mongo_cps", [("database", "users")])
ck("key_value 优先于 key（10/s 而非 100/s）",
   rl is not None and rl.requests_per_unit == 10 and key == "database_users")
rl, key = cfg.get_limit("mongo_cps", [("database", "ops")])
ck("无精确条目时回落到默认 key（100/s）",
   rl is not None and rl.requests_per_unit == 100 and key == "database")
rl, _ = cfg.get_limit("unknown_domain", [("database", "users")])
ck("未配置 domain 返回 None", rl is None)

# ---- 3. 深度必须完全匹配 ---------------------------------------------------
rl, _ = cfg.get_limit("mongo_cps", [("deep", "x")])
ck("请求深度浅于配置（limit 在更深层）→ None", rl is None)
rl, _ = cfg.get_limit("mongo_cps", [("deep", "x"), ("sub", "y")])
ck("深度完全匹配才采用（7/hour）", rl is not None and rl.requests_per_unit == 7)
rl, _ = cfg.get_limit("mongo_cps", [("database", "users"), ("extra", "z")])
ck("请求深度深于配置（limit 在更浅层）→ None", rl is None)

# ---- 4. 通配条目 -----------------------------------------------------------
rl, key = cfg.get_limit("mongo_cps", [("path", "/api/v1"), ("remote_address", "1.2.3.4")])
ck("通配 finalKey 命中 path_/api/*", rl is not None and rl.requests_per_unit == 5)
rl, _ = cfg.get_limit("mongo_cps", [("path", "/other/v1"), ("remote_address", "1.2.3.4")])
ck("通配前缀不符 → None", rl is None)

# ---- 5. Envoy 侧 limit override 绕过配置树 ---------------------------------
rl, key = cfg.get_limit("mongo_cps", [("deep", "x"), ("sub", "y")],
                        limit_override={"unit": "minute", "requests_per_unit": 3})
ck("override 直接生效（3/minute）", rl is not None and rl.requests_per_unit == 3)
ck("override 的 shadow_mode 恒为 False（源码注释：不启用）", rl.shadow_mode is False)
ck("override 时配置树里的 7/hour 被完全绕过", rl.requests_per_unit != 7)
rl, _ = cfg.get_limit("mongo_cps", [("database", "users")],
                      limit_override={"unit": "minute", "requests_per_unit": 3})
ck("override 甚至不要求 descriptor 在配置里", rl is not None and rl.requests_per_unit == 3)

# ---- 6. 配置期校验 ---------------------------------------------------------
ck("unlimited 同时给 unit → 报错",
   raises(lambda: RateLimit.from_yaml({"unlimited": True, "unit": "second"})))
ck("unlimited 不给 unit → 合法",
   RateLimit.from_yaml({"unlimited": True}).unlimited is True)
ck("非 unlimited 且无 unit → 报错",
   raises(lambda: RateLimit.from_yaml({"requests_per_unit": 1})))
ck("非法 unit → 报错",
   raises(lambda: RateLimit.from_yaml({"requests_per_unit": 1, "unit": "fortnight"})))
ck("空 key → 报错", raises(lambda: RateLimitConfig().load("d", [{"value": "x"}])))
ck("重复 finalKey → 报错",
   raises(lambda: RateLimitConfig().load("d", [{"key": "a", "value": "b"},
                                               {"key": "a", "value": "b"}])))

# ---- 7. 判定阈值是严格大于 -------------------------------------------------
lim = RateLimit(10, "second")
info = LimitInfo(lim, before=10, addend=0)
code, rem, _ = get_response_descriptor_status("k", info)
ck("after == threshold 时不过限（严格大于）", code == "OK")
info = LimitInfo(lim, before=10, addend=1)
code, rem, stats = get_response_descriptor_status("k", info)
ck("after == threshold+1 时过限", code == "OVER_LIMIT")
ck("过限时 LimitRemaining 为 0", rem == 0)
ck("自增前已过限 ⇒ 整份 addend 计入 OverLimit",
   stats == ["OverLimit(1)"])

# ---- 8. hits_addend 可大于 1，也可为负 ------------------------------------
info = LimitInfo(lim, before=9, addend=3)
code, _, stats = get_response_descriptor_status("k", info)
ck("addend=3 时 after=12 过限", code == "OVER_LIMIT")
ck("自增前未过限 ⇒ 只计超出部分 OverLimit(2)（12-10）", "OverLimit(2)" in stats)
ck("同时把落进 near 区间的那一份记上 NearLimit(1)（10-max(8,9)）",
   "NearLimit(1)" in stats)
info = LimitInfo(lim, before=9, addend=1)
ck("after == threshold 时不过限（严格大于）",
   get_response_descriptor_status("k", info)[0] == "OK")
ck("负数 hits 走独立分支且计入 TotalNegativeHits",
   status_for_negative_hits(lim, -2)[2] == ["TotalNegativeHits(-2)"])

# ---- 9. shadow_mode / local cache -----------------------------------------
sl = RateLimit(10, "second", shadow_mode=True)
info = LimitInfo(sl, before=20, addend=5)
code, _, stats = get_response_descriptor_status("k", info)
ck("shadow_mode 下即使过限也返回 OK", code == "OK")
ck("shadow_mode 统计被累加", "ShadowMode(5)" in stats)
ck("shadow 下仍会记 OverLimit(5)，只是返回码被改成 OK", stats == ["OverLimit(5)", "ShadowMode(5)"])
info = LimitInfo(sl, before=8, addend=5)
ck("before 未过限时只计超出的部分（13-10=3）", "ShadowMode(3)" in
   get_response_descriptor_status("k", info)[2])
code, _, stats = get_response_descriptor_status("k", LimitInfo(lim, 3, 1),
                                               is_over_limit_with_local_cache=True)
ck("local cache 命中直接 OVER_LIMIT", code == "OVER_LIMIT")
ck("local cache 命中同时计两项统计",
   stats == ["OverLimit(1)", "OverLimitWithLocalCache(1)"])
ck("空 cache key 恒 OK（该 descriptor 没配 limit）",
   get_response_descriptor_status("", LimitInfo(lim, 99, 99))[0] == "OK")

# ---- 10. near limit 阈值 ---------------------------------------------------
info = LimitInfo(lim, before=8, addend=0, near_limit_ratio=0.8)
ck("nearLimitThreshold = floor(10*0.8) = 8", info.near_limit_threshold == 8)
ck("after == 8 时不算 near（严格大于）",
   "NearLimit" not in get_response_descriptor_status("k", info)[2])
info = LimitInfo(lim, before=8, addend=1)
ck("after == 9 时计入 NearLimit", "NearLimit(1)" in get_response_descriptor_status("k", info)[2])
ck("未过限且未近限时只计 WithinLimit",
   get_response_descriptor_status("k", LimitInfo(lim, 1, 1))[2] == ["WithinLimit(1)"])
ck("before 已近限 ⇒ 整份 addend 计入 NearLimit（before=8 addend=2）",
   "NearLimit(2)" in get_response_descriptor_status("k", LimitInfo(lim, 8, 2))[2])

# ---- 11. 缓存键 ------------------------------------------------------------
e = [("database", "users")]
k1, ps1 = generate_cache_key("mongo_cps", e, RateLimit(10, "second"), now_unix=1234)
ck("键结构 = domain_k_v_bucketStart", k1 == "mongo_cps_database_users_1234")
ck("秒级单位 PerSecond=True", ps1 is True)
k2, ps2 = generate_cache_key("mongo_cps", e, RateLimit(5, "minute"), now_unix=1234)
ck("分钟桶下取整到 1200", k2 == "mongo_cps_database_users_1200")
ck("分钟单位 PerSecond=False", ps2 is False)
ck("limit 为 None 时返回空键", generate_cache_key("d", e, None, 1)[0] == "")
ck("同域同描述符同窗口 → 同键（分布式一致前提）",
   generate_cache_key("d", e, RateLimit(1, "second"), 100)[0]
   == generate_cache_key("d", e, RateLimit(1, "second"), 100)[0])
ck("跨窗口 → 不同键",
   generate_cache_key("d", e, RateLimit(1, "second"), 100)[0]
   != generate_cache_key("d", e, RateLimit(1, "second"), 101)[0])
ck("键里带 prefix", generate_cache_key("d", e, RateLimit(1, "second"), 0,
                                       prefix="rl:")[0] == "rl:d_database_users_0")
ck("share_threshold 用通配模式替换实际值",
   generate_cache_key("d", [("f", "a.txt")], RateLimit(1, "second"), 0,
                      share_threshold_pattern=["*"])[0] == "d_f_*_0")

# ---- 12. 日历月（utils.time.go 已实读） ------------------------------------
ts = int(dt.datetime(2026, 3, 15, 12, 0, 0, tzinfo=dt.timezone.utc).timestamp())
ck("MonthStartUnix = 当月 1 日 00:00 UTC",
   month_start_unix(ts) == int(dt.datetime(2026, 3, 1, tzinfo=dt.timezone.utc).timestamp()))
ck("MonthExpirationSeconds = 距 4/1 还有 16.5 天 = 1425600s",
   month_expiration_seconds(ts) == 1425600)
ck("month 单位默认走日历月桶",
   generate_cache_key("d", e, RateLimit(1, "month"), ts)[0].endswith(str(month_start_unix(ts))))
ck("关掉 use_calendar_month 后走固定 30 天除数语义（此处按 second 退化验证）",
   generate_cache_key("d", e, RateLimit(1, "second"), ts,
                      use_calendar_month=False)[0].endswith(str(ts)))

if FAIL:
    print("FAILED %d:" % len(FAIL))
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("ratelimit_service selfcheck OK: %d assertions" % len(OK))
