"""Siren（application/vnd.siren+json）的最小可运行模型。

口径来源（实读 GitHub kevinswiber/siren README.md，master 分支）：
  - 媒体类型 `application/vnd.siren+json`；Entity 是有属性与动作的 URI 可寻址资源。
  - 根实体与「嵌入表示」型子实体 SHOULD 含至少一条 rel 为 `self` 的 links；
    「嵌入链接」型子实体 MUST 含 `href`。
  - `class` MUST 是字符串数组（Optional）；`properties` 是对象（Optional）。
  - `entities` 数组：子实体含 `href` 即嵌入链接；不含 `href` 即嵌入表示，
    此时 MUST 含 `rel` 描述与父实体的关系。
  - `links`：`rel` MUST 是字符串数组且 Required；`href` Required；
    `class` / `title` / `type` Optional。
  - `actions`：`name` Required 且在同一实体的动作集内 MUST 唯一（违反时客户端行为未定义）；
    `href` Required；`method` 省略时按 GET 处理；`type` 省略且存在 `fields`
    时默认 `application/x-www-form-urlencoded`；`fields` 内 `name` Required 且唯一，
    `type` 省略时默认 `text`。
  - 字段 `type` 的取值域是 HTML5 input type 集合
    （hidden/text/search/tel/url/email/password/datetime/date/month/week/time/
     datetime-local/number/range/color/checkbox/radio/file，共 19 种）。
  - Classes 与 Relations 的分工：rel 描述两资源间关系，class 描述元素自身分类。

运行：python siren.py
"""

SIREN_MEDIA_TYPE = "application/vnd.siren+json"

FIELD_INPUT_TYPES = (
    "hidden", "text", "search", "tel", "url", "email", "password",
    "datetime", "date", "month", "week", "time", "datetime-local",
    "number", "range", "color", "checkbox", "radio", "file",
)

DEFAULT_METHOD = "GET"
DEFAULT_ACTION_TYPE = "application/x-www-form-urlencoded"
DEFAULT_FIELD_TYPE = "text"


def _is_str_list(value, allow_empty):
    if not isinstance(value, list):
        return False
    if not value:
        return allow_empty
    return all(isinstance(v, str) for v in value)


def validate_entity(entity, errors=None, path="$", require_rel=False):
    """校验一个 Siren Entity，返回错误列表。子实体走 require_rel=True。"""
    if errors is None:
        errors = []
    if not isinstance(entity, dict):
        errors.append("%s: Entity 必须是对象" % path)
        return errors

    if "class" in entity and not _is_str_list(entity["class"], True):
        errors.append("%s.class: MUST 是字符串数组" % path)
    if "properties" in entity and not isinstance(entity["properties"], dict):
        errors.append("%s.properties: 必须是对象" % path)
    if "title" in entity and not isinstance(entity["title"], str):
        errors.append("%s.title: 必须是字符串" % path)

    if require_rel:
        rel = entity.get("rel")
        if not _is_str_list(rel, False):
            errors.append("%s.rel: 子实体 MUST 含非空字符串数组 rel" % path)

    for i, link in enumerate(entity.get("links") or []):
        _validate_link(link, "%s.links[%d]" % (path, i), errors)
    for i, sub in enumerate(entity.get("entities") or []):
        validate_entity(sub, errors, "%s.entities[%d]" % (path, i), require_rel=True)
    _validate_actions(entity, errors, path)
    return errors


def _validate_link(link, where, errors):
    if not isinstance(link, dict):
        errors.append("%s: Link 必须是对象" % where)
        return
    if not _is_str_list(link.get("rel"), False):
        errors.append("%s.rel: MUST 是非空字符串数组（Required）" % where)
    if not isinstance(link.get("href"), str):
        errors.append("%s.href: 必须是字符串（Required）" % where)
    if "class" in link and not _is_str_list(link["class"], True):
        errors.append("%s.class: MUST 是字符串数组" % where)
    if "title" in link and not isinstance(link["title"], str):
        errors.append("%s.title: 必须是字符串" % where)
    if "type" in link and not isinstance(link["type"], str):
        errors.append("%s.type: 必须是字符串" % where)


