"""02-RateLimit响应头部 自检。运行：python selfcheck_ratelimit_headers.py"""

import sys

from ratelimit_headers import (
    PROBLEM_STATUS,
    HeaderError,
    is_throttled_problem,
    parse_rate_limit,
    parse_rate_limit_policy,
    safe_wait,
)

OK = []
FAIL = []


def ck(name, cond):
    (OK if cond else FAIL).append(name)


def rejected(fn):
    try:
        fn()
    except HeaderError:
        return True
    return False


# ---- 1. RateLimit-Policy 解析（draft-11 §3） ------------------------------
pol = parse_rate_limit_policy('"burst";q=100;w=60,"daily";q=1000;w=86400')
ck("两条策略被拆开", len(pol) == 2)
ck("第一条 name/q/w 正确", pol[0].name == "burst" and pol[0].q == 100 and pol[0].w == 60)
ck("第二条 name/q/w 正确", pol[1].name == "daily" and pol[1].q == 1000 and pol[1].w == 86400)
ck("缺省配额单位是 requests", pol[0].qu == "requests")
ck("策略速率 = q/w", abs(pol[0].rate - 100 / 60) < 1e-12)

pol2 = parse_rate_limit_policy('"permin";q=50;w=60,"perhr";q=1000;w=3600')
ck("规范 §3.2 示例解析正确", [p.q for p in pol2] == [50, 1000])

# ---- 2. 参数校验：q 必填、w 非零、qu 受限 ---------------------------------
ck("q 缺失 → 非法", rejected(lambda: parse_rate_limit_policy('"x";w=60')))
ck("q 为负 → 非法", rejected(lambda: parse_rate_limit_policy('"x";q=-1;w=60')))
ck("w=0 → 非法（§3.1.3 非负且非零）",
   rejected(lambda: parse_rate_limit_policy('"x";q=1;w=0')))
ck("w 为负 → 非法", rejected(lambda: parse_rate_limit_policy('"x";q=1;w=-5')))
ck("qu=content-bytes 合法",
   parse_rate_limit_policy('"x";q=65535;qu="content-bytes";w=10')[0].qu == "content-bytes")
ck("qu=concurrent-requests 合法",
   parse_rate_limit_policy('"x";q=2;qu="concurrent-requests";w=10')[0].qu == "concurrent-requests")
ck("未注册的 qu → 非法",
   rejected(lambda: parse_rate_limit_policy('"x";q=1;qu="bananas";w=10')))
ck("空 List → 非法", rejected(lambda: parse_rate_limit_policy("   ")))

# ---- 3. 分区键 pk 是字节序列 ----------------------------------------------
p = parse_rate_limit_policy('"peruser";q=100;w=60;pk=:cHsdsRa894==:')
ck("pk 被解成字节", p[0].pk is not None and len(p[0].pk) > 0)
ck("pk 非字节序列形式 → 非法",
   rejected(lambda: parse_rate_limit_policy('"x";q=1;w=1;pk=abc')))

# ---- 4. RateLimit 解析（draft-11 §4） -------------------------------------
rl = parse_rate_limit('"default";r=50;t=30')
ck("r/t 解析正确", rl[0].name == "default" and rl[0].r == 50 and rl[0].t == 30)
ck("有效窗口速率 = r/t", abs(rl[0].rate - 50 / 30) < 1e-12)
rl2 = parse_rate_limit('"default";r=999;pk=:dHJpYWwxMjEzMjM=:')
ck("无 t 时 rate 为 None（不可推算）", rl2[0].t is None and rl2[0].rate is None)
ck("r 缺失 → 非法", rejected(lambda: parse_rate_limit('"default";t=30')))
ck("t=0 允许（非负整数，0 合法）", parse_rate_limit('"d";r=0;t=0')[0].t == 0)

# ---- 5. 多策略取最保守 -----------------------------------------------------
multi = parse_rate_limit('"burst";r=10;t=1,"daily";r=500;t=86400')
rates = [i.rate for i in multi]
ck("多 Item 中取最小速率对应的等待时间",
   abs(safe_wait({"RateLimit": '"burst";r=10;t=1,"daily";r=500;t=86400'})
       - 1.0 / min(rates)) < 1e-12)

# ---- 6. 客户端优先级链（§7） ----------------------------------------------
hdr = {"RateLimit": '"default";r=1;t=7'}
ck("无 Retry-After 时按有效窗口推算", abs(safe_wait(hdr) - 7.0) < 1e-12)
ck("Retry-After 存在时 MUST 优先（即使更大也照用）",
   safe_wait(hdr, retry_after=120) == 120.0)
ck("Retry-After 更小时同样优先", safe_wait(hdr, retry_after=1) == 1.0)
ck("畸形 RateLimit 字段被忽略（§7 MUST ignore）",
   safe_wait({"RateLimit": '"default";r=notanint;t=7'}) == 0.0)
ck("完全没有 RateLimit 字段时不退避", safe_wait({}) == 0.0)
ck("有 r 但无 t 时不据此退避", safe_wait({"RateLimit": '"d";r=999'}) == 0.0)

# ---- 7. 问题类型（§5） ----------------------------------------------------
ok, vp = is_throttled_problem({
    "type": "https://iana.org/assignments/http-problem-types#quota-exceeded",
    "violated-policies": ["daily", "bandwidth"],
})
ck("识别 quota-exceeded", ok)
ck("violated-policies 是字符串数组", vp == ["daily", "bandwidth"])
ok2, _ = is_throttled_problem({"type": "urn:example:other"})
ck("非限流问题类型不识别", not ok2)

# ---- 8. 规范原文的状态码笔误（照录 + 说明） --------------------------------
ck("§5.1 示例写的是 429 Bad Request", PROBLEM_STATUS["quota-exceeded"] == (429, "Bad Request"))
ck("§5.3 示例写的是 429 Too Many Requests",
   PROBLEM_STATUS["abnormal-usage-detected"] == (429, "Too Many Requests"))
ck("§5.2 用 503（能力临时下降）", PROBLEM_STATUS["temporary-reduced-capacity"][0] == 503)
ck("RFC 6585 §4 定义 429 的短语是 Too Many Requests，故 §5.1 系笔误",
   PROBLEM_STATUS["quota-exceeded"][1] != "Too Many Requests")

if FAIL:
    print("FAILED %d:" % len(FAIL))
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("ratelimit_headers selfcheck OK: %d assertions" % len(OK))
