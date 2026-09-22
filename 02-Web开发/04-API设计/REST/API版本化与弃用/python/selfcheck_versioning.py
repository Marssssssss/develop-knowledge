"""607 自检：Deprecation / Sunset 与 Accept 主动协商。

按「误报集 + 漏报集」成对构造；遇到官方文本与逐字规则冲突时（RFC 9110 Table 5），
把两侧都写成显式断言并在 README 标注，而不是悄悄折中。
"""

import sys

from harness import check, expect_errors

from sunset import (
    MAX_SF_DATE,
    MIN_SF_DATE,
    Endpoint,
    format_sf_date,
    imf_fixdate,
    parse_http_date,
    parse_sf_date,
)
from negotiate import (
    cache_key_matches,
    media_range_matches,
    negotiate,
    parse_accept,
    parse_vary,
    quality,
    resolve_version,
    specificity,
    version_from_header,
    version_from_media_type,
    version_from_path,
)

# draft-ietf-httpapi-deprecation-header-07 §4 的官方示例
DEPRECATED_AT = 1688169599            # Fri, 30 Jun 2023 23:59:59 GMT
SUNSET_AT = 1719791999                # Sun, 30 Jun 2024 23:59:59 GMT

# ------------------------------------------------------------- sf-date

check(parse_sf_date("@1688169599") == DEPRECATED_AT, "draft §2.1 官方示例 @1688169599")
check(parse_sf_date("  @1688169599  ") == DEPRECATED_AT, "sf-date 允许前后 OWS")
check(parse_sf_date("@-1") == -1, "sf-date 可为负（1970 年前）")
check(parse_sf_date("1688169599") is None, "缺 @ 不是合法 sf-date")
check(parse_sf_date("@") is None, "@ 后必须有整数")
check(parse_sf_date("@16.5") is None, "sf-date 不接受小数")
check(parse_sf_date("@abc") is None, "sf-date 不接受非数字")
check(parse_sf_date("@%d" % MAX_SF_DATE) == MAX_SF_DATE, "上界 9999 年（RFC 9651 §3.3.7）")
check(parse_sf_date("@%d" % (MAX_SF_DATE + 1)) is None, "超出上界即非法")
check(parse_sf_date("@%d" % MIN_SF_DATE) == MIN_SF_DATE, "下界 1 年")
check(parse_sf_date("@%d" % (MIN_SF_DATE - 1)) is None, "低于下界即非法")
check(format_sf_date(DEPRECATED_AT) == "@1688169599", "sf-date 回写")

# ----------------------------------------------------------- HTTP-date

check(parse_http_date("Sun, 30 Jun 2024 23:59:59 GMT") == SUNSET_AT,
      "RFC 8594 §4 官方示例的 IMF-fixdate")
check(parse_http_date("Sun, 06 Nov 1994 08:49:37 GMT") == 784111777,
      "RFC 9110 §5.6.7 的 IMF-fixdate 例子")
check(parse_http_date("Sunday, 06-Nov-94 08:49:37 GMT") == 784111777,
      "接收方 MUST 接受 rfc850 格式")
check(parse_http_date("Sun Nov  6 08:49:37 1994") == 784111777,
      "接收方 MUST 接受 asctime 格式（日期两位带空格填充）")
check(parse_http_date("Sun Nov 16 08:49:37 1994") == 784975777,
      "asctime 两位日期的另一种填充")
check(parse_http_date("2024-06-30T23:59:59Z") is None, "ISO 8601 不是 HTTP-date")
check(parse_http_date("Sun, 30 Jun 2024 23:59:59 UTC") is None, "非 GMT 时区非法")
check(parse_http_date("") is None, "空串非法")
check(imf_fixdate(SUNSET_AT) == "Sun, 30 Jun 2024 23:59:59 GMT",
      "发送方 MUST 生成 IMF-fixdate（实得 %r）" % imf_fixdate(SUNSET_AT))
check(parse_http_date(imf_fixdate(DEPRECATED_AT)) == DEPRECATED_AT, "IMF-fixdate 往返")

# ------------------------------------------------------------ 生命周期

EP = Endpoint("GET /v1/orders", DEPRECATED_AT, SUNSET_AT,
              deprecation_link="https://developer.example.com/deprecation",
              sunset_link="https://developer.example.com/sunset")
check(EP.errors() == [], "Sunset 不早于 Deprecation 时合法")
check(EP.headers() == {"Deprecation": "@1688169599",
                       "Sunset": "Sun, 30 Jun 2024 23:59:59 GMT"}, "两个头各自的序列化形态")
