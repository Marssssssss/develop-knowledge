"""CSP3 的 'strict-dynamic' 与违规报告最小模型。

依据 W3C *Content Security Policy Level 3*(https://www.w3.org/TR/CSP3/ 全文实读)：

  * §6.7.1.1 script 指令的 pre-request check:
      ① 若指令值含 "'none'" 且只此一项 → "Blocked"
      ② SRI 元数据匹配源列表 → "Allowed"
      ③ **若源列表含 'strict-dynamic':请求 parser metadata 为
         "parser-inserted" → "Blocked",否则 → "Allowed"**
      ④ 否则走普通 URL 匹配
  * §6.7.2.6 Does url match source list?:
      源列表为空 → "Does Not Match";**只有一项且是 "'none'"** → "Does Not Match";
      原文 note:空源列表等价于 'none';**'none' 与其它表达式并存时不起作用**
      («'none', https://example.com» 仍然匹配 example.com)。
  * §6.7.3.2 Does a source list allow all inline behavior for type?:
      遇到 nonce-source / hash-source → 立即 "Does Not Allow";
      **type 为 "script"/"script attribute"/"navigation" 且遇到 'strict-dynamic'
      → 立即 "Does Not Allow"**;遇到 'unsafe-inline' → 置 allow;
      末尾按 allow 返回 "Allows" / "Does Not Allow"。
  * §6.7.3.3 Does element match source list for type and source?:
      ① 上一步返回 "Allows" → "Matches"
      ② type 为 script/style 且元素 nonceable:nonce 属性与 nonce-source 的值相等 → "Matches"
         (原文 note:**nonce 只作用于 inline script / inline style,
          不作用于属性,也不作用于 javascript: 导航**)
      ③ 遍历源列表:遇 'strict-dynamic' 且 **type 是 "script" 且元素非
         parser-inserted** → "Matches"
      ④ 遇 hash-source:把 base64-value 里的 '-'→'+'、'_'→'/'(原文 note:
         这是把 base64url 归一化成 base64 再比对)后与实际的 base64 摘要比对
  * §8.2 'strict-dynamic' 的语义(非规范但权威):
      - host-source / path / scheme-source 以及 'self' 与 'unsafe-inline' **在加载
        script 时被忽略**,只有 nonce-source 与 hash-source 被尊重
      - **`document.write()` 产生的 script 元素是 "parser-inserted"**,因此被拦;
        而 `createElement()` 产生的不是,因此被放行
      - 原文警告:若运行时脚本的 URL 可被攻击者控制,该策略就会放行任意脚本
  * §8.5 Strict CSP 的判据:`script-src` 只用 nonce/hash 源表达式配
    'strict-dynamic',且 `base-uri` 为 'self' 或 'none'。
  * §2.4.1 / §2.4:违规对象含 documentURL / referrer / blockedURL /
    effectiveDirective / originalPolicy / sourceFile / sample / disposition /
    statusCode / lineNumber / columnNumber;**sample 是内联脚本/事件处理器/样式的
    前 40 个字符**,外部文件的违规**不含 sample**。
  * §1 变更记录:**`report-uri` 已被弃用,取而代之的是基于 [REPORTING] 的
    `report-to`**;§5 的 csp hash report 走 `Reporting-Endpoints` 声明的端点,
    report type 为 "csp-hash",且**对 ReportingObserver 不可见**。
"""

import base64
import hashlib

# §1:report-uri 已弃用
DEPRECATED_DIRECTIVES = ("report-uri",)
REPORTING_DIRECTIVES = ("report-to",)

NONCE_PREFIX = "'nonce-"
HASH_PREFIXES = ("'sha256-", "'sha384-", "'sha512-")


def parse_source_list(value):
    return value.split() if value and value.strip() else []


def is_keyword(expr, name):
    return expr.lower() == "'%s'" % name


def is_nonce_source(expr):
    return expr.startswith(NONCE_PREFIX) and expr.endswith("'")


def is_hash_source(expr):
    return any(expr.startswith(p) for p in HASH_PREFIXES) and expr.endswith("'")


# --------------------------------------------- §6.7.2.6 Does url match source list?
def does_url_match_source_list(url, source_list, self_origin):
    if not source_list:
        return False
    if len(source_list) == 1 and is_keyword(source_list[0], "none"):
        return False
    for expr in source_list:
        if is_keyword(expr, "none"):
            continue                      # 'none' 与其它表达式并存时不起作用
        if is_keyword(expr, "self"):
            if url.startswith(self_origin):
                return True
            continue
        if expr.endswith(":") and "://" not in expr:
            if url.startswith(expr):
                return True
            continue
        if expr == "*" or url.startswith(expr):
            return True
    return False


# ------------------------------ §6.7.3.2 Does a source list allow all inline behavior?
def allows_all_inline(source_list, element_type):
    allow = False
    for expr in source_list:
        if is_nonce_source(expr) or is_hash_source(expr):
            return False
        if element_type in ("script", "script attribute", "navigation") \
                and is_keyword(expr, "strict-dynamic"):
            return False
        if is_keyword(expr, "unsafe-inline"):
            allow = True
    return allow


# --------------------- §6.7.3.3 Does element match source list for type and source?
class Element:
    def __init__(self, tag="script", nonce=None, parser_inserted=True,
                 nonceable=True, text=""):
        self.tag = tag
        self.nonce = nonce
        self.parser_inserted = parser_inserted
        self.nonceable = nonceable
        self.text = text


_HASH_ALGO = {"'sha256-": hashlib.sha256,
              "'sha384-": hashlib.sha384,
              "'sha512-": hashlib.sha512}


def _base64_digest(algo, source_bytes):
    return base64.b64encode(algo(source_bytes).digest()).decode("ascii")


