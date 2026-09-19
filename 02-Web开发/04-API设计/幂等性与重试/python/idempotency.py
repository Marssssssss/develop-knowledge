# -*- coding: utf-8 -*-
"""Idempotency-Key 的强制语义（draft-ietf-httpapi-idempotency-key-header-07）。

覆盖规范里的可判定条款：
  * §2.1  Idempotency-Key 是 RFC 8941 Item Structured Header，值 **MUST be a String**
          → 线上形态必须带引号：`Idempotency-Key: "8e03978e-..."`。
  * §2.2  键 MUST 唯一，且 MUST NOT 与不同 payload 一起复用；推荐 UUID。
  * §2.3  资源 MAY 要求基于时间的键以便过期清理；SHOULD 公布过期策略。
  * §2.4  fingerprint 可由整个 payload 的校验和 / 选中字段 / 逐字段比对 / 请求签名生成。
  * §2.6  三种情形：首次（正常处理）、重试（**返回先前已完成的结果**，成功或错误）、
          并发重试（**回资源冲突错误**）。
  * §2.7  缺头 → 400（body 带文档链接）；键复用但 payload 不同 → 422；并发未完成 → 409。
          客户端 MUST 修正请求后再重试（**409 例外，无需修正**）。
  * §5    低熵键 → 攻击者可猜到别人的键并读到别人缓存的条目 → 实现唯一的**复合键**
          （客户端键 + 只有资源知道的客户端属性）。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Dict, Optional, Tuple

# RFC 8941 §4.2 的 quoted-string：只覆盖可打印 ASCII（本 demo 的键都落在这个范围）
SF_STRING_RE = re.compile(r'^"([\x20-\x21\x23-\x5b\x5d-\x7e]*)"$')
UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
# §6 给的第二种示例：随机串生成器（32 位小写字母数字）
RANDOM_RE = re.compile(r'^[a-z0-9]{32}$')

DOC_LINK = '<https://developer.example.com/idempotency>; rel="describedby"; type="text/html"'
PROBLEM_TYPE = "https://developer.example.com/idempotency"

# §2.1：结构化字段 String —— 不带引号的值不合法
def parse_structured_string(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    m = SF_STRING_RE.match(raw.strip())
    return m.group(1) if m else None


def is_valid_key(key: str) -> bool:
    """§5：实现应"建立固定的键格式并公布之"，处理前一律校验。

    本 demo 公布两种格式：UUID（§2.2 RECOMMENDED）与 32 位小写随机串（§6 示例）。
    """
    return bool(UUID_RE.match(key) or RANDOM_RE.match(key))


def fingerprint(payload: Any) -> str:
    """§2.4：整个 payload 的校验和（demo 用规范化 JSON 的 SHA-256）。"""
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def problem(status: int, title: str, detail: str) -> Dict[str, Any]:
    return {"type": PROBLEM_TYPE, "status": status, "title": title, "detail": detail}


# --------------------------------------------------------------------------
# 幂等性中间件
# --------------------------------------------------------------------------
class Record:
    __slots__ = ("fingerprint", "state", "status", "body", "expires_at")

    def __init__(self, fp: str, expires_at: float) -> None:
        self.fingerprint = fp
        self.state = "in_progress"   # in_progress | done
        self.status = 0
        self.body: Any = None
        self.expires_at = expires_at


class Response:
    def __init__(self, status: int, body: Any = None,
                 headers: Optional[Dict[str, str]] = None,
                 note: str = "") -> None:
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.note = note

    def __repr__(self) -> str:
        return "Response(%d, %s%s)" % (self.status, self.body,
                                       (" [%s]" % self.note) if self.note else "")


class IdempotencyMiddleware:
    """把幂等语义包在业务 handler 外面。

    * `require_key=True` 时缺头直接 400（带上文档链接）；
    * 存储按 **(client_id, key)** 复合键索引 —— §5 明确要求，否则猜到键就能读到别人的结果；
    * `ttl` 控制 §2.3 的过期策略。
    """

    def __init__(self, ttl: float = 86400.0, require_key: bool = True) -> None:
        self.ttl = ttl
        self.require_key = require_key
        self.store: Dict[Tuple[str, str], Record] = {}
        self.clock = 0.0

    # -- 过期清理（§2.3）----------------------------------------------------
    def purge_expired(self) -> int:
        dead = [k for k, r in self.store.items() if r.expires_at <= self.clock]
        for k in dead:
            del self.store[k]
        return len(dead)

    def handle(self, method: str, client_id: str, headers: Dict[str, str],
               payload: Any,
               handler: Callable[[], Tuple[int, Any]]) -> Response:
        # 幂等方法不需要 Idempotency-Key（draft §1：POST / PATCH 才是非幂等的）
        if method.upper() in ("GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE"):
            status, body = handler()
            return Response(status, body, note="method already idempotent")

        raw = headers.get("Idempotency-Key")
        if raw is None:
            if self.require_key:
                return Response(400, problem(400, "Idempotency-Key is missing",
                                             "This operation is idempotent and it requires "
                                             "correct usage of Idempotency Key."),
                                {"Link": DOC_LINK}, note="missing key")
            status, body = handler()          # 不要求键时照常处理 → 会重复创建
            return Response(status, body, note="no key required")

        key = parse_structured_string(raw)
        if key is None:
            # §2.1：值必须是结构化字段的 String（带引号）
            return Response(400, problem(400, "Idempotency-Key is malformed",
                                         "The Idempotency-Key field value MUST be a String."),
                            {"Link": DOC_LINK}, note="not a structured string")
        if not is_valid_key(key):
            # §5：处理任何请求之前必须按键的公布格式校验
            return Response(400, problem(400, "Idempotency-Key is invalid",
                                         "The key does not match the published format (UUID)."),
                            {"Link": DOC_LINK}, note="format rejected")

        fp = fingerprint(payload)
        ck = (client_id, key)                 # §5 复合键
        rec = self.store.get(ck)

        # §2.3 过期即视为新键
        if rec is not None and rec.expires_at <= self.clock:
            del self.store[ck]
            rec = None

        if rec is None:
            rec = Record(fp, self.clock + self.ttl)
            self.store[ck] = rec
            status, body = handler()
            rec.state = "done"
            rec.status = status
            rec.body = body
            return Response(status, body, note="first time")

        if rec.fingerprint != fp:
            # §2.7：同一键配不同 payload → 422
            return Response(422, problem(422, "Idempotency-Key is already used",
                                         "Idempotency Key MUST not be reused across different "
                                         "payloads of this operation."),
                            {"Link": DOC_LINK}, note="fingerprint mismatch")

        if rec.state == "in_progress":
            # §2.6 并发重试 → 409（客户端无需修正即可重试）
            return Response(409, problem(409, "A request is outstanding for this Idempotency-Key",
                                         "A request with the same Idempotency-Key for the same "
                                         "operation is being processed or is outstanding."),
                            {"Link": DOC_LINK}, note="concurrent")

        # §2.6 重试：返回先前已完成操作的结果（成功或错误）
        return Response(rec.status, rec.body, note="replayed")