def _validate_actions(entity, errors, path):
    actions = entity.get("actions")
    if actions is None:
        return
    if not isinstance(actions, list):
        errors.append("%s.actions: 必须是数组" % path)
        return
    seen = set()
    for i, action in enumerate(actions):
        where = "%s.actions[%d]" % (path, i)
        if not isinstance(action, dict):
            errors.append("%s: Action 必须是对象" % where)
            continue
        name = action.get("name")
        if not isinstance(name, str):
            errors.append("%s.name: 必须是字符串（Required）" % where)
        else:
            if name in seen:
                errors.append("%s.name: 动作名 MUST 在实体内唯一" % where)
            seen.add(name)
        if not isinstance(action.get("href"), str):
            errors.append("%s.href: 必须是字符串（Required）" % where)
        method = action.get("method", DEFAULT_METHOD)
        if not isinstance(method, str):
            errors.append("%s.method: 必须是字符串" % where)
        _validate_fields(action, errors, where)


def _validate_fields(action, errors, path):
    fields = action.get("fields")
    if fields is None:
        return
    if not isinstance(fields, list):
        errors.append("%s.fields: 必须是数组" % path)
        return
    seen = set()
    for i, field in enumerate(fields):
        where = "%s.fields[%d]" % (path, i)
        if not isinstance(field, dict):
            errors.append("%s: Field 必须是对象" % where)
            continue
        name = field.get("name")
        if not isinstance(name, str):
            errors.append("%s.name: 必须是字符串（Required）" % where)
        else:
            if name in seen:
                errors.append("%s.name: 字段名 MUST 在动作内唯一" % where)
            seen.add(name)
        ftype = field.get("type", DEFAULT_FIELD_TYPE)
        if ftype not in FIELD_INPUT_TYPES:
            errors.append("%s.type: %r 不在 HTML5 input type 集合内" % (where, ftype))


def is_embedded_link(sub):
    """子实体含 href 即嵌入链接，否则是嵌入表示。"""
    return isinstance(sub, dict) and "href" in sub


def partition_entities(entity):
    """把 entities 分成 (嵌入链接列表, 嵌入表示列表)。"""
    links, reps = [], []
    for sub in entity.get("entities") or []:
        (links if is_embedded_link(sub) else reps).append(sub)
    return links, reps


def action_by_name(entity, name):
    for action in entity.get("actions") or []:
        if action.get("name") == name:
            return action
    return None


def field_defaults(action):
    """字段默认值：有 value 用 value，hidden 型也照此参与序列化。"""
    out = {}
    for field in action.get("fields") or []:
        if isinstance(field, dict) and "value" in field:
            out[field.get("name")] = field.get("value")
    return out


def to_form_body(action, values=None):
    """按 action.type 序列化请求体。

    只实现规范里给了默认值的 application/x-www-form-urlencoded：
    field 的 value 与调用方传入的 values 合并（后者覆盖前者），
    hidden 字段同样出现在请求体里。
    """
    import urllib.parse

    payload = dict(field_defaults(action))
    if values:
        payload.update(values)
    pairs = []
    for field in action.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        if name in payload:
            pairs.append((name, str(payload[name])))
    for key, value in (values or {}).items():
        if key not in [f.get("name") for f in action.get("fields") or [] if isinstance(f, dict)]:
            pairs.append((key, str(value)))
    return urllib.parse.urlencode(pairs)


def action_request(action, values=None):
    """产出 (method, href, content_type, body)。"""
    method = action.get("method", DEFAULT_METHOD).upper()
    href = action.get("href")
    has_fields = bool(action.get("fields"))
    ctype = action.get("type")
    if ctype is None and has_fields:
        ctype = DEFAULT_ACTION_TYPE
    body = ""
    if ctype == DEFAULT_ACTION_TYPE:
        body = to_form_body(action, values)
    return method, href, ctype, body
