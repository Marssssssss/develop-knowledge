# -*- coding: utf-8 -*-
"""Richardson 成熟度模型（RMM）+ RFC 9110 §9.2 方法语义。

RMM 来源：Martin Fowler, "Richardson Maturity Model: steps toward the glory of REST"
（2010-03-18，martinfowler.com/articles/richardsonMaturityModel.html，全文实读）。
方法语义来源：RFC 9110 §9.2.1 Safe Methods / §9.2.2 Idempotent Methods /
§9.2.3 Methods and Caching（实读）。

本 demo 把"成熟度"做成**可从报文反推**的判据，而不是主观打分。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

# --------------------------------------------------------------------------
# RFC 9110 §9.2 方法属性
# --------------------------------------------------------------------------
# §9.2.1 "the GET, HEAD, OPTIONS, and TRACE methods are defined to be safe"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")
# §9.2.2 "PUT, DELETE, and safe request methods are idempotent"
IDEMPOTENT_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE")
# §9.2.3 "This specification defines caching semantics for GET, HEAD, and POST"
CACHE_SEMANTICS_DEFINED = ("GET", "HEAD", "POST")


def is_safe(method: str) -> bool:
    return method.upper() in SAFE_METHODS


def is_idempotent(method: str) -> bool:
    return method.upper() in IDEMPOTENT_METHODS


def has_cache_semantics(method: str) -> bool:
    return method.upper() in CACHE_SEMANTICS_DEFINED


def client_may_auto_retry(method: str, knows_idempotent: bool = False,
                          can_detect_never_applied: bool = False) -> bool:
    """§9.2.2：非幂等方法客户端 SHOULD NOT 自动重试，除非有额外依据。"""
    if is_idempotent(method):
        return True
    return knows_idempotent or can_detect_never_applied


def proxy_may_auto_retry(method: str) -> bool:
    """§9.2.2：A proxy MUST NOT automatically retry non-idempotent requests."""
    return is_idempotent(method)


def safe_method_violation(method: str, action_is_unsafe: bool) -> bool:
    """§9.2.1：资源所有者 MUST 禁用 safe 方法上的不安全动作。

    典型反例：GET /page?do=delete —— 爬虫、预取、建索引都会顺着 URI 触发删除。
    """
    return is_safe(method) and action_is_unsafe


# --------------------------------------------------------------------------
# 报文与 RMM 判定
# --------------------------------------------------------------------------
class Exchange:
    """一次请求/响应对的可观测特征（Fowler 文中四层的例子都落在这个形状里）。"""

    def __init__(self, method: str, path: str, status: int,
                 location: Optional[str] = None,
                 links: Optional[Sequence[str]] = None,
                 body_has_resource_ids: bool = False) -> None:
        self.method = method.upper()
        self.path = path
        self.status = status
        self.location = location
        self.links = list(links or [])
        self.body_has_resource_ids = body_has_resource_ids

    def __repr__(self) -> str:
        return "%s %s -> %d" % (self.method, self.path, self.status)


def _resource_paths(ex: Sequence[Exchange]) -> List[str]:
    return [e.path.split("?", 1)[0] for e in ex]


def uses_resources(ex: Sequence[Exchange]) -> bool:
    """Level 1：不再所有请求都打同一个服务端点。"""
    return len(set(_resource_paths(ex))) > 1


def uses_http_verbs(ex: Sequence[Exchange]) -> bool:
    """Level 2：读取类交互用 safe 方法（GET）而不是一律 POST 隧道。"""
    return any(is_safe(e.method) for e in ex)


def uses_status_codes(ex: Sequence[Exchange]) -> bool:
    """Level 2：用状态码表达结果，而不是一律 200 + body 里的错误标记。"""
    return any(e.status != 200 for e in ex)


def uses_hypermedia(ex: Sequence[Exchange]) -> bool:
    """Level 3：响应里带超媒体控制，告诉客户端"下一步能做什么、往哪儿发"。"""
    return any(e.links for e in ex)


def richardson_level(ex: Sequence[Exchange]) -> int:
    """从可观测报文反推 RMM 等级（0..3）。逐级短路。"""
    if not uses_resources(ex):
        return 0
    if not (uses_http_verbs(ex) and uses_status_codes(ex)):
        return 1
    if not uses_hypermedia(ex):
        return 2
    return 3


LEVEL_MEANING: Dict[int, str] = {
    0: "把 HTTP 当隧道（RPC/POX），所有请求打同一个端点",
    1: "引入资源：把大端点拆成多个可寻址资源（分而治之）",
    2: "引入标准动词与状态码：同类情况用同样方式处理（消除不必要的变化）",
    3: "引入超媒体控制：协议自描述、可发现（discoverability）",
}


# --------------------------------------------------------------------------
# Fowler 文中「预约医生」例子在四个层级上的报文
# --------------------------------------------------------------------------
def level0_flow() -> List[Exchange]:
    """Level 0：POST /appointmentService 打天下，成功失败一律 200。"""
    return [
        Exchange("POST", "/appointmentService", 200),
        Exchange("POST", "/appointmentService", 200),
        Exchange("POST", "/appointmentService", 200),  # 失败也在 body 里说
    ]


def level1_flow() -> List[Exchange]:
    """Level 1：资源各自可寻址，但仍一律 POST、一律 200。"""
    return [
        Exchange("POST", "/doctors/mjones", 200, body_has_resource_ids=True),
        Exchange("POST", "/slots/1234", 200, body_has_resource_ids=True),
    ]


def level2_flow() -> List[Exchange]:
    """Level 2：查询用 GET（safe、可缓存），创建返 201 + Location，冲突返 409。"""
    return [
        Exchange("GET", "/doctors/mjones/slots?date=20100104&status=open", 200,
                 body_has_resource_ids=True),
        Exchange("POST", "/slots/1234", 201, location="/slots/1234/appointment"),
        Exchange("POST", "/slots/1234", 409),
    ]


def level3_flow() -> List[Exchange]:
    """Level 3：响应里带 link rel，客户端不需要自己知道该往哪儿发。"""
    return [
        Exchange("GET", "/doctors/mjones/slots?date=20100104&status=open", 200,
                 links=["/linkrels/slot/book"], body_has_resource_ids=True),
        Exchange("POST", "/slots/1234", 201, location="/slots/1234/appointment",
                 links=["/linkrels/appointment/cancel",
                        "/linkrels/appointment/addTest",
                        "self",
                        "/linkrels/appointment/changeTime",
                        "/linkrels/appointment/updateContactInfo",
                        "/linkrels/help"]),
        Exchange("POST", "/slots/1234", 409, links=["/linkrels/slot/book"]),
    ]


def audit(ex: Sequence[Exchange]) -> Dict[str, object]:
    """给出成熟度等级 + 逐条判定依据 + §9.2 层面的违规检查。"""
    violations = [e for e in ex if safe_method_violation(e.method, False)]
    return {
        "level": richardson_level(ex),
        "meaning": LEVEL_MEANING[richardson_level(ex)],
        "resources": uses_resources(ex),
        "verbs": uses_http_verbs(ex),
        "status_codes": uses_status_codes(ex),
        "hypermedia": uses_hypermedia(ex),
        "retryable_by_proxy": sorted({e.method for e in ex if proxy_may_auto_retry(e.method)}),
        "non_idempotent": sorted({e.method for e in ex if not is_idempotent(e.method)}),
        "safe_violations": len(violations),
    }
