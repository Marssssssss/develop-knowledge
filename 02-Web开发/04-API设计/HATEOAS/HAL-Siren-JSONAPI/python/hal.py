"""HAL（JSON Hypertext Application Language）的最小可运行模型。

口径来源（实读 ietf.org/archive/id/draft-kelly-json-hal-08.txt）：
  - §3：媒体类型 application/hal+json，根对象 MUST 是 Resource Object。
  - §4：Resource Object 只有两个保留属性 `_links` 与 `_embedded`，
    其余属性表示资源当前状态；两个保留属性都是 OPTIONAL。
  - §4.1.1：`_links` 是对象，属性名是 link relation type（RFC 5988），
    值是 Link Object **或** Link Object 数组。
  - §4.1.2：`_embedded` 同理，值是 Resource Object 或 Resource Object 数组；
    嵌入资源 MAY 是目标 URI 上表示的 full / partial / inconsistent 版本。
  - §5.1：`href` REQUIRED；值是 URI 或 URI Template。
  - §5.2：`templated` 是布尔；href 是 URI Template 时 SHOULD 为 true；
    未定义或非 true 的取值 SHOULD 被视为 false。
  - §5.3-§5.8：type / deprecation / name / profile / title / hreflang 均为 OPTIONAL。
  - §8.1：每个 Resource Object SHOULD 含 `self` link。
  - §8.2：CURIE 通过根资源上 rel 为 `curies` 的一组 Link Object 建立，
    含带 `rel` token 的 URI Template，并用 `name` 命名。
  - §8.3：hypertext cache pattern —— 客户端 MAY 优先读同 rel 的嵌入资源而不去遍历链接；
    服务端 SHOULD NOT 把链接整体换成嵌入（客户端支持是 OPTIONAL）。

运行：python hal.py
"""

import re

HAL_MEDIA_TYPE = "application/hal+json"
RESERVED_PROPERTIES = ("_links", "_embedded")
LINK_PROPERTIES = (
    "href",
    "templated",
    "type",
    "deprecation",
    "name",
    "profile",
    "title",
    "hreflang",
)

_URI_TEMPLATE = re.compile(r"\{[^{}]+\}")


def is_uri_template(href):
    """§5.1：href 既可以是 URI，也可以是 URI Template。"""
    return bool(_URI_TEMPLATE.search(href))


def as_link_list(value):
    """§4.1.1：把 `_links` 的成员值归一成 Link Object 列表。

    单对象与数组两种写法合法；其余形状一律视为非法（返回空列表）。
    """
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def as_resource_list(value):
    """§4.1.2：`_embedded` 的成员值同理归一成 Resource Object 列表。"""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def validate_resource(doc, path="$", errors=None):
    """校验一个 Resource Object，返回错误字符串列表（空列表即合法）。"""
    if errors is None:
        errors = []
    if not isinstance(doc, dict):
        errors.append("%s: 根/资源必须是 JSON 对象" % path)
        return errors

    links = doc.get("_links")
    if links is not None:
        if not isinstance(links, dict):
            errors.append("%s._links: 必须是对象" % path)
        else:
            for rel, value in links.items():
                where = "%s._links.%s" % (path, rel)
                if isinstance(value, dict):
                    objs = [value]
                elif isinstance(value, list):
                    objs = list(value)
                else:
                    errors.append("%s: 必须是 Link Object 或 Link Object 数组" % where)
                    continue
                for i, obj in enumerate(objs):
                    _validate_link(obj, "%s[%d]" % (where, i), errors)

    embedded = doc.get("_embedded")
    if embedded is not None:
        if not isinstance(embedded, dict):
            errors.append("%s._embedded: 必须是对象" % path)
        else:
            for rel, value in embedded.items():
                where = "%s._embedded.%s" % (path, rel)
                if isinstance(value, (dict, list)):
                    for i, res in enumerate(as_resource_list(value)):
                        validate_resource(res, "%s[%d]" % (where, i), errors)
                else:
                    errors.append("%s: 必须是 Resource Object 或数组" % where)
    return errors


