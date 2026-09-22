"""OpenAPI 3.1 文档级约束的最小可运行校验器。

口径来源（本轮实读 spec.openapis.org/oas/v3.1.1 全文，576 103 字节）：
  - §4.8.1 OpenAPI Object：`openapi` **REQUIRED**，值是本规范的版本号，
    与 `info.version` 无关；`jsonSchemaDialect` MUST 是 URI 形式；
    `servers` 缺省（或空数组）时等价于一个 `url: /` 的 Server Object；
    `webhooks` 的 key 是唯一字符串，值是 Path Item Object。
  - §1.1 / §4：一份 OpenAPI Description（OAD）由 entry document 加它引用的文档组成，
    **MUST 至少含 `paths`、`components`、`webhooks` 之一**。
  - §1.2：版本用 major.minor.patch；major.minor 决定特性集，**patch 只纠错与澄清**，
    支持 OAS 3.1 的工具 SHOULD 兼容所有 3.1.*，且 SHOULD NOT 区分 3.1.0 与 3.1.1。
  - §4.8.2.1 Info Object：`license.identifier` 是 [SPDX-Licenses] 表达式，
    与 `license.url` **互斥**。
  - §4.8.1：`tags` 列表里每个 tag 名 MUST 唯一；没在 `tags` 里声明的 tag MAY 被
    工具自行组织，不要求声明。
  - §4.8.1：`security` 是**备选**列表——只需满足其中一个即可授权；
    要显式声明"可不鉴权"，塞一个空对象 `{}` 进数组。
  - §4.8.9.1 Operation Object：`operationId` MUST 在整个 API 内唯一且**大小写敏感**。
  - §4.8.23 Reference Object：只允许 `$ref` / `summary` / `description`，
    额外属性 SHALL 被忽略；`summary` / `description` 默认 SHOULD 覆盖被引用组件的
    同名成员（被引用类型不支持该成员时无效果）。
  - §4.8.14.1 Link Object：`operationRef` 与 `operationId` 互斥。

运行：python oas31.py
"""

import re

from schema import OAS_DIALECT  # noqa: F401  供上层直接引用

REFERENCE_MEMBERS = ("$ref", "summary", "description")

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(value):
    """§1.2：major.minor.patch。返回 (major, minor, patch)，非法返回 None。"""
    m = _SEMVER.match(value.strip()) if isinstance(value, str) else None
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def same_feature_set(a, b):
    """§1.2：patch 版本 SHOULD NOT 被工具区分 —— 只比 major.minor。"""
    va, vb = parse_version(a), parse_version(b)
    if va is None or vb is None:
        return False
    return va[:2] == vb[:2]


def is_uri(value):
    """§4.8.24.5：jsonSchemaDialect 'MUST be in the form of a URI'。"""
    return isinstance(value, str) and re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", value) is not None


def effective_dialect(document, schema=None):
    """§4.8.24.5：`$schema`（资源根）> `jsonSchemaDialect` > OAS 方言。"""
    if isinstance(schema, dict) and isinstance(schema.get("$schema"), str):
        return schema["$schema"]
    declared = document.get("jsonSchemaDialect")
    if isinstance(declared, str):
        return declared
    return OAS_DIALECT


def validate_document(doc, errors=None, path="$"):
    """校验一份 OpenAPI Document，返回错误列表。"""
    if errors is None:
        errors = []
    if not isinstance(doc, dict):
        errors.append("%s: 文档必须是对象" % path)
        return errors

    if "openapi" not in doc:
        errors.append("%s: 缺 REQUIRED 的 openapi" % path)
    elif parse_version(doc["openapi"]) is None:
        errors.append("%s.openapi: 必须是 major.minor.patch" % path)

    if not any(k in doc for k in ("paths", "components", "webhooks")):
        errors.append("%s: OAD MUST 至少含 paths / components / webhooks 之一" % path)

    if "jsonSchemaDialect" in doc and not is_uri(doc["jsonSchemaDialect"]):
        errors.append("%s.jsonSchemaDialect: MUST 是 URI 形式" % path)
    if "webhooks" in doc and not isinstance(doc["webhooks"], dict):
        errors.append("%s.webhooks: MUST 是 Map[string, Path Item Object]" % path)

    info = doc.get("info")
    if info is not None:
        if not isinstance(info, dict):
            errors.append("%s.info: MUST 是对象" % path)
        else:
            _validate_license(info.get("license"), errors, path + ".info.license")

    tags = doc.get("tags")
    if tags is not None:
        if not isinstance(tags, list):
            errors.append("%s.tags: MUST 是数组" % path)
        else:
            seen = set()
            for tag in tags:
                name = tag.get("name") if isinstance(tag, dict) else None
                if name in seen:
                    errors.append("%s.tags: tag 名 %r MUST 唯一" % (path, name))
                seen.add(name)

    security = doc.get("security")
    if security is not None and not isinstance(security, list):
        errors.append("%s.security: MUST 是数组" % path)

    for path_key, item in (doc.get("paths") or {}).items():
        _validate_path_item(item, errors, "%s.paths.%s" % (path, path_key))
    _check_operation_ids(doc, errors, path)
    return errors


