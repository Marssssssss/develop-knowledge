"""JSON:API v1.1 查询参数族的最小可运行模型（文档结构见 jsonapi.py）。

口径来源（实读 jsonapi.org/format/1.1/）：
  - §Query Parameter Families：参数族 = 基名 + 若干 `[]` / `[合法成员名]` /
    `[点分隔合法成员名列表]`；`filter[_]` 不合法，因为 `_` 不是合法成员名。
  - §Inclusion：include 值是逗号分隔的关系路径，路径内点分隔；空值表示不返回相关资源；
    不支持该参数 MUST 400。
  - §Sparse Fieldsets：fields[TYPE] 值是逗号分隔字段名，空值表示不返回任何字段。
  - §Sorting：sort 值是逗号分隔排序字段；`-` 前缀为降序，其余 MUST 升序；
    多个字段 SHOULD 按给出顺序依次应用；不支持 MUST 400。
  - §Pagination：分页键固定 first/last/prev/next，不可用则省略或置 null。
  - §Filtering：filter 族保留给过滤，规范对策略不做规定。
  - §Implementation-Specific Query Parameters：实现自定义参数的基名 MUST 是合法成员名
    且**至少含一个非 a-z 字符**（否则 400）；规范借此保留纯小写名字的标准化空间。

运行：python jsonapi_query.py
"""

import re

from jsonapi import is_legal_member_name

PAGINATION_KEYS = ("first", "last", "prev", "next")
RESERVED_FAMILIES = ("include", "sort", "fields", "page", "filter")

_FAMILY_PARAM = re.compile(r"^([^\[\]]+)((?:\[[^\[\]]*\])*)$")
_FAMILY_BRACKET = re.compile(r"\[([^\[\]]*)\]")


def family_of(name):
    """返回 (基名, 方括号内的名字列表)；返回 None 表示不是合法参数族名。"""
    m = _FAMILY_PARAM.match(name)
    if not m:
        return None
    return m.group(1), _FAMILY_BRACKET.findall(m.group(2))


def is_valid_family_name(name):
    """参数是「基名 + 空方括号 / 合法成员名 / 点分隔合法成员名列表」的形状。"""
    parsed = family_of(name)
    if parsed is None:
        return False
    base, brackets = parsed
    if not is_legal_member_name(base):
        return False
    for inner in brackets:
        if inner == "":
            continue
        for member in inner.split("."):
            if not is_legal_member_name(member):
                return False
    return True


def parse_include(value):
    """§Inclusion：逗号分隔关系路径；空串表示不返回相关资源。"""
    if not value:
        return []
    return [p for p in (seg.strip() for seg in value.split(",")) if p]


def include_paths(value):
    """返回 [[关系名, ...], ...]：路径内点分隔，路径间逗号分隔。"""
    return [path.split(".") for path in parse_include(value)]


def parse_fields(params):
    """§Sparse Fieldsets：fields[articles]=title,body → {articles: [title, body]}。"""
    out = {}
    for key, raw in params.items():
        m = re.match(r"^fields\[([^\]]+)\]$", key)
        if not m:
            continue
        out[m.group(1)] = [f for f in (s.strip() for s in (raw or "").split(",")) if f]
    return out


def apply_fields(resource, fieldsets):
    """按 fields[TYPE] 裁剪资源；未指定该 type 时原样返回。

    type/id 不是「字段」，不在裁剪范围内；未被请求的 attributes/relationships 全部删除。
    """
    rtype = resource.get("type")
    if rtype not in fieldsets:
        return resource
    wanted = set(fieldsets[rtype])
    out = {"type": rtype}
    if "id" in resource:
        out["id"] = resource["id"]
    for key in ("attributes", "relationships"):
        if key in resource:
            kept = {k: v for k, v in resource[key].items() if k in wanted}
            if kept:
                out[key] = kept
    return out


def parse_sort(value):
    """§Sorting：返回 [(字段名, 是否降序), ...]，保持给出顺序。"""
    if not value:
        return []
    out = []
    for field in value.split(","):
        field = field.strip()
        if not field:
            continue
        if field.startswith("-"):
            out.append((field[1:], True))
        else:
            out.append((field, False))
    return out


def apply_sort(resources, value, getter):
    """稳定排序：先应用末位字段、再依次往前，使首位字段成为主键。"""
    items = list(resources)
    for field, desc in reversed(parse_sort(value)):
        items.sort(key=lambda r: getter(r, field), reverse=desc)
    return items


def pagination_links(links):
    """§Pagination：只保留四个合法键，未给出的不臆造。"""
    return {k: links[k] for k in PAGINATION_KEYS if k in links}


def validate_query_param(name):
    """实现自定义参数：形状合法 + 基名至少含一个非 a-z 字符。"""
    if not is_valid_family_name(name):
        return False
    base = family_of(name)[0]
    return any(not ("a" <= ch <= "z") for ch in base)


def check_query_params(names):
    """返回违反命名约定的参数名列表（服务端 MUST 400）。"""
    bad = []
    for name in names:
        base = name.split("[", 1)[0]
        if base in RESERVED_FAMILIES:
            continue
        if not validate_query_param(base):
            bad.append(name)
    return bad