def _validate_link(obj, where, errors):
    if not isinstance(obj, dict):
        errors.append("%s: Link Object 必须是对象" % where)
        return
    if "href" not in obj:
        errors.append("%s: 缺 REQUIRED 的 href" % where)
    elif not isinstance(obj["href"], str):
        errors.append("%s.href: 必须是字符串" % where)
    else:
        # §5.2 的 SHOULD：模板却没标 templated —— 记录为提示而非硬错误
        if is_uri_template(obj["href"]) and obj.get("templated") is not True:
            errors.append("%s: href 是 URI Template 但 templated 不为 true" % where)
    if "templated" in obj and not isinstance(obj["templated"], bool):
        errors.append("%s.templated: 必须是布尔" % where)
    for prop in ("type", "deprecation", "name", "profile", "title", "hreflang"):
        if prop in obj and not isinstance(obj[prop], str):
            errors.append("%s.%s: 必须是字符串" % (where, prop))


def is_templated(link):
    """§5.2：非 true 的一切取值 SHOULD 被视为 false。"""
    return link.get("templated") is True


def curies(doc):
    """§8.2：返回根资源上声明的 CURIE 映射 {name: URI Template}。"""
    out = {}
    for link in as_link_list((doc.get("_links") or {}).get("curies")):
        name = link.get("name")
        href = link.get("href")
        if isinstance(name, str) and isinstance(href, str):
            out[name] = href
    return out


def expand_rel(doc, rel):
    """把 `acme:widgets` 形式的短关系名展开成完整 URI。

    原样返回未带已声明前缀的关系名（IANA 注册名与绝对 URI 都不动）。
    """
    table = curies(doc)
    if ":" not in rel:
        return rel
    prefix, _, rest = rel.partition(":")
    if prefix in table:
        return table[prefix].replace("{rel}", rest)
    return rel


def links_for(doc, rel):
    """按 link relation type 取一组 Link Object（支持 CURIE 前缀）。"""
    container = doc.get("_links") or {}
    for key, value in container.items():
        if key == rel or expand_rel(doc, key) == rel:
            return as_link_list(value)
    return []


def link_by_name(doc, rel, name):
    """§5.5：`name` MAY 作为同一 rel 下多个 Link Object 的次级键。"""
    for link in links_for(doc, rel):
        if link.get("name") == name:
            return link
    return None


def embedded_for(doc, rel):
    """取同 rel 的嵌入资源（第一个）。"""
    container = doc.get("_embedded") or {}
    for key, value in container.items():
        if key == rel or expand_rel(doc, key) == rel:
            items = as_resource_list(value)
            if items:
                return items[0]
    return None


def traverse(doc, rel, fetch=None, name=None):
    """§8.3 hypertext cache pattern。

    同 rel 的嵌入资源（若存在）优先，避免一次真实请求；否则走链接。
    返回 (来源, 资源或 href, 请求计数)。`fetch` 是「跟链接」的注入点，
    用于统计本模式省下的往返次数。
    """
    requests = 0
    embedded = embedded_for(doc, rel)
    if embedded is not None:
        return "embedded", embedded, 0
    if name is not None:
        link = link_by_name(doc, rel, name)
        candidates = [link] if link else []
    else:
        candidates = links_for(doc, rel)
    for link in candidates:
        if is_templated(link) or is_uri_template(link.get("href", "")):
            continue
        if fetch is None:
            return "link", link.get("href"), 1
        return "link", fetch(link.get("href")), 1
    return "none", None, requests


def state_properties(doc):
    """§4：除两个保留属性外的一切属性即资源状态。"""
    return {k: v for k, v in doc.items() if k not in RESERVED_PROPERTIES}


def expand_template(href, variables):
    """对 §6 示例里那种 `{?id}` 形式做最小展开（只支持 {?a,b} 与 {a}）。"""
    def _sub(match):
        expr = match.group(0)[1:-1]
        query = expr.startswith("?")
        names = [n for n in expr.lstrip("?").split(",") if n in variables]
        if not names:
            return ""
        if query:
            return "?" + "&".join("%s=%s" % (n, variables[n]) for n in names)
        return ",".join(str(variables[n]) for n in names)

    return _URI_TEMPLATE.sub(_sub, href)
