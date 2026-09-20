"""HTTP 缓存与条件请求 —— 自检。

断言策略：断言**误报集与漏报集**（哪些情况该命中、哪些不该），
以及**互斥口径的对照**（共享 vs 私有、保守 vs 非保守 age、显式 vs 启发式）。
"""

from main import (StoredResponse, build_validation_request, calculate_age,
                  evaluate_preconditions, freshen, freshness_lifetime,
                  heuristic_lifetime, http_date, may_serve_stale,
                  parse_cache_control, response_is_fresh, select_stored,
                  unmodified_since, vary_matches)

PASS = [0]


def ok(name: str, cond: bool, extra: str = "") -> None:
    if cond:
        PASS[0] += 1
        print(f"  PASS  {name}{(' -> ' + extra) if extra else ''}")
    else:
        raise AssertionError(f"FAIL  {name}{(' -> ' + extra) if extra else ''}")


def approx(a, b, tol=1e-9):
    return abs(a - b) <= tol


D0 = http_date("Mon, 01 Jan 2024 00:00:00 GMT")   # 1704067200

print("== 1. §4.2.1 freshness_lifetime 的四个分支与优先级 ==")
r = StoredResponse(headers={"cache-control": "s-maxage=60, max-age=600"})
ok("共享缓存取 s-maxage", freshness_lifetime(r, shared=True) == 60.0,
   str(freshness_lifetime(r, shared=True)))
ok("私有缓存取 max-age", freshness_lifetime(r, shared=False) == 600.0,
   str(freshness_lifetime(r, shared=False)))
r = StoredResponse(headers={"cache-control": "max-age=100",
                            "expires": "Mon, 01 Jan 2024 01:00:00 GMT",
                            "date": "Mon, 01 Jan 2024 00:00:00 GMT"})
ok("max-age 优先于 Expires", freshness_lifetime(r, shared=False) == 100.0)
r = StoredResponse(headers={"expires": "Mon, 01 Jan 2024 01:00:00 GMT",
                            "date": "Mon, 01 Jan 2024 00:00:00 GMT"})
ok("无 max-age 时取 Expires 减 Date = 3600s",
   approx(freshness_lifetime(r, shared=False), 3600.0),
   str(freshness_lifetime(r, shared=False)))
r = StoredResponse(headers={"date": "Mon, 01 Jan 2024 00:00:00 GMT"})
ok("三者皆无 → 返回 None（可走启发式）", freshness_lifetime(r, shared=False) is None)

print("== 2. §4.2.2 启发式：10%，且有显式过期时禁止使用 ==")
r = StoredResponse(headers={"date": "Mon, 01 Jan 2024 00:00:00 GMT",
                            "last-modified": "Sun, 31 Dec 2023 00:00:00 GMT"})
ok("无显式过期：10% × (Date − Last-Modified) = 8640s",
   approx(heuristic_lifetime(r, D0), 8640.0), str(heuristic_lifetime(r, D0)))
r2 = StoredResponse(headers={"cache-control": "max-age=10",
                             "date": "Mon, 01 Jan 2024 00:00:00 GMT",
                             "last-modified": "Sun, 31 Dec 2023 00:00:00 GMT"})
ok("有显式过期 → 启发式被禁止（返回 None）", heuristic_lifetime(r2, D0) is None)
ok("启发式与显式值不同源：10 ≠ 8640",
   freshness_lifetime(r2, shared=False) == 10.0 and heuristic_lifetime(r, D0) == 8640.0)

print("== 3. §4.2 response_is_fresh 是严格大于 ==")
r = StoredResponse(headers={"cache-control": "max-age=100",
                            "date": "Mon, 01 Jan 2024 00:00:00 GMT"})
ok("age=99 仍新鲜", response_is_fresh(r, False, D0 + 99, D0, D0))
ok("age=100 恰好等于 lifetime → 已 stale（严格大于）",
   not response_is_fresh(r, False, D0 + 100, D0, D0))
