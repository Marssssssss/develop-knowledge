"""SRI(Subresource Integrity)与 Integrity-Policy 最小模型。

依据 W3C *Subresource Integrity*（https://www.w3.org/TR/SRI/ 全文实读）：

  * §2 "valid SRI hash algorithm token set" 是**有序**集合
    «"sha256", "sha384", "sha512"»，更强的算法排在后面（§3.2.2 / §3.3.3）。
  * §2 digest = **base64 编码**（RFC 4648 §4 的**标准 base64**，带 `+` `/` 与
    `=` 填充），不是 base64url。官方例子里的 `...t+eX6xO` 与 `...Prw==` 就是证据。
  * §3.3.2 Parse metadata：按空格切分 → 每项按 `?` 切成
    (表达式, 选项表达式) → 表达式按 `-` 切成 (算法, base64值)；
    **不认识的算法直接跳过**（continue）；目前没有任何 options 被定义。
  * §3.3.3 Get the strongest metadata from set：按上面那个有序集合的下标比较，
    下标更大者胜出并**清空**结果集；**下标相同则都保留**（§3.3.4 因此允许多份
    同强度摘要，命中任意一份即通过）。
  * §3.3.4 Do bytes match metadataList?：**解析结果为空集 → 返回 true**
    （没有 integrity 属性就等于不做校验）；否则只用"最强"那批去比对。
  * §3.3.4 note：**SRI 需要 CORS**，不带 CORS 去用 SRI 是"逻辑错误"，
    规范鼓励 UA 在控制台告警。
  * §3.7 校验失败 → UA 拒绝渲染/执行，返回 **network error**，并触发 error 事件
    （所以可以用 error 事件回退到自备副本）。
  * §3.8 Integrity-Policy / Integrity-Policy-Report-Only：值是 RFC 9651 的
    Dictionary；`sources` 只可能是 `"inline"`（**缺省即 inline**）；
    `blocked-destinations` 取 `"script"` / `"style"`；`endpoints` 指向
    Reporting-Endpoints 声明的端点，JS 侧可用 ReportingObserver 收
    `"integrity-violation"`。
  * §3.8.2 Should request be blocked by integrity policy：
      ① 有 integrity 元数据且 mode ∈ {cors, same-origin} → Allowed
      ② url 是 local → Allowed
      ③ 强制策略与 report-only 策略都为空 → Allowed
      ④ 策略的 sources 含 "inline" 且 blocked destinations 含本次 destination → block
      ⑤ report-only 命中 → 报 reportOnly=true 的违规但**不阻断**
"""

import base64
import hashlib

# §2：有序集合，更强的排在后面
VALID_ALGOS = ("sha256", "sha384", "sha512")

_HASHER = {"sha256": hashlib.sha256,
           "sha384": hashlib.sha384,
           "sha512": hashlib.sha512}


def digest_of(data, algo):
    """§3.3.1 Apply algorithm to bytes + base64 编码（标准 base64，带填充）。"""
    return base64.b64encode(_HASHER[algo](data).digest()).decode("ascii")


def integrity_metadata(data, algo="sha384"):
    return "%s-%s" % (algo, digest_of(data, algo))


# ------------------------------------------------------------- §3.3.2 解析
def parse_metadata(metadata):
    """返回 [{"alg":..., "val":...}, ...]，只保留 UA 认识的算法。"""
    result = []
    for item in (metadata or "").split():
        expression = item.split("?")[0]
        parts = expression.split("-", 1)
        algo = parts[0].lower()
        if algo not in VALID_ALGOS:
            continue
        value = parts[1] if len(parts) > 1 else ""
        result.append({"alg": algo, "val": value})
    return result


# ------------------------------------------------- §3.3.3 取最强的一批元数据
def get_strongest_metadata(parsed):
    result = []
    strongest = None
    for item in parsed:
        if not result:
            result.append(item)
            strongest = item
            continue
        cur = VALID_ALGOS.index(strongest["alg"])
        new = VALID_ALGOS.index(item["alg"])
        if new < cur:
            continue
        if new > cur:
            strongest = item
            result = [item]
        else:
            result.append(item)
    return result


# --------------------------------------------------------- §3.3.4 字节比对
def do_bytes_match(data, metadata):
    parsed = parse_metadata(metadata)
    if not parsed:
        return True
    for item in get_strongest_metadata(parsed):
        if digest_of(data, item["alg"]) == item["val"]:
            return True
    return False


def verify_subresource(data, metadata, url_origin, doc_origin, crossorigin):
    """建模 §3.3.4 的 note：跨源且没带 crossorigin → 直接失败。

    返回 (ok, reason)。
    """
    if url_origin != doc_origin and not crossorigin:
        return (False, "cross-origin 且未声明 crossorigin：SRI 需要 CORS（§3.3.4 note）")
    if not do_bytes_match(data, metadata):
        return (False, "integrity 校验失败：返回 network error（§3.7）")
    return (True, "ok")


# -------------------------------------------------- §3.8 Integrity-Policy
LOCAL_SCHEMES = ("about", "blob", "data", "filesystem")


class IntegrityPolicy:
    def __init__(self):
        self.sources = []
        self.blocked_destinations = []
        self.endpoints = []

    def is_empty(self):
        return not self.blocked_destinations and not self.sources


def process_integrity_policy(dictionary):
    """§3.8：值是 RFC 9651 Dictionary；sources 缺省即 ["inline"]。"""
    policy = IntegrityPolicy()
    sources = dictionary.get("sources")
    if sources is None or "inline" in sources:
        policy.sources.append("inline")
    blocked = dictionary.get("blocked-destinations") or []
    for dest in ("script", "style"):
        if dest in blocked:
            policy.blocked_destinations.append(dest)
    policy.endpoints = list(dictionary.get("endpoints") or [])
    return policy


class PolicyContainer:
    def __init__(self, policy=None, report_only=None):
        self.integrity_policy = policy or IntegrityPolicy()
        self.report_only_integrity_policy = report_only or IntegrityPolicy()


class IntegrityRequest:
    def __init__(self, url, destination="script", mode="no-cors",
                 integrity="", local=False):
        self.url = url
        self.destination = destination
        self.mode = mode
        self.integrity = integrity
        self.local = local


class IntegrityViolation:
    def __init__(self, document_url, blocked_url, destination, report_only):
        self.document_url = document_url
        self.blocked_url = blocked_url
        self.destination = destination
        self.report_only = report_only


def should_request_be_blocked(request, container, document_url="https://example.com/",
                              violations=None):
    """§3.8.2：返回 "Blocked" / "Allowed"，并把违规收进 violations。"""
    parsed = parse_metadata(request.integrity)
    if parsed and request.mode in ("cors", "same-origin"):
        return "Allowed"
    if request.local:
        return "Allowed"
    policy = container.integrity_policy
    report_policy = container.report_only_integrity_policy
    if policy.is_empty() and report_policy.is_empty():
        return "Allowed"
    block = "inline" in policy.sources and request.destination in policy.blocked_destinations
    report_block = ("inline" in report_policy.sources
                    and request.destination in report_policy.blocked_destinations)
    if (block or report_block) and violations is not None:
        violations.append(IntegrityViolation(document_url, request.url,
                                             request.destination, False))
        if report_block and not block:
            violations[-1].report_only = True
    return "Blocked" if block else "Allowed"
