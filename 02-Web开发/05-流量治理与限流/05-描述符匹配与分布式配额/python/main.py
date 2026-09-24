"""演示入口：描述符匹配、判定与分布式缓存键。"""

from ratelimit_service import (
    LimitInfo,
    RateLimit,
    RateLimitConfig,
    generate_cache_key,
    get_response_descriptor_status,
)

DESCRIPTORS = [
    {"key": "database", "value": "users",
     "rate_limit": {"unit": "second", "requests_per_unit": 10}},
    {"key": "database",
     "rate_limit": {"unit": "second", "requests_per_unit": 100}},
    {"key": "path_/api/*",
     "descriptors": [{"key": "remote_address",
                      "rate_limit": {"unit": "minute", "requests_per_unit": 5}}]},
]

REQUESTS = [
    [("database", "users")],
    [("database", "ops")],
    [("path", "/api/v1"), ("remote_address", "1.2.3.4")],
    [("path", "/other"), ("remote_address", "1.2.3.4")],
    [("deep", "x"), ("sub", "y")],
]

COUNTERS = [(0, 1), (9, 1), (10, 1), (18, 3)]


def main():
    cfg = RateLimitConfig()
    cfg.load("mongo_cps", DESCRIPTORS)

    print("== GetLimit 匹配 ==")
    for entries in REQUESTS:
        rl, key = cfg.get_limit("mongo_cps", entries)
        shown = "None" if rl is None else "%d/%s%s" % (
            rl.requests_per_unit, rl.unit, " shadow" if rl.shadow_mode else "")
        print("  %-46s → key=%-24s limit=%s"
              % (str(entries), key, shown))
    rl, _ = cfg.get_limit("mongo_cps", [("database", "users")],
                          limit_override={"unit": "minute", "requests_per_unit": 3})
    print("  %-46s → override limit=%d/%s（绕过配置树）"
          % ("envoy 侧 limit override", rl.requests_per_unit, rl.unit))

    print("\n== 判定阈值（threshold=10，near=8）==")
    lim = RateLimit(10, "second")
    for before, addend in COUNTERS:
        info = LimitInfo(lim, before, addend, near_limit_ratio=0.8)
        code, rem, stats = get_response_descriptor_status("k", info)
        print("  before=%-3d addend=%-2d after=%-3d → %-10s stats=%s"
              % (before, addend, info.after, code, stats))

    shadow = LimitInfo(RateLimit(10, "second", shadow_mode=True), 20, 5)
    print("  shadow_mode=true, before=20 addend=5 → %s stats=%s"
          % (get_response_descriptor_status("k", shadow)[0],
             get_response_descriptor_status("k", shadow)[2]))

    print("\n== 分布式缓存键（now=1234）==")
    for unit in ("second", "minute", "hour"):
        k, ps = generate_cache_key("mongo_cps", [("database", "users")],
                                   RateLimit(10, unit), now_unix=1234)
        print("  unit=%-7s perSecond=%-5s key=%s" % (unit, ps, k))
    print("  limit=None → key=%r（空键在判定里被短路成 OK）"
          % generate_cache_key("mongo_cps", [("database", "users")], None, 1234)[0])


if __name__ == "__main__":
    main()
