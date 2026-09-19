# -*- coding: utf-8 -*-
"""RFC 9110 §13 / §8.8.3 自检。运行：python check.py"""

import sys

import conditional as C

FAIL = 0
COUNT = 0


def ck(label, cond):
    global FAIL, COUNT
    COUNT += 1
    if not cond:
        FAIL += 1
        print("FAIL: %s" % label)


def ignored(resp, name):
    """ignored 里的条目带括号后缀，按前缀匹配（list 的 in 是精确相等）。"""
    return any(x.startswith(name) for x in resp.ignored)


# --- §8.8.3.2 Table 3 逐行 -------------------------------------------------
ck("W/1 vs W/1 强比较不匹配", C.strong_compare('W/"1"', 'W/"1"') is False)
ck("W/1 vs W/2 强比较不匹配", C.strong_compare('W/"1"', 'W/"2"') is False)
ck("W/1 vs 1  强比较不匹配", C.strong_compare('W/"1"', '"1"') is False)
ck("1   vs 1  强比较匹配", C.strong_compare('"1"', '"1"') is True)
ck("W/1 vs W/1 弱比较匹配", C.weak_compare('W/"1"', 'W/"1"') is True)
ck("W/1 vs W/2 弱比较不匹配", C.weak_compare('W/"1"', 'W/"2"') is False)
ck("W/1 vs 1  弱比较匹配", C.weak_compare('W/"1"', '"1"') is True)
ck("1   vs 1  弱比较匹配", C.weak_compare('"1"', '"1"') is True)
ck("小写 w/ 前缀非法（W/ 大小写敏感）", C.parse_entity_tag('w/"1"') is None)
ck("未加引号不是合法 entity-tag", C.parse_entity_tag("1") is None)
ck("空 opaque-tag 合法（ETag: \"\"）", C.parse_entity_tag('""') == (False, ""))

# --- §13.1.1 If-Match MUST 用强比较 ----------------------------------------
r = C.Resource(exists=True, etag='W/"1"')
srv = C.OriginServer(r)
ck("资源是弱 ETag，If-Match: \"1\" → 412（强比较）",
   srv.handle("PUT", {"If-Match": '"1"'}).status == 412)
# 强比较要求"两者都不弱"，所以服务器给弱 ETag 时 If-Match **永远**不成立
ck("资源是弱 ETag，If-Match: W/\"1\" 也失败（强比较要求双方都不弱）",
   srv.handle("PUT", {"If-Match": 'W/"1"'}).status == 412)

r2 = C.Resource(exists=True, etag='"1"')
ck("If-Match 命中列表任一项即通过",
   C.OriginServer(r2).handle("PUT", {"If-Match": '"x", "y", "1"'}).status == 200)
ck("If-Match 全不命中 → 412",
   C.OriginServer(r2).handle("PUT", {"If-Match": '"x", "y"'}).status == 412)
ck("If-Match: * 且资源存在 → 通过",
   C.OriginServer(r2).handle("PUT", {"If-Match": "*"}).status == 200)
ck("If-Match: * 但资源不存在 → 412",
   C.OriginServer(C.Resource(exists=False)).handle("PUT", {"If-Match": "*"}).status == 412)

# MAY：变更看起来已被应用过 → 可回 2xx
ck("If-Match 失败但已应用过 → MAY 回 200",
   C.OriginServer(r2).handle("PUT", {"If-Match": '"old"'}, already_applied=True).status == 200)

# --- §13.1.2 If-None-Match MUST 用弱比较 ------------------------------------
r3 = C.Resource(exists=True, etag='W/"1"')
ck("INM: W/\"1\" 命中弱比较 → GET 返 304",
   C.OriginServer(r3).handle("GET", {"If-None-Match": 'W/"1"'}).status == 304)
ck("INM: \"1\" 对弱 ETag 也命中 → 304",
   C.OriginServer(r3).handle("GET", {"If-None-Match": '"1"'}).status == 304)
ck("INM 不命中 → 正常执行",
   C.OriginServer(r3).handle("GET", {"If-None-Match": '"9"'}).status == 200)
ck("INM 在非安全方法上失败 → 412（不是 304）",
   C.OriginServer(r3).handle("POST", {"If-None-Match": '"1"'}).status == 412)
ck("INM: * 用于 PUT 防重复创建（资源已存在 → 412）",
   C.OriginServer(r3).handle("PUT", {"If-None-Match": "*"}).status == 412)
ck("INM: * 资源不存在 → 条件为真，执行",
   C.OriginServer(C.Resource(exists=False)).handle("PUT", {"If-None-Match": "*"}).status == 200)

# --- §13.1.3 If-Modified-Since ---------------------------------------------
r4 = C.Resource(exists=True, etag='"1"', last_modified=1000)
ck("IMS 未变更 → 304",
   C.OriginServer(r4).handle("GET", {"If-Modified-Since": 1000}).status == 304)
