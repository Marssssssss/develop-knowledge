"""Cookie 存储模型(RFC 6265 §5 / draft-ietf-httpbis-rfc6265bis §5.7–§5.9)。

实现的是**用户代理(浏览器)侧**的算法,而不是服务端"设置 cookie"的简化版:
判断某个 Set-Cookie 该不该收、某个请求该带哪些 cookie、带成什么顺序,
都是规范和攻击面真正所在的地方。权威来源见 README。

行内注释里的 §x.y 指 RFC 6265bis 的章节;标注 [6265] 的是 RFC 6265 原文。
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

MAX_SET_COOKIE = 4096          # §5.6 step 1 / §5.7 step 1:整条 Set-Cookie 上限(octets)
MAX_ATTR = 1024                # §5.6 / §5.7:Domain/Path 属性值上限
AGE_LIMIT = 400 * 86400        # §5.5:RECOMMENDED 上限 400 天,超限必须截断
PER_DOMAIN = 50                # §5.7 "such as 50 cookies"
TOTAL = 3000                   # §5.7 "such as 3000 cookies"

# 只为演示:真实实现用 publicsuffix.org 的完整列表
PUBLIC_SUFFIXES = {"com", "org", "net", "co.uk", "github.io", "s3.amazonaws.com"}


class Cookie:
    __slots__ = ("name", "value", "domain", "path", "creation", "expiry", "persistent",
                 "host_only", "secure_only", "http_only", "same_site", "last_access")

    def __init__(self, name, value, domain, path, creation):
        self.name, self.value = name, value
        self.domain, self.path = domain, path
        self.creation = self.last_access = creation
        self.expiry = float("inf")
        self.persistent = False
        self.host_only = False
        self.secure_only = False
        self.http_only = False
        self.same_site = "Default"      # Default / Lax / Strict / None

    def expired(self, now):
        return now > self.expiry

    def key(self):
        return (self.name, self.domain, self.host_only, self.path)


def canonical_host(url_host: str) -> str:
    """§5.1.2 规范化的最小近似:小写 + 去尾点 + IDN 留给调用方。"""
    return url_host.rstrip(".").lower()


def default_path(uri_path: str) -> str:
    """§5.1.4 default-path 算法(注意:是"到最右斜杠之前",不是"取最后一段")。"""
    if not uri_path or not uri_path.startswith("/"):
        return "/"
    if uri_path.count("/") <= 1:
        return "/"
    return uri_path[: uri_path.rfind("/")]


def domain_match(string: str, domain_string: str) -> bool:
    """§5.1.3:相同,或者是"点边界"上的后缀 —— 且 string 必须是主机名而非 IP。"""
    if not string or not domain_string:
        return False
    if string == domain_string:
        return True
    if not string.endswith(domain_string):
        return False
    if len(string) == len(domain_string):
        return False
    if string[-(len(domain_string) + 1)] != ".":
        return False
    return not re.match(r"^[0-9.]+$", string)


def path_match(request_path: str, cookie_path: str) -> bool:
    """§5.1.4 path-match(注意第二条:`/docs` 能匹配 `/docs`、`/docs/x`,但不匹配 `/docsets`)。"""
    if request_path == cookie_path:
        return True
    if not request_path.startswith(cookie_path):
        return False
    if cookie_path.endswith("/"):
        return True
    return request_path[len(cookie_path)] == "/"


class CookieStore:
    def __init__(self, reject_public_suffixes=True, lax_allowing_unsafe=True,
                 unsafe_window=120, now=0):
        self.jar: list[Cookie] = []
        self.reject_public_suffixes = reject_public_suffixes
        self.lax_allowing_unsafe = lax_allowing_unsafe
        self.unsafe_window = unsafe_window
        self.now = now

    # ---------------- 接收 ----------------
    @staticmethod
    def parse_set_cookie(header: str) -> tuple[str, str, dict] | None:
        if len(header.encode("utf-8")) > MAX_SET_COOKIE:
            return None                                   # §5.6 step 1
        nv, _, rest = header.partition(";")
        if "=" not in nv:
            return None                                   # step 2
        name, _, value = nv.partition("=")
        name, value = name.strip(), value.strip()
        if not name:                                      # step 5
            return None
        attrs: dict[str, list[str]] = {}
        for raw in rest.split(";"):
            raw = raw.strip()
            if not raw:
                continue
            k, _, v = raw.partition("=")
            attrs.setdefault(k.strip().lower(), []).append(v.strip())   # §5.3:同名属性取最后一个
        return name, value, attrs

    def receive(self, header: str, request_url: str, from_http_api=True,
                site_of_request=None) -> Cookie | None:
        parsed = self.parse_set_cookie(header)
        if parsed is None:
            return None
        name, value, attrs = parsed
        url = urlsplit(request_url)
        host = canonical_host(url.hostname or "")
        secure_conn = url.scheme == "https" or url.scheme == "wss"

        c = Cookie(name, value, host, default_path(url.path), self.now)
        # step 6-8:Max-Age 优先于 Expires;两者都没有 → 会话 cookie
        if "max-age" in attrs:
            raw = attrs["max-age"][-1]
            if re.fullmatch(r"-?[0-9]+", raw or ""):
                secs = min(int(raw), AGE_LIMIT)           # §5.5 截断到 400 天
                c.persistent = True
                c.expiry = self.now + secs if secs > 0 else -1
        elif "expires" in attrs:
            c.persistent = True
            c.expiry = self.now + 86400                   # 日期解析非本 demo 重点
        # step 9-11:Domain
        dom = attrs["domain"][-1].lstrip(".").lower() if attrs.get("domain") else ""
        if dom and self.reject_public_suffixes and dom in PUBLIC_SUFFIXES:
            if dom == host:
                dom = ""                              # step 5 前半:与请求主机同时视为未指定
            else:
                return None                           # step 5 后半:整条丢弃
        if dom:
            if not domain_match(host, dom):
                return None                           # step 6:请求主机不 domain-match
            c.host_only = False
            c.domain = dom
        else:
            c.host_only = True                        # step 7:省略(或无效)→ host-only cookie
            c.domain = host
        # step 12-13:Path
        if "path" in attrs and attrs["path"] and attrs["path"][-1].startswith("/") \
                and len(attrs["path"][-1]) <= MAX_ATTR:
            c.path = attrs["path"][-1]
        # step 14-16:Secure
        c.secure_only = "secure" in attrs
        if c.secure_only and not secure_conn:
            return None                                   # step 16:非安全源不得设 Secure cookie
        if "httponly" in attrs:
            c.http_only = True
            if not from_http_api:
                return None                               # step 18
        # step 19-20:SameSite
        if "samesite" in attrs:
            v = (attrs["samesite"][-1] or "").lower()
            c.same_site = {"none": "None", "strict": "Strict", "lax": "Lax"}.get(v, "Default")
        if c.same_site == "None" and not c.secure_only:
            return None                                   # step 22:None 必须配 Secure
        # step 21:非安全 cookie 不得覆盖同名 Secure cookie(路径比较**不对称**)
        if not c.secure_only and not secure_conn:
            for old in self.jar:
                if (old.name == c.name and old.secure_only
                        and (domain_match(c.domain, old.domain) or domain_match(old.domain, c.domain))
                        and path_match(c.path, old.path)):
                    return None                           # 防 cookie-fixing
        # step 23-24:前缀强制(UA 侧**大小写不敏感**)
        low = c.name.lower()
        if low.startswith("__secure-") and not c.secure_only:
            return None
        if low.startswith("__host-"):
            explicit_path = "path" in attrs and attrs["path"] and attrs["path"][-1] == "/"
            if not (c.secure_only and c.host_only and explicit_path):
                return None
        if not c.name and (value.lower().startswith("__secure-") or value.lower().startswith("__host-")):
            return None
        for old in list(self.jar):                        # step 25:同名同域同路径 → 替换
            if old.key() == (c.name, c.domain, c.host_only, c.path):
                c.creation = old.creation                 # 继承 creation-time
                self.jar.remove(old)
        self.jar.append(c)
        self.evict()
        if c.expired(self.now):
            if c in self.jar:
                self.jar.remove(c)                        # Max-Age=0/负值:立刻删掉
            return None
        return c

    def evict(self):
        self.jar = [x for x in self.jar if not x.expired(self.now)]     # step 26
        by_domain: dict[str, list[Cookie]] = {}
        for x in self.jar:
            by_domain.setdefault(x.domain, []).append(x)
        for dom, group in by_domain.items():                           # step 27:每域上限
            if len(group) > PER_DOMAIN:
                group.sort(key=lambda x: x.last_access)
                drop = {id(x) for x in group[: len(group) - PER_DOMAIN]}
                self.jar = [x for x in self.jar if id(x) not in drop]
        while len(self.jar) > TOTAL:                                   # step 28:总量上限
            oldest = min(self.jar, key=lambda x: x.last_access)
            self.jar.remove(oldest)

    # ---------------- 检索 ----------------
    def cookie_header(self, request_url: str, http_api=True, cross_site=False,
                      top_level_nav=False, safe_method=False) -> str:
        url = urlsplit(request_url)
        host = canonical_host(url.hostname or "")
        secure_conn = url.scheme == "https" or url.scheme == "wss"
        out = []
        for c in self.jar:
            if c.host_only:
                if host != c.domain:
                    continue
            elif not domain_match(host, c.domain):
                continue
            if not path_match(url.path or "/", c.path):
                continue
            if c.secure_only and not secure_conn:
                continue
            if c.http_only and not http_api:
                continue
            if not self.same_site_send(c, cross_site, top_level_nav, safe_method):
                continue
            c.last_access = self.now
            out.append(c)
        out.sort(key=lambda c: (-len(c.path), c.creation))   # §5.4 排序规则
        return "; ".join(f"{c.name}={c.value}" for c in out)

    def same_site_send(self, c: Cookie, cross_site, top_level_nav, safe_method) -> bool:
        mode = c.same_site
        if mode == "None":
            return True
        if mode == "Default":
            # §5.6.7.2:默认即 Lax;若 UA 启用 Lax-allowing-unsafe,则仅 2 分钟内创建者可放行
            if not cross_site:
                return True
            if top_level_nav and safe_method:
                return True
            return (self.lax_allowing_unsafe
                    and self.now - c.creation <= self.unsafe_window
                    and top_level_nav)
        if mode == "Strict":
            return not cross_site
        if mode == "Lax":                     # §5.6.7.1:仅顶层导航 + 安全方法
            return (not cross_site) or (top_level_nav and safe_method)
        return False

    def names(self) -> list[str]:
        return [c.name for c in self.jar]