ok("age=101 已 stale", not response_is_fresh(r, False, D0 + 101, D0, D0))

print("== 4. §4.2.3 Age：三种口径的对照 ==")
# Date 在未来（origin 时钟超前）时 apparent_age 本应为负，被 max(0, ...) 夹成 0：
# 与「Date 恰好等于 response_time」的情形结果完全一致，这就是夹取的可观测后果。
future = StoredResponse(headers={"date": "Mon, 01 Jan 2024 00:00:50 GMT"})
aligned = StoredResponse(headers={"date": "Mon, 01 Jan 2024 00:00:10 GMT"})
a_future = calculate_age(future, D0 + 20, D0 + 5, D0 + 10, conservative=True)
a_aligned = calculate_age(aligned, D0 + 20, D0 + 5, D0 + 10, conservative=True)
ok("Date 超前 40s 不会让 age 变负：与 Date==response_time 结果相同",
   approx(a_future, a_aligned), f"{a_future} {a_aligned}")
ok("两种情形的 current_age = corrected(5) + resident(10) = 15", approx(a_future, 15.0),
   str(a_future))

# corrected_age_value = age_value + response_delay
r = StoredResponse(headers={"date": "Mon, 01 Jan 2024 00:00:00 GMT", "age": "10"})
cons = calculate_age(r, D0 + 100, D0, D0 + 5, conservative=True)
noncons = calculate_age(r, D0 + 100, D0, D0 + 5, conservative=False)
ok("corrected_age_value = age_value + response_delay = 15",
   approx(noncons - (100 - 5), 15.0), str(noncons))

# 保守版 = max(apparent, corrected)：Date 很久远时 apparent 远大于 corrected
old = StoredResponse(headers={"date": "Mon, 31 Dec 2023 23:43:20 GMT"})
c = calculate_age(old, D0 + 11, D0, D0 + 1, conservative=True)
n = calculate_age(old, D0 + 11, D0, D0 + 1, conservative=False)
ok("保守版由 apparent_age 主导（1001 + 10）", approx(c, 1011.0), str(c))
ok("非保守版只用 corrected_age_value（1 + 10）", approx(n, 11.0), str(n))
ok("保守版严格大于非保守版", c > n, f"{c} {n}")
r = StoredResponse(headers={"date": "Mon, 01 Jan 2024 00:00:00 GMT", "age": "0"})
ok("Age 头偏小时保守版由 apparent_age 主导",
   approx(calculate_age(r, D0 + 100, D0, D0 + 30, conservative=True), 100.0),
   str(calculate_age(r, D0 + 100, D0, D0 + 30, conservative=True)))

print("== 5. §4.2.4 什么情况下允许出 stale ==")
r = StoredResponse(headers={"cache-control": "max-age=1, must-revalidate"})
ok("must-revalidate 时即使有 max-stale 也不许出 stale",
   not may_serve_stale(r, False, {"max-stale": "100"}))
r = StoredResponse(headers={"cache-control": "max-age=1"})
ok("无 max-stale 且未断网 → 不许出 stale", not may_serve_stale(r, False, {}))
ok("有 max-stale → 允许", may_serve_stale(r, False, {"max-stale": "100"}))
ok("断网 → 允许", may_serve_stale(r, False, {}, disconnected=True))
r = StoredResponse(headers={"cache-control": "max-age=1, no-cache"})
ok("no-cache 显式禁止 stale", not may_serve_stale(r, False, {"max-stale": "100"},
                                                  disconnected=True))
r = StoredResponse(headers={"cache-control": "max-age=1, s-maxage=5"})
ok("共享缓存下 s-maxage 亦禁止 stale", not may_serve_stale(r, True, {"max-stale": "9"}))