def _validate_license(license_obj, errors, where):
    if license_obj is None:
        return
    if not isinstance(license_obj, dict):
        errors.append("%s: MUST 是对象" % where)
        return
    if "identifier" in license_obj and "url" in license_obj:
        errors.append("%s: identifier 与 url 互斥" % where)
    if "url" in license_obj and not is_uri(license_obj["url"]):
        errors.append("%s.url: MUST 是 URI 形式" % where)


def _validate_path_item(item, errors, where):
    if isinstance(item, dict) and "$ref" in item and len(item) == 1:
        return
    if not isinstance(item, dict):
        errors.append("%s: Path Item Object 必须是对象" % where)
        return
    for method in ("get", "put", "post", "delete", "options", "head", "patch", "trace"):
        if method in item:
            _validate_operation(item[method], errors, "%s.%s" % (where, method))


def _validate_operation(operation, errors, where):
    if not isinstance(operation, dict):
        errors.append("%s: Operation Object 必须是对象" % where)
        return
    op_id = operation.get("operationId")
    if op_id is not None and not isinstance(op_id, str):
        errors.append("%s.operationId: MUST 是字符串" % where)
    responses = operation.get("responses")
    if responses is not None and not isinstance(responses, dict):
        errors.append("%s.responses: MUST 是对象" % where)
    for name, link in (operation.get("links") or {}).items():
        where_link = "%s.links.%s" % (where, name)
        if not isinstance(link, dict):
            errors.append("%s: Link Object 必须是对象" % where_link)
            continue
        if "operationRef" in link and "operationId" in link:
            errors.append("%s: operationRef 与 operationId 互斥" % where_link)


def _check_operation_ids(doc, errors, path):
    """§4.8.9.1：operationId MUST 在整个 API 内唯一，且大小写敏感。"""
    seen = {}
    for path_key, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method not in ("get", "put", "post", "delete", "options",
                              "head", "patch", "trace"):
                continue
            if not isinstance(operation, dict):
                continue
            op_id = operation.get("operationId")
            if not isinstance(op_id, str):
                continue
            if op_id in seen:
                errors.append("%s: operationId %r 重复（%s 与 %s）"
                              % (path, op_id, seen[op_id], "%s.%s" % (path_key, method)))
            seen[op_id] = "%s.%s" % (path_key, method)


def collect_operation_ids(doc):
    return sorted(_operation_ids(doc))


def _operation_ids(doc):
    out = set()
    for _key, item in (doc.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in ("get", "put", "post", "delete", "options", "head", "patch", "trace"):
            operation = item.get(method)
            if isinstance(operation, dict) and isinstance(operation.get("operationId"), str):
                out.add(operation["operationId"])
    return out


def is_reference(obj):
    """§4.8.23：含 `$ref` 的 Reference Object 只允许三个成员。"""
    return isinstance(obj, dict) and "$ref" in obj


def reference_extra_members(obj):
    """额外属性 SHALL 被忽略 —— 这里返回被忽略掉的那部分，便于断言。"""
    if not is_reference(obj):
        return []
    return [k for k in obj if k not in REFERENCE_MEMBERS]


def resolve_reference(obj, summary=None, description=None):
    """`summary` / `description` 默认 SHOULD 覆盖被引用组件的同名成员。"""
    out = {"$ref": obj["$ref"]}
    if summary is not None:
        out["summary"] = summary
    if description is not None:
        out["description"] = description
    return out


def security_satisfied(security, provided_scopes_for):
    """§4.8.1：security 是**备选**——任一 Security Requirement 满足即可。

    `provided_scopes_for(scheme_name)` 返回本次请求实际具备的 scope 集合。
    空对象 `{}` 表示"可不鉴权"，直接视为满足。
    """
    for requirement in security or []:
        if not requirement:
            return True, "empty requirement（显式可不鉴权）"
        ok = True
        for scheme, scopes in requirement.items():
            have = provided_scopes_for(scheme)
            if have is None or not set(scopes).issubset(have):
                ok = False
                break
        if ok:
            return True, list(requirement)[0]
    return False, None