def does_element_match(element, source_list, element_type, source):
    if allows_all_inline(source_list, element_type):
        return True
    if element_type in ("script", "style") and element.nonceable:
        for expr in source_list:
            if is_nonce_source(expr):
                expected = expr[len(NONCE_PREFIX):-1]
                if element.nonce is not None and element.nonce == expected:
                    return True
    for expr in source_list:
        if is_keyword(expr, "strict-dynamic"):
            if element_type == "script" and not element.parser_inserted:
                return True
            continue
        if is_hash_source(expr):
            for prefix, algo in _HASH_ALGO.items():
                if expr.startswith(prefix):
                    expected = expr[len(prefix):-1].replace("-", "+").replace("_", "/")
                    actual = _base64_digest(algo, source.encode("utf-8"))
                    if actual == expected:
                        return True
    return False


# -------------------------------------- §6.7.1.1 script 指令的 pre-request check
class ScriptRequest:
    def __init__(self, url, parser_metadata="parser-inserted", integrity=None):
        self.url = url
        self.parser_metadata = parser_metadata
        self.integrity = integrity


def script_pre_request_check(request, source_list, self_origin):
    """返回 "Allowed" / "Blocked"。"""
    if not source_list or (len(source_list) == 1 and is_keyword(source_list[0], "none")):
        return "Blocked"
    if request.integrity and _integrity_matches(request.integrity, source_list):
        return "Allowed"
    for expr in source_list:
        if is_keyword(expr, "strict-dynamic"):
            if request.parser_metadata == "parser-inserted":
                return "Blocked"
            return "Allowed"
    if does_url_match_source_list(request.url, source_list, self_origin):
        return "Allowed"
    return "Blocked"


def _integrity_matches(integrity, source_list):
    for expr in source_list:
        if is_hash_source(expr):
            for prefix in _HASH_ALGO:
                if expr.startswith(prefix):
                    expected = expr[len(prefix):-1].replace("-", "+").replace("_", "/")
                    if integrity == expected or integrity == expr[len(prefix):-1]:
                        return True
    return False


# --------------------------------------------------------------- 违规与报告
class Violation:
    def __init__(self, document_url, effective_directive, original_policy,
                 disposition, blocked_url=None, source_file=None, sample="",
                 status_code=200, line_number=None, column_number=None,
                 referrer=""):
        self.document_url = document_url
        self.effective_directive = effective_directive
        self.original_policy = original_policy
        self.disposition = disposition
        self.blocked_url = blocked_url
        self.source_file = source_file
        self.sample = sample
        self.status_code = status_code
        self.line_number = line_number
        self.column_number = column_number
        self.referrer = referrer

    def as_report_body(self):
        """§5:`application/reports+json` 里 csp-violation 的 body 字段集合。"""
        body = {
            "documentURL": self.document_url,
            "referrer": self.referrer,
            "disposition": self.disposition,
            "effectiveDirective": self.effective_directive,
            "originalPolicy": self.original_policy,
            "statusCode": self.status_code,
        }
        if self.blocked_url:
            body["blockedURL"] = self.blocked_url
        if self.source_file:
            body["sourceFile"] = self.source_file
            body["lineNumber"] = self.line_number
            body["columnNumber"] = self.column_number
        if self.sample:
            body["sample"] = self.sample
        return body


def create_violation(global_obj, policy, directive, source="", blocked_url=None):
    """§2.4.1 + §2.4:sample 取**前 40 个字符**;外部文件违规不带 sample。"""
    sample = source[:40] if source else ""
    v = Violation(document_url=global_obj.document_url,
                  effective_directive=directive,
                  original_policy=policy.serialized,
                  disposition=policy.disposition,
                  blocked_url=blocked_url,
                  source_file=global_obj.source_file if source else None,
                  sample=sample,
                  status_code=global_obj.status_code,
                  line_number=global_obj.line_number if source else None,
                  column_number=global_obj.column_number if source else None,
                  referrer=global_obj.referrer)
    global_obj.violations.append(v)
    return v


class Global:
    def __init__(self, document_url="https://example.com/", status_code=200,
                 referrer="", source_file=None, line_number=None,
                 column_number=None):
        self.document_url = document_url
        self.status_code = status_code
        self.referrer = referrer
        self.source_file = source_file
        self.line_number = line_number
        self.column_number = column_number
        self.violations = []


class Policy:
    def __init__(self, serialized, disposition="enforce", **directives):
        self.serialized = serialized
        self.disposition = disposition
        self.directives = {k: v for k, v in directives.items() if v is not None}

    def source_list(self, name):
        return parse_source_list(self.directives.get(name, ""))

    def reporting_endpoints(self):
        """§1 / §5:report-to 指向 [REPORTING] 端点;report-uri 已弃用。"""
        eps = []
        if self.directives.get("report-to"):
            eps.append(self.directives["report-to"])
        if self.directives.get("report-uri"):
            eps.append(("deprecated", self.directives["report-uri"]))
        return eps


# ------------------------------------------------------------- §8.5 Strict CSP
def is_strict_csp(policy):
    """§8.5:script-src 只用 nonce/hash + 'strict-dynamic',base-uri ∈ {'self','none'}。"""
    src = policy.source_list("script-src") or policy.source_list("default-src")
    if not src:
        return False
    has_dynamic = any(is_keyword(e, "strict-dynamic") for e in src)
    only_nonce_or_hash = True
    for e in src:
        if is_keyword(e, "strict-dynamic") or is_nonce_source(e) or is_hash_source(e):
            continue
        only_nonce_or_hash = False
    base = policy.source_list("base-uri")
    base_ok = len(base) == 1 and (is_keyword(base[0], "self")
                                  or is_keyword(base[0], "none"))
    return has_dynamic and only_nonce_or_hash and base_ok