print("== 6. §4.1 Vary 匹配的误报集与漏报集 ==")
ok("同名同值匹配", vary_matches({"accept-encoding": "gzip"},
                                {"accept-encoding": "gzip"}, "Accept-Encoding"))
ok("大小写归一化后匹配（Accept-Encoding 值不区分大小写）",
   vary_matches({"accept-encoding": "GZIP"}, {"accept-encoding": "gzip"},
                "Accept-Encoding"))
ok("空白差异归一化后匹配",
   vary_matches({"accept-encoding": "gzip,br"}, {"accept-encoding": "gzip,  br"},
                "Accept-Encoding"))
# 规范只许可「按该字段规范已知语义等价」的重排；偏好顺序本身是语义的一部分，
# 故本模型不做重排 —— 顺序不同即不匹配。
ok("顺序不同 → 不匹配（重排不在本模型许可的归一化内）",
   not vary_matches({"accept-encoding": "gzip, br"}, {"accept-encoding": "br, gzip"},
                    "Accept-Encoding"))
ok("值不同 → 不匹配", not vary_matches({"accept-encoding": "gzip"},
                                       {"accept-encoding": "br"}, "Accept-Encoding"))
ok("一方缺失 → 不匹配（缺席只能对缺席）",
   not vary_matches({}, {"accept-encoding": "gzip"}, "Accept-Encoding")
   and not vary_matches({"accept-encoding": "gzip"}, {}, "Accept-Encoding"))
ok("双方都缺失 → 匹配", vary_matches({}, {}, "Accept-Encoding"))
ok("Vary: * 恒不匹配", not vary_matches({"a": "1"}, {"a": "1"}, "*"))
ok("Vary 覆盖多个字段且只差一个 → 不匹配",
   not vary_matches({"a": "1", "b": "2"}, {"a": "1", "b": "3"}, "a, b"))

print("== 7. §4.1 多条存储响应的选择 ==")
old = StoredResponse(headers={"date": "Mon, 01 Jan 2024 00:00:00 GMT"},
                     request_headers={"accept-encoding": "gzip"})
new = StoredResponse(headers={"date": "Mon, 01 Jan 2024 01:00:00 GMT", "vary": "Accept-Encoding"},
                     request_headers={"accept-encoding": "gzip"})
picked = select_stored([old, new], {"accept-encoding": "gzip"}, D0)
ok("有 Vary 的那条优先（即便它不是最新的比较基准）", picked is new, str(picked.date_value(D0)))
bad = StoredResponse(headers={"date": "Mon, 01 Jan 2024 01:00:00 GMT",
                              "vary": "Accept-Encoding"},
                     request_headers={"accept-encoding": "br"})
ok("无一条 Vary 匹配 → 无法复用", select_stored([bad], {"accept-encoding": "gzip"}, D0) is None)
ok("未声明 Vary 的响应对任何请求都算匹配（这正是它会被误用的原因）",
   select_stored([old], {"accept-encoding": "br"}, D0) is old)

print("== 8. §4.3.1 条件请求的生成 ==")
r = StoredResponse(headers={"etag": '"v1"', "last-modified": "Mon, 01 Jan 2024 00:00:00 GMT"})
h = build_validation_request(r)
ok("有 ETag 必须发 If-None-Match", h.get("if-none-match") == '"v1"', str(h))
ok("单条且非子范围时同时发 If-Modified-Since", "if-modified-since" in h)
ok("子范围请求时不发 If-Modified-Since",
   "if-modified-since" not in build_validation_request(r, subrange=True))
ok("只有 ETag 时仍发 If-None-Match（MUST，无 Last-Modified 也不影响）",
   build_validation_request(StoredResponse(headers={"etag": '"x"'}))["if-none-match"] == '"x"')

print("== 9. RFC 9110 §13.2.2 前置条件优先级 ==")
r = StoredResponse(headers={"etag": '"v2"',
                            "last-modified": "Mon, 01 Jan 2024 00:00:00 GMT"})
