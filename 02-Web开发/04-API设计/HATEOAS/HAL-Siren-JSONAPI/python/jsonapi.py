"""JSON:API v1.1 文档结构与查询参数的最小可运行模型。

口径来源（实读 jsonapi.org/format/1.1/，Archived Copy of v1.1）：
  - 媒体类型 application/vnd.api+json；**只**允许 ext 与 profile 两个媒体类型参数，
    带别的参数 → 415；Accept 里若所有 JSON:API 实例都被别参数修饰 → 406。
  - 顶层 MUST 至少含 data / errors / meta 之一；data 与 errors MUST NOT 共存；
    没有顶层 data 时 included MUST NOT 出现。
  - 顶层 links 可含 self / related / describedby 与分页链接。
  - Resource Object：type MUST；id MUST（客户端新建待创建的资源除外，
    此时 MAY 用 lid 在文档内按 type 局部唯一标识）；type/id/lid 的值 MUST 是字符串。
  - 字段（attributes + relationships）与 type/id 共享命名空间。
  - Resource Linkage：空 to-one 为 null、空 to-many 为 []、非空 to-one 为单个
    resource identifier object、非空 to-many 为数组。
  - Compound Documents：included 是扁平数组；每个 included 资源 MUST 能从 primary data
    出发经关系链到达（full linkage），唯一例外是稀疏字段集把关系字段裁掉了。
  - include = 逗号分隔的关系路径，路径内用点分隔；不支持即 400。
  - fields[TYPE] = 逗号分隔字段名；空值表示不返回任何字段。
  - sort：逗号分隔；`-` 前缀为降序，其余 MUST 升序；按给出顺序依次应用。
  - 分页键固定为 first / last / prev / next；不可用则省略或置 null。
  - 成员名：首尾 MUST 是「全局允许字符」（a-z A-Z 0-9 与 U+0080 以上）；
    中间额外允许 - _ 空格；保留字符（+ , . [ ] ! " # $ % & ' ( ) * / : ; < = > ? @ \
    ^ ` { | } ~ DEL 与 C0 控制符）MUST NOT 出现（@ 仅可作首字符）。
  - 实现自定义查询参数：基名 MUST 是合法成员名且**至少含一个非 a-z 字符**，否则 400。

运行：python jsonapi.py
"""

import re

JSONAPI_MEDIA_TYPE = "application/vnd.api+json"

GLOBALLY_ALLOWED = re.compile(r"[A-Za-z0-9\u0080-\U0010FFFF]")
MIDDLE_ALLOWED = re.compile(r"[A-Za-z0-9\u0080-\U0010FFFF_\- ]")
RESERVED_CHARS = set("+,.[]!\"#$%&'()*/:;<=>?@\\^`{|}~\u007f") | {
    chr(c) for c in range(0x00, 0x20)
}

PAGINATION_KEYS = ("first", "last", "prev", "next")


def is_legal_member_name(name):
    """§Member Names：长度 ≥1、只用允许字符、首尾是全局允许字符。

    `@` 只允许出现在首位（@-Members）；本实现把单独的 "@" 判为非法
    （去掉 @ 后没有任何字符），规范对这一边界未作显式规定，此处按本 demo 口径处理。
    """
    if not isinstance(name, str) or not name:
        return False
    if "@" in name[1:]:
        return False
    body = name[1:] if name[0] == "@" else name
    if not body:
        return False
    if not GLOBALLY_ALLOWED.match(body[0]) or not GLOBALLY_ALLOWED.match(body[-1]):
        return False
    for ch in body[1:-1]:
        if not MIDDLE_ALLOWED.match(ch):
            return False
    for ch in name:
        if ch in RESERVED_CHARS and ch != "@":
            return False
    return True


def media_type_params(content_type):
    """拆出 (媒体类型, 参数名列表)。"""
    if content_type is None:
        return None, []
    parts = [p.strip() for p in content_type.split(";")]
    base = parts[0].lower()
    names = []
    for p in parts[1:]:
        if "=" in p:
            names.append(p.split("=", 1)[0].strip().lower())
        elif p:
            names.append(p.lower())
    return base, names


def negotiate_content_type(content_type):
    """§Client/Server Responsibilities：返回 (决策, 状态码)。"""
    base, names = media_type_params(content_type)
    if base != JSONAPI_MEDIA_TYPE:
        return "not-jsonapi", 200
    bad = [n for n in names if n not in ("ext", "profile")]
    if bad:
        return "unsupported-param", 415
    return "ok", 200


