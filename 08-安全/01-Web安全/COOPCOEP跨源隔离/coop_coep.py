"""COOP / COEP / CORP 跨源隔离最小模型。

依据以下原文(全部实读):

**HTML 标准 §7.1.3 Cross-origin opener policies**
(https://html.spec.whatwg.org/multipage/browsers.html)
  * opener policy value 五种:`unsafe-none`(默认,与前一个文档共用 top-level
    browsing context)、`same-origin-allow-popups`、`same-origin`(额外要求它打开的
    auxiliary browsing context 也必须是同源且同策略,否则"看起来是关着的")、
    `same-origin-plus-COEP`(**不能由头直接设置**,是 COOP:same-origin 与一个
    "compatible with cross-origin isolation" 的 COEP **组合**出来的)、
    `noopener-allow-popups`。
  * To match opener policy values:
      ① 两边都是 unsafe-none                                    → true
      ② 任一边是 unsafe-none                                    → false
      ③ 两边值相等 且 两个 origin 同源                            → true
      ④ 其余                                                    → false
  * Check if popup COOP values require a browsing context group switch:
      ① responseCOOP == noopener-allow-popups                   → true
      ② activeCOOP ∈ {same-origin-allow-popups, noopener-allow-popups}
         且 responseCOOP == unsafe-none                          → false
      ③ 上面的 match 为 true                                     → false
      ④ 其余                                                    → true
  * Obtain an opener policy:reservedEnvironment 是**非安全上下文**时直接返回
    默认策略(即 unsafe-none);`same-origin` 且 COEP 与跨源隔离兼容 → 升级成
    `same-origin-plus-COEP`。

**W3C/WICG Cross-Origin-Embedder-Policy**
(https://wicg.github.io/cross-origin-embedder-policy/)
  * 值是 Structured Header 的 **token**;**解析失败一律 fail-open 到
    `unsafe-none`**。官方给出的表(本 demo 逐行实现):
        (无头)                    → unsafe-none
        require-corp              → require-corp
        unknown-value             → unsafe-none
        require-corp, unknown-value → unsafe-none
        unknown-value, unknown-value → unsafe-none
        unknown-value, require-corp → unsafe-none
        require-corp, require-corp   → unsafe-none   ← 最反直觉的一条:
        多个 COEP 头合并成了 list,而 list 不是 token,于是整条被忽略
  * Cross-origin resource policy internal check:
      ① mode ∈ {same-origin, cors, websocket}                    → allowed
      ② mode == navigate:embedder policy 为 unsafe-none 时 → allowed
      ③ 取响应的 CORP;**为 null 且 embedder 是 require-corp 时按
         `same-origin` 处理**(这就是"什么都不写 = 最严格")
      ④ null / cross-origin → allowed
         same-origin  → request origin 与 current URL origin 同源才 allowed
         same-site    → 主机 same-site **且**(scheme 是 https
                        **或** 响应的 HTTPS state 是 "none")
      ⑤ 其它值 → allowed
    note:`same-site` 不认为"安全传输来的响应"匹配"非安全的发起源"。
  * 嵌套导航:parent 是 require-corp 时,子文档必须**自己主动声明**
    require-corp,否则 Blocked(§3.1.3 / §4.3 cascading vs requiring)。
"""

UNSAFE_NONE = "unsafe-none"
REQUIRE_CORP = "require-corp"
SAME_ORIGIN = "same-origin"
SAME_ORIGIN_ALLOW_POPUPS = "same-origin-allow-popups"
SAME_ORIGIN_PLUS_COEP = "same-origin-plus-COEP"
NOOPENER_ALLOW_POPUPS = "noopener-allow-popups"


# ---------------------------------------------------------- 同源 / 同站判定
def same_origin(a, b):
    return a == b


def registrable_domain(host):
    """教学用的简化 'same site' 判定:取最后两个标签(不考虑公共后缀列表)。"""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def same_site(a, b):
    return registrable_domain(a) == registrable_domain(b)


# --------------------------------------------- HTML §7.1.3 match opener policy
def match_opener_policy_values(doc_coop, doc_origin, resp_coop, resp_origin):
    if doc_coop == UNSAFE_NONE and resp_coop == UNSAFE_NONE:
        return True
    if doc_coop == UNSAFE_NONE or resp_coop == UNSAFE_NONE:
        return False
    if doc_coop == resp_coop and same_origin(doc_origin, resp_origin):
        return True
    return False