ok("If-Match 不匹配 → 412（它先于 If-None-Match 评估）",
   evaluate_preconditions({"if-match": '"v1"', "if-none-match": '"v2"'}, r) == 412)
ok("缓存（非 origin）不适用 If-Match → 落到 If-None-Match → 304",
   evaluate_preconditions({"if-match": '"v1"', "if-none-match": '"v2"'}, r,
                          is_origin=False) == 304)
ok("If-None-Match 优先于 If-Modified-Since：ETag 命中就 304，不看日期",
   evaluate_preconditions({"if-none-match": '"v2"',
                           "if-modified-since": "Sun, 31 Dec 2023 00:00:00 GMT"}, r) == 304)
ok("只有 If-Modified-Since 且资源未修改 → 304",
   evaluate_preconditions({"if-modified-since": "Mon, 01 Jan 2024 00:00:00 GMT"}, r) == 304)
ok("只有 If-Modified-Since 且资源已修改 → 正常执行（None）",
   evaluate_preconditions({"if-modified-since": "Sun, 31 Dec 2023 00:00:00 GMT"}, r) is None)
ok("If-Unmodified-Since 不满足 → 412",
   evaluate_preconditions({"if-unmodified-since": "Sun, 31 Dec 2023 00:00:00 GMT"}, r) == 412)
ok("非 GET/HEAD 的 If-None-Match 命中 → 412 而不是 304",
   evaluate_preconditions({"if-none-match": '"v2"', "__method__": "PUT"}, r) == 412)
ok("If-Range 只在 GET + Range 同时出现时才评估",
   evaluate_preconditions({"if-range": '"v2"'}, r) is None
   and evaluate_preconditions({"if-range": '"v2"', "range": "bytes=0-9"}, r) == 206)

print("== 10. §4.3.4 304 的 freshening ==")
e1 = StoredResponse(headers={"etag": '"v1"', "date": "Mon, 01 Jan 2024 00:00:00 GMT"})
ok("强校验器匹配 → 更新该条", freshen([e1], StoredResponse(
    status=304, headers={"etag": '"v1"', "cache-control": "max-age=999"})) == [e1]
   and e1.headers["cache-control"] == "max-age=999")
e2 = StoredResponse(headers={"etag": '"other"'})
ok("强校验器无匹配 → MUST NOT 更新（返回空）",
   freshen([e2], StoredResponse(status=304, headers={"etag": '"v1"'})) == [])
w1 = StoredResponse(headers={"etag": 'W/"w1"', "date": "Mon, 01 Jan 2024 00:00:00 GMT"})
w2 = StoredResponse(headers={"etag": 'W/"w1"', "date": "Mon, 01 Jan 2024 01:00:00 GMT"})
hit = freshen([w1, w2], StoredResponse(status=304, headers={"etag": 'W/"w1"'}))
ok("弱校验器 → 只更新最近的那一条", hit == [w2], str([id(x) for x in hit]))
n1 = StoredResponse(headers={"date": "Mon, 01 Jan 2024 00:00:00 GMT"})
ok("无校验器且仅一条且该条也无校验器 → 更新",
   freshen([n1], StoredResponse(status=304, headers={"cache-control": "max-age=7"})) == [n1]
   and n1.headers["cache-control"] == "max-age=7")

print("== 11. Cache-Control 解析细节 ==")
cc = parse_cache_control("max-age=100, no-cache, MAX-AGE=5")
ok("指令名大小写不敏感", "max-age" in cc)
ok("重复指令取第一次出现（§4.2.1：要么用第一次，要么整个判 stale）",
   cc["max-age"] == "100", str(cc))
ok("无值指令解析为 None", cc["no-cache"] is None)
ok("非法整数（max-age=abc）被当成无效新鲜度信息",
   parse_cache_control("max-age=abc")["max-age"] == "abc")

print(f"\n全部 {PASS[0]} 项断言通过")
