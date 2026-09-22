"""607 API 版本化与弃用 —— 演示入口。

同一条 /orders 端点，走完「宣告弃用 → 到期下线」的两阶段，
并演示三种版本载体如何在同一次请求里并存（以及它们互相冲突时怎么办）。
"""

from negotiate import (
    cache_key_matches,
    media_range_matches,
    negotiate,
    parse_accept,
    parse_vary,
    quality,
    resolve_version,
    specificity,
    version_from_media_type,
    version_from_path,
)
from sunset import Endpoint, format_sf_date, imf_fixdate, parse_http_date, parse_sf_date

# draft-07 §4 的官方示例：2023-06-30T23:59:59Z 弃用，2024-06-30T23:59:59Z 下线
DEPRECATED_AT = 1688169599
SUNSET_AT = 1719791999


def main():
    print("== Deprecation（sf-date）与 Sunset（HTTP-date）==")
    print("  Deprecation: @1688169599 →", parse_sf_date("@1688169599"))
    print("  Sunset: Sun, 30 Jun 2024 23:59:59 GMT →",
          parse_http_date("Sun, 30 Jun 2024 23:59:59 GMT"))
    ep = Endpoint("GET /v1/orders", DEPRECATED_AT, SUNSET_AT,
                  deprecation_link="https://developer.example.com/deprecation",
                  sunset_link="https://developer.example.com/sunset")
    print("  响应头:", ep.headers())
    print("  校验:", ep.errors() or "OK（Sunset 不早于 Deprecation）")
    print("  链接:", "deprecation →", ep.deprecation_link, "/ sunset →", ep.sunset_link)
    for now, label in ((1670000000, "2022-12"), (1700000000, "2023-11"),
                       (1750000000, "2025-06")):
        print("    %s: phase=%s usable=%s hint=%s"
              % (label, ep.phase(now), ep.usable(now), ep.hint_vs_status(now, 200)))

    bad = Endpoint("GET /v1/bad", DEPRECATED_AT, DEPRECATED_AT - 86400)
    print("  反例（Sunset 早于 Deprecation）:", bad.errors())

    print()
    print("== Accept 媒体范围与 qvalue（RFC 9110 §12.5.1）==")
    table5 = ("text/*;q=0.3, text/plain;q=0.7, text/plain;format=flowed, "
              "text/plain;format=fixed;q=0.4, */*;q=0.5")
    items = parse_accept(table5)
    print("  解析出 %d 个 media-range" % len(items))
    for mt in ("text/plain;format=flowed", "text/plain", "text/html", "image/jpeg",
               "text/plain;format=fixed", "text/html;level=3"):
        specs = [specificity(i) for i in items if media_range_matches(i, mt)]
        print("    %-24s q=%.3f spec=%s" % (mt, quality(items, mt), max(specs)))
    print("  官方 Table 5 写 text/html;level=3 = 0.7；按规则推导 = 0.3")
    print("  → Errata 7306（2022-11-09 Verified）确认应为 0.3")

    avail = [("json", "application/json"), ("v2json", "application/vnd.example.v2+json"),
             ("html", "text/html")]
    print("  协商 Accept: application/vnd.example.v2+json →",
          negotiate("application/vnd.example.v2+json", avail))
    print("  协商 Accept: text/html;q=0.1, */*;q=0.05 →",
          negotiate("text/html;q=0.1, */*;q=0.05", avail))
    print("  协商 Accept: application/xml（全 0）→",
          negotiate("application/xml", avail), "（None 即 406）")

    print()
    print("== Vary 与缓存复用（§12.5.5）==")
    print("  Vary: accept-encoding, accept-language →", parse_vary("accept-encoding, accept-language"))
    print("  Vary: * →", parse_vary("*"))
    stored = {"accept-encoding": "gzip"}
    print("  同值可复用:", cache_key_matches("accept-encoding", stored, {"accept-encoding": "gzip"}))
    print("  异值不可复用:", cache_key_matches("accept-encoding", stored, {"accept-encoding": "br"}))
    print("  通配符不可复用:", cache_key_matches("*", stored, stored))

    print()
    print("== 三种版本载体并存 ==")
    print("  path /v2/orders →", version_from_path("/v2/orders"))
    print("  media type      →", version_from_media_type("application/vnd.example.v2+json"))
    print("  仅路径:", resolve_version(path="/v2/orders"))
    print("  仅媒体类型:", resolve_version(accept="application/vnd.example.v3+json"))
    print("  头 + 路径冲突:", resolve_version(path="/v2/orders", headers={"API-Version": "4"}))
    print("  三者冲突:", resolve_version(path="/v2/orders",
                                    accept="application/vnd.example.v3+json",
                                    headers={"API-Version": "4"}))
    print()
    print("  格式化回写:", format_sf_date(DEPRECATED_AT), "/", imf_fixdate(SUNSET_AT))


if __name__ == "__main__":
    main()
