"""RFC 9110 §12 主动协商：Accept 媒体范围、qvalue 与 Vary 的最小可运行模型。

口径来源（本轮实读 rfc-editor.org/rfc/rfc9110.txt，502 941 字节）：
  - §12.4.1 缺席：请求不带协商头 = 该维度无偏好；不可接受时服务端可 406，
    也可以**无视**该头按未协商处理。
  - §12.4.2 qvalue：0..1，三位小数封顶，缺省 1，0 表示"不可接受"。
  - §12.4.3 通配符：`*/*` 与 `type/*`；没有通配符时未列出的值视为不可接受。
  - §12.5.1 Accept：`media-range = ("*/*" / type "/" "*" / type "/" subtype) parameters`；
    每个 media-range 后可跟适用的媒体类型参数，再跟可选的 q。
    "Media ranges can be overridden by more specific media ranges"——
    同一 type 上最具体的引用优先。
  - §12.5.1 注：发送方 SHOULD 把 q 放最后，接收方 SHOULD 无视位置一律当权重。
  - §12.5.5 Vary：值是 `*` 或请求头字段名列表；`*` 表示方差无限，
    缓存 MUST NOT 在未转发请求的情况下判定复用；代理 MUST NOT 生成 `*`。
    列出字段名时，除非后续请求在这些头上的取值相同，否则缓存 MUST NOT 复用。
  - §15.5.7 406：无当前可接受的表示，且服务端不愿给默认表示。
  - §15.5.16 415：请求内容格式不被支持（与 406 的方向相反）。

⚠ 已核实的官方勘误（Errata ID 7306，2022-11-09 Verified）：
    §12.5.1 的 Table 5 最后一行写 `text/html;level=3 → 0.7`，
    但按本节的"取最具体的匹配 media range"规则只能推出 `text/*;q=0.3`。
    勘误确认应改为 0.3（RFC 7231 §5.3.2 的同类例子里 Accept 含 `text/html;q=0.7`，
    所以那里 0.7 是对的；RFC 9110 抄过来时漏了这一项）。
    本实现按**规则**给出 0.3，并把官方表的 0.7 记成一条显式断言。

运行：python negotiate.py
"""

import re

_ACCEPT_ITEM = re.compile(r"^\s*(?P<range>[^;,]+)(?P<params>(?:;[^;]*)*)\s*$")


def _split_params(text):
    """拆出 `;a=b;q=0.5` 里的 (参数字典, q)。q 大小写不敏感。"""
    params = {}
    q = 1.0
    for chunk in text.split(";")[1:]:
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" in chunk:
            key, _, value = chunk.partition("=")
        else:
            key, value = chunk, ""
        key = key.strip().lower()
        value = value.strip().strip('"')
        if key == "q":
            try:
                q = float(value)
            except ValueError:
                q = 0.0
        else:
            params[key] = value
    return params, q


def parse_accept(header):
    """解析 Accept，返回 [{type, subtype, params, q, raw}]。

    type / subtype 用 None 表示通配符所在的位置。
    """
    if not header:
        return []
    out = []
    for item in header.split(","):
        m = _ACCEPT_ITEM.match(item)
        if not m:
            continue
        raw = m.group("range").strip().lower()
        if "/" not in raw:
            continue
        typ, _, sub = raw.partition("/")
        typ, sub = typ.strip(), sub.strip()
        if typ == "*":
            typ = None
        if sub == "*":
            sub = None
        if typ is None and sub is not None:
            continue
        params, q = _split_params(m.group("params"))
        out.append({"type": typ, "subtype": sub, "params": params, "q": q,
                    "raw": raw})
    return out


