"""HTTP 缓存语义自检（RFC 9111 / RFC 5861）。

运行： python http_cache_selftest.py
"""
import sys
from http_cache_core import (
    parse_cache_control, split_commas, delta_seconds,
    freshness_lifetime, current_age, response_is_fresh,
    is_storable, may_reuse_without_validation,
    error_status_for_stale_must_revalidate,
    stale_window, may_serve_stale_if_error, STALE_IF_ERROR_STATUSES,
    vary_match, secondary_cache_key,
)

FAILS = []


def check(label, cond, detail=""):
    if cond:
        print("  ok   %s" % label)
    else:
        FAILS.append("%s %s" % (label, detail))
        print("  FAIL %s %s" % (label, detail))


def near(a, b, eps=1e-9):
    return abs(a - b) < eps


print("[1] Cache-Control 解析：引号感知的逗号切分")
d = parse_cache_control('max-age=600, stale-while-revalidate=30')
check("max-age 解析", d.get("max-age") == "600", d)
check("swr 解析", d.get("stale-while-revalidate") == "30", d)
d2 = parse_cache_control('no-cache="Set-Cookie,ETag"')
check("引号内逗号不被切开", d2.get("no-cache") == "Set-Cookie,ETag", d2)
check("朴素切分会切开(反证)", len(split_commas('a="x,y"')) == 1)
d3 = parse_cache_control('Max-Age=60')
check("指令名大小写不敏感", d3.get("max-age") == "60", d3)
check("delta-seconds 非法值 -> None", delta_seconds("abc") is None)
check("delta-seconds 上限 2^31", delta_seconds("99999999999") == 2147483648)

print("[2] freshness_lifetime 优先级（RFC 9111 §4.2.1）")
h = {"cache-control": "s-maxage=600, max-age=60", "date": 1000}
fl, src = freshness_lifetime(h, shared=True)
check("共享缓存取 s-maxage", (fl, src) == (600, "s-maxage"), (fl, src))
fl, src = freshness_lifetime(h, shared=False)
check("私有缓存忽略 s-maxage 取 max-age", (fl, src) == (60, "max-age"), (fl, src))
fl, src = freshness_lifetime({"expires": 1300, "date": 1000}, shared=True)
check("Expires - Date", (fl, src) == (300, "expires-date"), (fl, src))
fl, src = freshness_lifetime({"date": 1000}, shared=True, last_modified=1000, now=101000)
check("启发式 = 10% 间隔", (fl, src) == (10000, "heuristic"), (fl, src))
fl, src = freshness_lifetime({"cache-control": "max-age=abc"}, shared=True)
check("非法 max-age 不产生寿命", src == "none", (fl, src))
# 有显式过期时间时**禁止**用启发式（§4.2.2 MUST NOT）
fl, src = freshness_lifetime({"cache-control": "max-age=10"}, shared=True,
                             last_modified=0, now=10 ** 6)
check("有显式过期则不用启发式", src == "max-age", (fl, src))

print("[3] response_is_fresh 是严格大于（§4.2）")
check("age 99 < 100 -> fresh", response_is_fresh(100, 99) is True)
check("age 100 == 100 -> stale", response_is_fresh(100, 100) is False)
check("age 101 > 100 -> stale", response_is_fresh(100, 101) is False)

print("[4] current_age（§4.2.3）")
# 时钟落后于源站：apparent_age 被 clamp 到 0
age = current_age(age_value=0, date_value=1000, request_time=900,
                  response_time=905, now=2000)
check("时钟落后 -> apparent 归零，走 corrected", age == 1100, age)
# Age 头与本地时钟分歧：保守合成取 max
age_c = current_age(age_value=100, date_value=0, request_time=1000,
                    response_time=1002, now=1010, conservative=True)
age_n = current_age(age_value=100, date_value=0, request_time=1000,
                    response_time=1002, now=1010, conservative=False)
