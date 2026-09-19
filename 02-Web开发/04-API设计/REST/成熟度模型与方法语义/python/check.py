# -*- coding: utf-8 -*-
"""RMM + RFC 9110 §9.2 自检。运行：python check.py"""

import sys

import maturity as M

FAIL = 0
COUNT = 0


def ck(label, cond):
    global FAIL, COUNT
    COUNT += 1
    if not cond:
        FAIL += 1
        print("FAIL: %s" % label)


# --- §9.2.1 Safe Methods ----------------------------------------------------
for m in ("GET", "HEAD", "OPTIONS", "TRACE"):
    ck("%s 是 safe" % m, M.is_safe(m))
for m in ("POST", "PUT", "PATCH", "DELETE"):
    ck("%s 不是 safe" % m, not M.is_safe(m))
ck("safe 方法全是幂等的（§9.2.2 明列）", all(M.is_idempotent(m) for m in M.SAFE_METHODS))

# --- §9.2.2 Idempotent Methods ---------------------------------------------
for m in ("GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE"):
    ck("%s 幂等" % m, M.is_idempotent(m))
for m in ("POST", "PATCH"):
    ck("%s 不幂等" % m, not M.is_idempotent(m))

ck("客户端可自动重试 PUT", M.client_may_auto_retry("PUT"))
ck("客户端不 SHOULD 自动重试 POST", not M.client_may_auto_retry("POST"))
ck("知道语义幂等时可以重试 POST", M.client_may_auto_retry("POST", knows_idempotent=True))
ck("能检测『原请求从未被应用』时可重试 POST",
   M.client_may_auto_retry("POST", can_detect_never_applied=True))
ck("代理 MUST NOT 重试 POST", not M.proxy_may_auto_retry("POST"))
ck("代理可以重试 DELETE", M.proxy_may_auto_retry("DELETE"))
ck("PATCH 不幂等 → 代理也不许重试", not M.proxy_may_auto_retry("PATCH"))

# --- §9.2.1 的 MUST disallow 条款 -------------------------------------------
ck("GET /page?do=delete 是违规", M.safe_method_violation("GET", True))
ck("GET 只读不算违规", not M.safe_method_violation("GET", False))
ck("POST 执行删除动作不算 safe 违规", not M.safe_method_violation("POST", True))

# --- §9.2.3 Methods and Caching --------------------------------------------
for m in ("GET", "HEAD", "POST"):
    ck("%s 有缓存语义定义" % m, M.has_cache_semantics(m))
for m in ("PUT", "DELETE", "PATCH"):
    ck("%s 无缓存语义定义" % m, not M.has_cache_semantics(m))

# --- RMM 四层反推（Fowler 文中的预约例子） -----------------------------------
l0, l1, l2, l3 = M.level0_flow(), M.level1_flow(), M.level2_flow(), M.level3_flow()

ck("Level 0 判定", M.richardson_level(l0) == 0)
ck("Level 1 判定", M.richardson_level(l1) == 1)
ck("Level 2 判定", M.richardson_level(l2) == 2)
ck("Level 3 判定", M.richardson_level(l3) == 3)

ck("L0 只有一个端点（资源未引入）", not M.uses_resources(l0))
ck("L0 一律 200（状态码未用起来）", not M.uses_status_codes(l0))
ck("L0 一律 POST（动词未用起来）", not M.uses_http_verbs(l0))
ck("L1 引入了多个资源", M.uses_resources(l1))
ck("L1 仍一律 200 → 停在 1", not M.uses_status_codes(l1))
ck("L2 查询改用了 GET", M.uses_http_verbs(l2))
ck("L2 创建返 201", any(e.status == 201 for e in l2))
ck("L2 创建带 Location", any(e.location for e in l2))
ck("L2 冲突返 409", any(e.status == 409 for e in l2))
ck("L3 响应带超媒体控制", M.uses_hypermedia(l3))
ck("L3 的 cancel 与 self 指向同一 URI（Fowler 原文）",
   "/linkrels/appointment/cancel" in l3[1].links and "self" in l3[1].links)
ck("L3 里 well-known 关系 self 不带前缀", "self" in l3[1].links)

# 等级递增：每加一层特征就升一级
ck("L2 报文去掉超媒体 → 落成 2", M.richardson_level(
    [M.Exchange("GET", "/a", 200), M.Exchange("POST", "/b", 201)]) == 2)
ck("多个资源但仍全 200 → 落成 1", M.richardson_level(
    [M.Exchange("POST", "/a", 200), M.Exchange("POST", "/b", 200)]) == 1)
ck("只加状态码不加 GET → 仍落成 1", M.richardson_level(
    [M.Exchange("POST", "/a", 200), M.Exchange("POST", "/b", 409)]) == 1)

# --- audit 汇总 -------------------------------------------------------------
a = M.audit(l3)
ck("audit 的等级与 richardson_level 一致", a["level"] == 3)
ck("audit 列出可被代理重试的方法", a["retryable_by_proxy"] == ["GET"])
ck("audit 列出非幂等方法", a["non_idempotent"] == ["POST"])
ck("audit 未误报 safe 违规", a["safe_violations"] == 0)
ck("Level 3 的含义串", "discoverability" in str(a["meaning"]))

print("assertions=%d fail=%d" % (COUNT, FAIL))
sys.exit(1 if FAIL else 0)