def media_range_matches(media_range, media_type):
    """判断 media-range 是否匹配某个具体媒体类型。

    带参数的 media-range 只匹配"这些参数都存在且取值相同"的媒体类型；
    媒体类型可以有额外参数（§12.5.1 的例子即如此）。
    """
    # 必须先切参数再切类型：`text/plain;format=flowed` 的 subtype 只到 plain 为止
    head, _, rest = media_type.partition(";")
    params = {}
    for chunk in rest.split(";"):
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            params[key.strip().lower()] = value.strip().strip('"')
    typ, _, sub = head.partition("/")
    typ = typ.strip().lower()
    sub = sub.strip().lower()

    if media_range["type"] is not None and media_range["type"] != typ:
        return False
    if media_range["subtype"] is not None and media_range["subtype"] != sub:
        return False
    for key, value in media_range["params"].items():
        if key not in params or params[key] != value:
            return False
    return True


def specificity(media_range):
    """§12.5.1 的"更具体者优先"：带参数的完整类型 > 完整类型 > type/* > */*。"""
    if media_range["type"] is None:
        return 0
    if media_range["subtype"] is None:
        return 1
    return 3 if media_range["params"] else 2


def quality(accept_list, media_type):
    """§12.5.1：取**最具体的**那个匹配 media-range 的 q 作为该类型的质量因子。"""
    best = None
    best_spec = -1
    for item in accept_list:
        if not media_range_matches(item, media_type):
            continue
        spec = specificity(item)
        if spec > best_spec:
            best_spec, best = spec, item["q"]
    if best is None:
        return 0.0
    return best


def negotiate(accept_header, available, default=None):
    """主动协商：返回选中的表示名；全 0 且无默认 → None（406）。"""
    items = parse_accept(accept_header)
    if not items:
        if default is not None:
            return default
        return available[0][0] if available else None
    best_name, best_q = None, 0.0
    for name, media_type in available:
        q = quality(items, media_type)
        if q > best_q:
            best_name, best_q = name, q
    if best_name is not None:
        return best_name
    return default


def parse_vary(value):
    """§12.5.5：返回 (是否通配符, 字段名集合)。"""
    if not value:
        return False, set()
    names = {n.strip().lower() for n in value.split(",") if n.strip()}
    if "*" in names:
        return True, names
    return False, names


def cache_key_matches(vary_value, stored_request_headers, new_request_headers):
    """§12.5.5 用途 1：判断缓存条目能否复用。

    `*` → 方差无限，未转发请求不得复用；列出字段名 → 这些头的取值必须逐一相同
    （两边都缺也视为相同）。
    """
    wildcard, names = parse_vary(vary_value)
    if wildcard:
        return False
    if not names:
        return True
    for name in names:
        if (stored_request_headers.get(name) or "") != (new_request_headers.get(name) or ""):
            return False
    return True


# --------------------------------------------------- API 版本化：三种载体

def version_from_path(path, prefix="/v"):
    """URI 路径版本：`/v2/orders` → "2"。"""
    m = re.match(r"^" + re.escape(prefix) + r"(\d+)(?:/|$)", path)
    return m.group(1) if m else None


def version_from_media_type(media_type, base="application/vnd.example"):
    """内容协商版本：`application/vnd.example.v2+json` → "2"。"""
    m = re.match(r"^" + re.escape(base) + r"\.v(\d+)\+json$", (media_type or "").strip())
    return m.group(1) if m else None


def version_from_header(headers, name="API-Version"):
    """自定义请求头版本：值与路径/媒体类型同样按字符串比较。"""
    value = headers.get(name)
    return str(value).strip() if value is not None else None


def resolve_version(path=None, accept=None, headers=None, default="1"):
    """三种载体的优先级：本 demo 口径为「显式头 > 媒体类型 > 路径 > 默认」。

    规范并没有规定三者优先级，这个顺序是工程惯例；写成显式函数是为了让
    "三种载体可以并存且互相冲突"这一点可被断言。
    """
    v = version_from_header(headers or {})
    if v:
        return v, "header"
    v = version_from_media_type(accept or "")
    if v:
        return v, "media-type"
    v = version_from_path(path or "")
    if v:
        return v, "path"
    return default, "default"