def check_popup_coop_requires_switch(resp_origin, active_origin,
                                     resp_coop, active_coop):
    if resp_coop == NOOPENER_ALLOW_POPUPS:
        return True
    if active_coop in (SAME_ORIGIN_ALLOW_POPUPS, NOOPENER_ALLOW_POPUPS) \
            and resp_coop == UNSAFE_NONE:
        return False
    if match_opener_policy_values(active_coop, active_origin,
                                  resp_coop, resp_origin):
        return False
    return True


def check_coop_requires_switch(is_initial_about_blank, resp_origin,
                               active_origin, resp_coop, active_coop):
    if is_initial_about_blank:
        return check_popup_coop_requires_switch(resp_origin, active_origin,
                                                resp_coop, active_coop)
    if match_opener_policy_values(active_coop, active_origin,
                                  resp_coop, resp_origin):
        return False
    return True


def obtain_opener_policy(coop_token, coep_compatible_with_isolation,
                         secure_context=True):
    """HTML §7.1.3.1:非安全上下文直接返回默认(unsafe-none)。"""
    policy = {"value": UNSAFE_NONE, "report_only_value": UNSAFE_NONE}
    if not secure_context:
        return policy
    if coop_token == SAME_ORIGIN:
        policy["value"] = SAME_ORIGIN_PLUS_COEP if coep_compatible_with_isolation \
            else SAME_ORIGIN
    elif coop_token == SAME_ORIGIN_ALLOW_POPUPS:
        policy["value"] = SAME_ORIGIN_ALLOW_POPUPS
    elif coop_token == NOOPENER_ALLOW_POPUPS:
        policy["value"] = NOOPENER_ALLOW_POPUPS
    return policy


# ------------------------------------------------------ COEP 解析(fail-open)
def _parse_sh_token(raw):
    """极简 Structured Header item(token)解析:不是单个 token 就返回 None。"""
    if raw is None:
        return None
    value = raw.strip()
    if not value or "," in value:
        return None
    return value


def obtain_embedder_policy(coep_header=None, report_only_header=None):
    """COEP §2.3:无法解析成 token 时一律回落到 unsafe-none。"""
    policy = {"value": UNSAFE_NONE, "report_only_value": UNSAFE_NONE,
              "reporting_endpoint": None, "report_only_reporting_endpoint": None}
    token = _parse_sh_token(coep_header)
    if token == REQUIRE_CORP:
        policy["value"] = REQUIRE_CORP
    token_ro = _parse_sh_token(report_only_header)
    if token_ro == REQUIRE_CORP:
        policy["report_only_value"] = REQUIRE_CORP
    return policy


def coep_compatible_with_cross_origin_isolation(policy):
    return policy["value"] == REQUIRE_CORP


# ------------------------------------------- CORP internal check(COEP §3.2.1)
def corp_internal_check(embedder_policy_value, mode, request_origin,
                        current_url_origin, corp_header, response_https_state="none",
                        request_scheme="https"):
    if mode in ("same-origin", "cors", "websocket"):
        return True
    if mode == "navigate" and embedder_policy_value == UNSAFE_NONE:
        return True
    policy = corp_header
    if policy is None and embedder_policy_value == REQUIRE_CORP:
        policy = SAME_ORIGIN
    if policy in (None, "cross-origin"):
        return True
    if policy == SAME_ORIGIN:
        return same_origin(request_origin, current_url_origin)
    if policy == "same-site":
        return (same_site(request_origin, current_url_origin)
                and (request_scheme == "https" or response_https_state == "none"))
    return True


def check_navigation_adherence(parent_policy_value, child_policy_value):
    """COEP §3.1.3:parent 是 require-corp 时子文档必须主动声明 require-corp。"""
    if parent_policy_value != REQUIRE_CORP:
        return "Allowed"
    if child_policy_value == UNSAFE_NONE:
        return "Blocked"
    return "Allowed"


# ----------------------------------------------------- 跨源隔离的综合判定
def is_cross_origin_isolated(coop_value, coep_policy):
    """HTML §7.1.3.1:same-origin + 兼容的 COEP → same-origin-plus-COEP。"""
    if coop_value != SAME_ORIGIN_PLUS_COEP:
        return False
    return coep_compatible_with_cross_origin_isolation(coep_policy)


def cross_origin_isolated_from_headers(coop_token, coep_header, secure=True):
    coep = obtain_embedder_policy(coep_header)
    coop = obtain_opener_policy(coop_token,
                                coep_compatible_with_cross_origin_isolation(coep),
                                secure_context=secure)
    return is_cross_origin_isolated(coop["value"], coep)