def validate_document(doc, errors=None, path="$"):
    """校验一份 JSON:API 文档，返回错误列表。"""
    if errors is None:
        errors = []
    if not isinstance(doc, dict):
        errors.append("%s: 顶层 MUST 是 JSON 对象" % path)
        return errors

    has_data = "data" in doc
    has_errors = "errors" in doc
    has_meta = "meta" in doc
    # 规范还允许「已应用扩展定义的成员」，但那需要知道扩展后才能判定，
    # 本实现只覆盖 data / errors / meta 三条可静态判定的路径。
    if not (has_data or has_errors or has_meta):
        errors.append("%s: 顶层 MUST 至少含 data / errors / meta 之一" % path)
    if has_data and has_errors:
        errors.append("%s: data 与 errors MUST NOT 共存" % path)
    if "included" in doc and not has_data:
        errors.append("%s: 无顶层 data 时 included MUST NOT 出现" % path)

    if "meta" in doc and not isinstance(doc["meta"], dict):
        errors.append("%s.meta: MUST 是对象" % path)
    if "jsonapi" in doc and not isinstance(doc["jsonapi"], dict):
        errors.append("%s.jsonapi: MUST 是对象" % path)
    if has_errors:
        _validate_errors(doc["errors"], errors, path + ".errors")

    if has_data:
        data = doc["data"]
        if isinstance(data, list):
            for i, res in enumerate(data):
                _validate_resource(res, errors, "%s.data[%d]" % (path, i))
        elif data is not None:
            _validate_resource(data, errors, path + ".data")

    if "included" in doc:
        if not isinstance(doc["included"], list):
            errors.append("%s.included: MUST 是数组" % path)
        else:
            for i, res in enumerate(doc["included"]):
                _validate_resource(res, errors, "%s.included[%d]" % (path, i))
    return errors


def _validate_resource(res, errors, where):
    if not isinstance(res, dict):
        errors.append("%s: 资源必须是对象" % where)
        return
    fail_missing_type = "type" not in res
    fail_missing_id = "id" not in res and "lid" not in res
    if fail_missing_type:
        errors.append("%s: 缺 REQUIRED 的 type" % where)
    if fail_missing_id:
        errors.append("%s: 缺 id（客户端新建资源须用 lid）" % where)
    for member in ("type", "id", "lid"):
        if member in res and not isinstance(res[member], str):
            errors.append("%s.%s: MUST 是字符串" % (where, member))
    rtype = res.get("type")
    if isinstance(rtype, str) and not is_legal_member_name(rtype):
        errors.append("%s.type: 值 MUST 满足成员名约束" % where)

    attrs = res.get("attributes")
    rels = res.get("relationships")
    if attrs is not None and not isinstance(attrs, dict):
        errors.append("%s.attributes: MUST 是对象" % where)
    if rels is not None and not isinstance(rels, dict):
        errors.append("%s.relationships: MUST 是对象" % where)
    # 字段与 type/id 共享命名空间
    for name in set(list(attrs or {}) ) & set(list(rels or {})):
        errors.append("%s: 字段 %r 同时是 attribute 与 relationship" % (where, name))
    for name in (list(attrs or {}) + list(rels or {})):
        if name in ("type", "id"):
            errors.append("%s: 字段不得名为 %r" % (where, name))
    for name, rel in (rels or {}).items():
        _validate_relationship(rel, errors, "%s.relationships.%s" % (where, name))


def _validate_relationship(rel, errors, where):
    if not isinstance(rel, dict):
        errors.append("%s: relationship object 必须是对象" % where)
        return
    has = [k for k in ("links", "data", "meta") if k in rel]
    if not has:
        errors.append("%s: MUST 至少含 links / data / meta 之一" % where)
    if "data" in rel:
        data = rel["data"]
        if data is None:
            linkage = "empty-to-one"
        elif isinstance(data, dict):
            linkage = "to-one"
            _validate_identifier(data, errors, where + ".data")
        elif isinstance(data, list):
            linkage = "to-many"
            for i, ident in enumerate(data):
                _validate_identifier(ident, errors, "%s.data[%d]" % (where, i))
        else:
            errors.append("%s.data: linkage 形状非法" % where)
            linkage = "invalid"
        rel["_linkage_shape"] = linkage


def _validate_identifier(ident, errors, where):
    if not isinstance(ident, dict):
        errors.append("%s: resource identifier object 必须是对象" % where)
        return
    if "type" not in ident:
        errors.append("%s: resource identifier object MUST 含 type" % where)
    if "id" not in ident and "lid" not in ident:
        errors.append("%s: 缺 id（新建资源须用 lid）" % where)
    for member in ("type", "id", "lid"):
        if member in ident and not isinstance(ident[member], str):
            errors.append("%s.%s: MUST 是字符串" % (where, member))


def _validate_errors(errors_array, errors, where):
    if not isinstance(errors_array, list):
        errors.append("%s: errors MUST 是数组" % where)
        return
    for i, item in enumerate(errors_array):
        if not isinstance(item, dict):
            errors.append("%s[%d]: error object 必须是对象" % (where, i))
            continue
        if not any(k in item for k in ("id", "links", "status", "code", "title", "detail")):
            errors.append("%s[%d]: MUST 至少含一个成员" % (where, i))
        if "status" in item and not isinstance(item["status"], str):
            errors.append("%s[%d].status: MUST 是字符串" % (where, i))
        if "code" in item and not isinstance(item["code"], str):
            errors.append("%s[%d].code: MUST 是字符串" % (where, i))
        source = item.get("source")
        if source is not None:
            if not isinstance(source, dict):
                errors.append("%s[%d].source: MUST 是对象" % (where, i))
            elif not any(k in source for k in ("pointer", "parameter", "header")):
                errors.append("%s[%d].source: SHOULD 含 pointer/parameter/header 之一" % (where, i))