check("保守合成取 max(apparent,corrected)", age_c == 1010, age_c)
check("非保守仅用 corrected", age_n == 110, age_n)
check("二者差异来源于 Date 与本地时钟偏移", age_c - age_n == 900, age_c - age_n)
check("负 apparent_age 被截断",
      current_age(0, 2000, 1900, 1900, 1900) == 0)

print("[5] 可存储性（§3）")
check("no-store 不可存", is_storable(200, {"cache-control": "no-store"}, True)[0] is False)
check("200 无指令可启发式缓存", is_storable(200, {}, True)[0] is True)
check("418 不可启发式缓存", is_storable(418, {}, True)[0] is False)
check("private 对共享缓存不可存", is_storable(200, {"cache-control": "private"}, True)[0] is False)
check("private 对私有缓存可存", is_storable(200, {"cache-control": "private"}, False)[0] is True)

print("[6] 陈旧复用限制（§4.2.4 / §5.2.2.x）")
ok, why = may_reuse_without_validation({"cache-control": "no-cache"}, True)
check("no-cache 无参必须校验", ok is False and why == "no-cache(unqualified)", why)
ok, why = may_reuse_without_validation({"cache-control": 'no-cache="Set-Cookie"'}, True)
check("no-cache 带字段名的限定形式可复用(排除该字段)", ok is True, why)
ok, why = may_reuse_without_validation({"cache-control": "must-revalidate"}, True,
                                       disconnected=True)
check("must-revalidate + 断网 -> 504", ok is False and why == "must-revalidate->504", why)
check("断网错误码 SHOULD 504", error_status_for_stale_must_revalidate() == 504)
ok, why = may_reuse_without_validation({"cache-control": "s-maxage=600"}, True)
check("共享缓存命中 s-maxage 不可复用陈旧", ok is False and why == "s-maxage", why)
ok, why = may_reuse_without_validation({"cache-control": "proxy-revalidate"}, True)
check("proxy-revalidate 对共享缓存生效", ok is False and why == "proxy-revalidate", why)
ok, why = may_reuse_without_validation({"cache-control": "proxy-revalidate"}, False)
check("proxy-revalidate 对私有缓存无效", ok is True, why)

print("[7] RFC 5861 stale 窗口")
check("599 -> fresh", stale_window(600, 599, 30) == "fresh")
check("600 == max-age -> 进入 swr", stale_window(600, 600, 30) == "swr")
check("629 -> swr", stale_window(600, 629, 30) == "swr")
check("630 == max-age+swr -> 真陈旧", stale_window(600, 630, 30) == "stale")
check("无 swr 时过期即陈旧", stale_window(600, 600, None) == "stale")
check("stale-if-error 窗口内", may_serve_stale_if_error(650, 600, 60) is True)
check("stale-if-error 窗口外", may_serve_stale_if_error(670, 600, 60) is False)
check("stale-if-error 认定的错误码", set(STALE_IF_ERROR_STATUSES) == {500, 502, 503, 504})

print("[8] Vary 二级键（§4.1）")
check("Vary:* 永不匹配", vary_match("*", {"accept": "a"}, {"accept": "a"}) is False)
hdr = {"accept-encoding": "gzip"}
check("Vary 头相同 -> 匹配", vary_match("Accept-Encoding", hdr, hdr) is True)
check("Vary 头不同 -> 不匹配",
      vary_match("Accept-Encoding", hdr, {"accept-encoding": "br"}) is False)
check("Vary 头缺失只能匹配缺失", vary_match("Accept-Encoding", hdr, {}) is False)
check("无 Vary 恒匹配", vary_match(None, {}, {"a": "1"}) is True)
k1 = secondary_cache_key("/p", "Accept-Encoding, User-Agent",
                         {"accept-encoding": "gzip", "user-agent": "curl"})
k2 = secondary_cache_key("/p", "User-Agent, Accept-Encoding",
                         {"user-agent": "curl", "accept-encoding": "gzip"})
check("二级键对字段顺序稳定", k1 == k2, (k1, k2))

print()
if FAILS:
    print("FAILED %d:" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL PASS")
