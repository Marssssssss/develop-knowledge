"""Fetch Metadata 请求头最小模型。

依据 W3C《Fetch Metadata Request Headers》
（https://w3c.github.io/webappsec-fetch-metadata/ ，140594 B 全文实读）：

§2 四个头都是 RFC 9651 Structured Field：
    Sec-Fetch-Dest = sf-token   （request destination）
    Sec-Fetch-Mode = sf-token   （cors/navigate/no-cors/same-origin/websocket）
    Sec-Fetch-Site = sf-token   （cross-site/same-origin/same-site/none）
    Sec-Fetch-User = sf-boolean （仅在导航请求且为 true 时发送）

§2.3 设置 Sec-Fetch-Site 的算法：
    1. header 初值 = same-origin
    2. 若 r 是「由用户交互显式触发」的导航请求 → none
    3. 若 header 不是 none，则遍历 r 的 url list，对每个 url：
       a. url 与 r 的 origin 同源 → continue
       b. header = cross-site
       c. 若 r 的 origin 与 url 的 origin 不同站 → break
       d. header = same-site

§2.4 设置 Sec-Fetch-User：非导航请求或 user-activation 为假 → 不发这个头。

§3 追加元数据前先判「potentially trustworthy URL」，不满足则一个头都不发。

§4.1 重定向：算法走完整个 url list —— 任一 url 跨站即 cross-site，
    只有全部同站才 same-site，只有全部同源才 same-origin。
    规范给的例子：即便最后重定向回 example.com，只要链上出现过
    example.net，最终仍是 cross-site。

§4.2 每个头都带 Sec- 前缀 → forbidden response-header name → JS 无法伪造。
"""

# 规范 §2 示例里明确给出的五个 destination（完整集合取自 Fetch 的 request
# destination，这里只收录实读到的，故集合视为开放集）
KNOWN_DEST = {"empty", "image", "worker", "document", "iframe"}
VALID_MODE = {"cors", "navigate", "no-cors", "same-origin", "websocket"}
VALID_SITE = {"cross-site", "same-origin", "same-site", "none"}

# 简化 Public Suffix List：只收录本 demo 用到的后缀
PUBLIC_SUFFIXES = {"com", "net", "org", "co.uk"}

TRUSTWORTHY_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


def registrable_domain(host):
    """eTLD+1。先试更长的后缀，避免把 co.uk 当成 registrable domain。"""
    labels = host.lower().rstrip(".").split(".")
    for n in (3, 2, 1):
        if len(labels) > n and ".".join(labels[-n:]) in PUBLIC_SUFFIXES:
            return ".".join(labels[-(n + 1):])
    if len(labels) >= 2:
        return ".".join(labels[-2:])
    return host.lower()


class Origin:
    def __init__(self, scheme, host, port=None):
        self.scheme = scheme.lower()
        self.host = host.lower()
        self.port = port

    def default_port(self):
        return {"http": 80, "https": 443, "ws": 80, "wss": 443}.get(self.scheme)

    def effective_port(self):
        return self.port if self.port is not None else self.default_port()

    def same_origin(self, other):
        return (self.scheme == other.scheme and self.host == other.host
                and self.effective_port() == other.effective_port())

    def same_site(self, other):
        """URL 的「schemelessly same site」：registrable domain 相同。

        规范完整的 "same site" 还要求 scheme 相容，本 demo 只取域这一半，
        并在 README 标注口径。
        """
        return registrable_domain(self.host) == registrable_domain(other.host)

    def __repr__(self):
        return "%s://%s" % (self.scheme, self.host)


class Req:
    """一个请求。url_list 是重定向链上的全部 URL（按访问顺序）。"""

    def __init__(self, origin, url_list=None, dest="empty", mode="no-cors",
                 navigation=False, user_activation=False, user_initiated=False,
                 trustworthy=True):
        self.origin = origin
        self.url_list = list(url_list or [])
        self.dest = dest
        self.mode = mode
        self.navigation = navigation
        self.user_activation = user_activation
        self.user_initiated = user_initiated
        self.trustworthy = trustworthy


def set_sec_fetch_site(r):
    """§2.3。返回 (值, 实际检查过的 url 个数)。"""
    value = "same-origin"
    examined = 0
    if r.navigation and r.user_initiated:
        return ("none", 0)
    for url in r.url_list:
        examined += 1
        uo = url if isinstance(url, Origin) else Origin(url.scheme, url.host)
        if r.origin.same_origin(uo):
            continue
        value = "cross-site"
        if not r.origin.same_site(uo):
            break
        value = "same-site"
    return (value, examined)


def set_sec_fetch_user(r):
    """§2.4。返回 '?1' 或 None（None 表示这个头根本不出现）。"""
    if not r.navigation or not r.user_activation:
        return None
    return "?1"


def append_fetch_metadata(r):
    """§3。返回最终发出的 Sec-Fetch-* 头字典。"""
    if not r.trustworthy:
        return {}
    site, _ = set_sec_fetch_site(r)
    headers = {
        "Sec-Fetch-Dest": r.dest,
        "Sec-Fetch-Mode": r.mode,
        "Sec-Fetch-Site": site,
    }
    user = set_sec_fetch_user(r)
    if user is not None:
        headers["Sec-Fetch-User"] = user
    return headers


def normalize_incoming(headers):
    """服务端侧规范化。

    §2.2/§2.3 明确要求：Mode 与 Site 出现**非法值**时服务端 SHOULD 忽略该头，
    以便前向兼容未知请求类型。规范对 Dest 没有这句要求，故 Dest 原样透传。
    """
    out = {}
    if "Sec-Fetch-Dest" in headers:
        out["dest"] = headers["Sec-Fetch-Dest"]
    mode = headers.get("Sec-Fetch-Mode")
    if mode is not None and mode in VALID_MODE:
        out["mode"] = mode
    site = headers.get("Sec-Fetch-Site")
    if site is not None and site in VALID_SITE:
        out["site"] = site
    return out


def isolation_policy(headers):
    """资源隔离策略示例 —— **工程惯例，不是规范条文**（README 已标注）。

    无元数据时 fail-open（老浏览器），否则按 mode/dest/site 分流。
    """
    n = normalize_incoming(headers)
    if not n or "site" not in n:
        return "allow"                       # 老浏览器：放行以免误伤
    site, mode, dest = n.get("site"), n.get("mode"), n.get("dest")
    if mode == "navigate" or dest == "document":
        return "allow" if site in ("same-origin", "same-site", "none") else "block"
    if site in ("same-origin", "same-site"):
        return "allow"
    return "block"


def is_forbidden_response_header_name(name):
    """§4.2：Sec- 前缀使这些头无法被 JS 改写。"""
    low = name.lower()
    if low.startswith("sec-"):
        return True
    return low in ("set-cookie", "set-cookie2")


def js_set_header(headers, name, value):
    """模拟 JS 侧设置请求头。返回是否成功。"""
    if is_forbidden_response_header_name(name):
        return False
    headers[name] = value
    return True
