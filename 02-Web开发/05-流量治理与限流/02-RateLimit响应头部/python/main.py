"""演示入口：一组真实响应头部 → 客户端等待决策。"""

from ratelimit_headers import is_throttled_problem, parse_rate_limit, parse_rate_limit_policy, safe_wait

CASES = [
    ("正常响应", {"RateLimit-Policy": '"burst";q=100;w=60,"daily";q=1000;w=86400',
                  "RateLimit": '"burst";r=10;t=60,"daily";r=500;t=43200'}, None),
    ("接近耗尽", {"RateLimit": '"default";r=1;t=7'}, None),
    ("被限流并给 Retry-After", {"RateLimit": '"default";r=0;t=7'}, 3600),
    ("畸形 RateLimit", {"RateLimit": '"default";r=abc;t=7'}, None),
]

PROBLEM = {
    "type": "https://iana.org/assignments/http-problem-types#quota-exceeded",
    "title": "Request cannot be satisfied as assigned quota has been exceeded",
    "violated-policies": ["daily", "bandwidth"],
}


def main():
    print("== 策略（RateLimit-Policy）==")
    for it in parse_rate_limit_policy('"burst";q=100;w=60,"daily";q=1000;w=86400'):
        print("  %-8s q=%-6d w=%-6d unit=%s  rate=%.4f/s" % (it.name, it.q, it.w, it.qu, it.rate))

    print("\n== 服务限额（RateLimit）→ 客户端等待 ==")
    for label, hdr, ra in CASES:
        items = hdr.get("RateLimit")
        wait = safe_wait(hdr, ra)
        detail = ""
        if items:
            try:
                detail = ", ".join("%s r=%s t=%s" % (i.name, i.r, i.t)
                                   for i in parse_rate_limit(items))
            except ValueError as exc:
                detail = "忽略(%s)" % exc
        print("  %-22s %-58s wait=%.2fs" % (label, detail, wait))

    ok, vp = is_throttled_problem(PROBLEM)
    print("\n== 问题类型 ==\n  识别=%s violated-policies=%s" % (ok, vp))


if __name__ == "__main__":
    main()