check(EP.phase(1670000000) == "active", "弃用前是 active")
check(EP.phase(DEPRECATED_AT) == "deprecated", "到达弃用时刻即 deprecated（闭区间）")
check(EP.phase(1700000000) == "deprecated", "弃用后到下线前仍是 deprecated")
check(EP.phase(SUNSET_AT) == "sunset", "到达下线时刻即 sunset（闭区间）")
check(EP.usable(1700000000), "§5 弃用不改变资源行为，仍可用")
check(not EP.usable(SUNSET_AT), "过了 sunset 才预期不可用")
check(EP.days_until_sunset(DEPRECATED_AT) == 366,
      "2023-06-30 到 2024-06-30 是 366 天（2024 是闰年）")
check(Endpoint("x").days_until_sunset(0) is None, "没有 Sunset 时返回 None")
check(Endpoint("x", DEPRECATED_AT).phase(SUNSET_AT) == "deprecated",
      "只有 Deprecation 时永远不到 sunset")

expect_errors(Endpoint("bad", DEPRECATED_AT, DEPRECATED_AT - 1).errors(),
              ["Sunset 早于 Deprecation"], label="draft §4 的 MUST NOT")
check(Endpoint("ok", DEPRECATED_AT, DEPRECATED_AT).errors() == [],
      "Sunset 恰好等于 Deprecation 是允许的（不是 earlier than）")

check(EP.hint_vs_status(1670000000, 200) == "before", "未到点状态码无约束")
check(EP.hint_vs_status(SUNSET_AT, 404) == "honored", "过点且非 2xx 视为兑现")
check(EP.hint_vs_status(SUNSET_AT, 200) == "hint-not-kept",
      "RFC 8594 §3：Sunset 只是 hint，过点仍 2xx 不算违规")

BUILT = Endpoint.from_headers("h", {"Deprecation": "@1688169599",
                                    "Sunset": "Sun, 30 Jun 2024 23:59:59 GMT"},
                              {"deprecation": "https://d", "sunset": "https://s"})
check(BUILT.deprecation == DEPRECATED_AT and BUILT.sunset == SUNSET_AT,
      "同一份时间点在两个头里走两套日期格式")
check(BUILT.deprecation_link == "https://d" and BUILT.sunset_link == "https://s",
      "两个链接关系类型各归各位")
check(Endpoint.from_headers("h", {}).deprecation is None, "没有弃用头时 deprecation 为 None")

# --------------------------------------------------------- Accept 协商

check(len(parse_accept("audio/*; q=0.2, audio/basic")) == 2, "两 media-range")
check(quality(parse_accept("audio/*; q=0.2, audio/basic"), "audio/basic") == 1.0,
      "§12.5.1 第一例：audio/basic 无 q 即 1")
check(quality(parse_accept("audio/*; q=0.2, audio/basic"), "audio/other") == 0.2,
      "audio/other 只匹配通配符范围")

ORDERED = parse_accept("text/plain; q=0.5, text/html, text/x-dvi; q=0.8, text/x-c")
check(quality(ORDERED, "text/html") == 1.0, "§12.5.1 第二例：text/html")
check(quality(ORDERED, "text/x-c") == 1.0, "§12.5.1 第二例：text/x-c 与 html 同权重")
check(quality(ORDERED, "text/x-dvi") == 0.8, "§12.5.1 第二例：text/x-dvi")
check(quality(ORDERED, "text/plain") == 0.5, "§12.5.1 第二例：text/plain")
check(quality(ORDERED, "image/png") == 0.0, "无通配符时未列出的类型 q=0")

PREC = parse_accept("text/*, text/plain, text/plain;format=flowed, */*")
check([specificity(i) for i in PREC] == [1, 2, 3, 0],
      "§12.5.1 优先级：带参完整型 > 完整型 > type/* > */*")

TABLE5 = ("text/*;q=0.3, text/plain;q=0.7, text/plain;format=flowed, "
          "text/plain;format=fixed;q=0.4, */*;q=0.5")
T5 = parse_accept(TABLE5)
check(quality(T5, "text/plain;format=flowed") == 1.0, "Table 5 第 1 行")
check(quality(T5, "text/plain") == 0.7, "Table 5 第 2 行")
check(quality(T5, "text/html") == 0.3, "Table 5 第 3 行")
check(quality(T5, "image/jpeg") == 0.5, "Table 5 第 4 行")
check(quality(T5, "text/plain;format=fixed") == 0.4, "Table 5 第 5 行")
check(quality(T5, "text/html;level=3") == 0.3,
      "Table 5 第 6 行：按规则只能推出 text/*;q=0.3（官方印成 0.7，Errata 7306）")