# 判据是「last_modified <= IMS → 条件为假」：资源比给定时间新才算 modified
ck("IMS 早于实际修改时间 → 条件为真，正常执行",
   C.OriginServer(r4).handle("GET", {"If-Modified-Since": 900}).status == 200)
ck("IMS 晚于实际修改时间 → 条件为假，304",
   C.OriginServer(r4).handle("GET", {"If-Modified-Since": 1001}).status == 304)
resp = C.OriginServer(r4).handle("GET", {"If-None-Match": '"9"', "If-Modified-Since": 500})
ck("INM 存在时 IMS 被 MUST ignore", ignored(resp, "If-Modified-Since"))
ck("...且结果按 INM 走（不命中 → 200）", resp.status == 200)
ck("非 GET/HEAD 时 IMS 被忽略",
   ignored(C.OriginServer(r4).handle("POST", {"If-Modified-Since": 500}),
           "If-Modified-Since"))

# --- §13.1.4 If-Unmodified-Since -------------------------------------------
ck("IUS 未变更（<=）→ 通过",
   C.OriginServer(r4).handle("PUT", {"If-Unmodified-Since": 1000}).status == 200)
ck("IUS 已变更 → 412",
   C.OriginServer(r4).handle("PUT", {"If-Unmodified-Since": 999}).status == 412)
resp = C.OriginServer(r4).handle("PUT", {"If-Match": '"1"', "If-Unmodified-Since": 999})
ck("If-Match 存在时 IUS 被屏蔽（不再单独求值）", resp.status == 200)
ck("IUS 非法日期被忽略",
   C.OriginServer(r4).handle("PUT", {"If-Unmodified-Since": "not-a-date"}).status == 200)

# --- §13.2.2 优先级 ---------------------------------------------------------
r5 = C.Resource(exists=True, etag='"1"', last_modified=1000)
resp = C.OriginServer(r5).handle("GET", {"If-Match": '"old"', "If-None-Match": '"1"'})
ck("If-Match 优先于 If-None-Match → 412 而非 304", resp.status == 412)
resp = C.OriginServer(r5).handle("PUT", {"If-Match": '"old"', "If-None-Match": '"1"'})
ck("...非安全方法同样先判 If-Match → 412", resp.status == 412)

# --- §13.1.5 If-Range：精确匹配，与 IUS 的 <= 不同 ---------------------------
r6 = C.Resource(exists=True, etag='"1"', last_modified=1000)
ck("If-Range ETag 命中 + Range → 206",
   C.OriginServer(r6).handle("GET", {"If-Range": '"1"'}, has_range=True).status == 206)
ck("If-Range ETag 不命中 → 忽略 Range 回 200",
   C.OriginServer(r6).handle("GET", {"If-Range": '"9"'}, has_range=True).status == 200)
ck("If-Range 没有 Range 时被 MUST ignore",
   ignored(C.OriginServer(r6).handle("GET", {"If-Range": '"1"'}), "If-Range"))
ck("If-Range 用强比较：资源弱 ETag 时不命中",
   C.OriginServer(C.Resource(etag='W/"1"', last_modified=1000)).handle(
       "GET", {"If-Range": '"1"'}, has_range=True).status == 200)
ck("If-Range 日期精确相等 → true",
   C.OriginServer(r6).handle("GET", {"If-Range": 1000}, has_range=True).status == 206)
ck("If-Range 日期更早也算 false（不是 <=）",
   C.OriginServer(r6).handle("GET", {"If-Range": 1001}, has_range=True).status == 200)

# --- §13.2.1 何时求值 -------------------------------------------------------
ck("基线 404 → 条件全部忽略，返回 404",
   C.OriginServer(C.Resource(exists=False), base_status=404).handle(
       "GET", {"If-None-Match": '"1"'}).status == 404)
ck("基线 301 → 重定向优先于条件求值",
   C.OriginServer(r5, base_status=301).handle("GET", {"If-Match": '"x"'}).status == 301)
ck("基线 401 → 认证失败优先",
   C.OriginServer(r5, base_status=401).handle("GET", {"If-Match": '"x"'}).status == 401)
for m in ("CONNECT", "OPTIONS", "TRACE"):
    ck("%s 与方法选择表示无关 → 忽略条件" % m,
       C.OriginServer(r5).handle(m, {"If-Match": '"x"'}).status == 200)

# --- lost update 场景 -------------------------------------------------------
naive, guarded = C.lost_update_demo()
ck("不带 If-Match 的并发 PUT 会覆盖（lost update）", naive == 200)
ck("带旧 ETag 的 If-Match 挡住覆盖", guarded == 412)

print("assertions=%d fail=%d" % (COUNT, FAIL))
sys.exit(1 if FAIL else 0)