check(0.7 != quality(T5, "text/html;level=3"),
      "显式记录与官方文本的分歧，不让两侧悄悄折中")

check(quality(parse_accept("text/plain;format=flowed;q=0.5"), "text/plain") == 0.0,
      "带参范围不匹配无参媒体类型（参数是匹配条件而非可选提示）")
check(quality(parse_accept("text/plain"), "text/plain;format=flowed") == 1.0,
      "无参范围匹配带额外参数的媒体类型")
check(quality(parse_accept("text/*;q=0.3;charset=utf-8"), "text/plain;charset=utf-8") == 0.3,
      "§12.5.1 注：q 不在最后时接收方也应识别")
check(quality(parse_accept("TEXT/HTML;Q=0.4"), "text/html") == 0.4,
      "类型与参数名大小写不敏感")

AVAIL = [("json", "application/json"), ("v2", "application/vnd.example.v2+json"),
         ("html", "text/html")]
check(negotiate("application/vnd.example.v2+json", AVAIL) == "v2", "精确匹配胜出")
check(negotiate("text/html;q=0.1, */*;q=0.05", AVAIL) == "html", "高 q 胜出")
check(negotiate("application/xml", AVAIL) is None, "全 0 且无默认 → 406")
check(negotiate("application/xml", AVAIL, default="json") == "json",
      "服务端 MAY 无视 Accept 直接给默认表示（§12.4.1）")
check(negotiate("", AVAIL) == "json", "Accept 缺席 = 无偏好，取首个可用")
check(negotiate("*/*;q=0", AVAIL) is None, "显式 q=0 的 */* 表示全不可接受")

# ---------------------------------------------------------------- Vary

check(parse_vary("accept-encoding, accept-language")
      == (False, {"accept-encoding", "accept-language"}), "Vary 字段名列表小写归一")
check(parse_vary("*") == (True, {"*"}), "Vary: * 是通配符")
check(parse_vary("") == (False, set()), "空 Vary")
STORED = {"accept-encoding": "gzip"}
check(cache_key_matches("accept-encoding", STORED, {"accept-encoding": "gzip"}),
      "同值可复用")
check(not cache_key_matches("accept-encoding", STORED, {"accept-encoding": "br"}),
      "异值不可复用")
check(cache_key_matches("accept-encoding", {}, {}), "两边都缺视为相同")
check(not cache_key_matches("*", STORED, STORED),
      "Vary: * 未转发请求不得复用")
check(cache_key_matches("", STORED, {"accept-encoding": "br"}), "无 Vary 时不约束缓存键")

# ------------------------------------------------------- 三种版本载体

check(version_from_path("/v2/orders") == "2", "路径版本")
check(version_from_path("/orders") is None, "无版本前缀")
check(version_from_path("/v2") == "2", "路径版本可到根")
check(version_from_path("/version2/orders") is None, "前缀必须是 /v 紧接数字")
check(version_from_media_type("application/vnd.example.v2+json") == "2", "媒体类型版本")
check(version_from_media_type("application/json") is None, "普通媒体类型无版本")
check(version_from_media_type("application/vnd.example.v2+xml") is None, "后缀必须是 +json")
check(version_from_header({"API-Version": "3"}) == "3", "请求头版本")
check(version_from_header({}) is None, "无该头")

check(resolve_version(path="/v2/orders") == ("2", "path"), "只有路径时取路径")
check(resolve_version(accept="application/vnd.example.v3+json") == ("3", "media-type"),
      "只有媒体类型时取媒体类型")
check(resolve_version(headers={"API-Version": "4"}) == ("4", "header"), "只有头时取头")
check(resolve_version(path="/v2/orders", headers={"API-Version": "4"}) == ("4", "header"),
      "头与路径冲突时按本 demo 口径取头")
check(resolve_version(path="/v2/orders", accept="application/vnd.example.v3+json",
                      headers={"API-Version": "4"}) == ("4", "header"),
      "三者冲突时按本 demo 口径取头")
check(resolve_version() == ("1", "default"), "都没有时回落到默认版本")

if __name__ == "__main__":
    print()
    import harness
    if harness.FAILURES:
        print("FAILED %d: %s" % (len(harness.FAILURES), harness.FAILURES[:5]))
        sys.exit(1)
    print("ALL PASS (%d 断言)" % harness.count())
