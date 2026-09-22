"""OpenAPI 3.1 契约优先：Schema Object 的最小可运行校验器。

口径来源（本轮实读原文）：
  - OpenAPI Specification v3.1.1（spec.openapis.org 全文 576 103 字节 HTML）
      * §4.4：数据类型基于 JSON Schema Validation Draft 2020-12 的六种 JSON 类型
        （null / boolean / object / array / number / string）外加 `integer` 这个
        "for convenience" 的别名；**JSON 本身没有整数类型**，
        所以 1 与 1.0 等价且都被视为整数。
      * §4.4：关键字与 format **不会隐式要求类型**——`pattern`、`date-time`
        对非字符串一律自动通过；要约束类型必须显式写 `type`。
      * §4.4.1：`format` 在默认词汇表下是**注解**不是断言；format 注册表里的
        int32/int64/float/double 的 JSON Data Type 都是 `number`，password 是 `string`。
      * §4.4.2：raw binary 用 `contentMediaType`、encoded binary 用
        `type: string` + `contentMediaType` + `contentEncoding`；
        3.0 的 `format: binary` 迁移为 `contentMediaType`，`format: byte` 迁移为
        `type: string` + `contentEncoding: base64`。
        `contentMediaType` 与 Media Type Object 的 key 矛盾时 SHALL 被忽略。
      * §4.8.24.5：Schema Object 的方言由 `$schema` 决定；未设 `jsonSchemaDialect`
        时 MUST 用 OAS 方言 `https://spec.openapis.org/oas/3.1/dialect/base`；
        资源根上的 `$schema` 永远覆盖默认值。
      * §4.8.24：`example` 已 deprecated，改用 JSON Schema 的 `examples`。
      * §4.8.24.4.1：discriminator 指向的属性 MUST 是 required；
        discriminator **MUST NOT 改变校验结论**；用在 oneOf/anyOf 上时
        所有可能的 schema MUST 显式列出；allOf 形式只服务于非校验用途。
      * §4.8.24.3.2：readOnly / writeOnly 是注解，JSON Schema 不知道数据流向。

运行：python schema.py
"""

OAS_DIALECT = "https://spec.openapis.org/oas/3.1/dialect/base"
JSON_SCHEMA_2020_12 = "https://json-schema.org/draft/2020-12/schema"

JSON_TYPES = ("null", "boolean", "object", "array", "number", "string")
SCHEMA_TYPES = JSON_TYPES + ("integer",)

FORMAT_JSON_TYPES = {
    "int32": "number", "int64": "number", "float": "number", "double": "number",
    "password": "string",
}


def is_number(value):
    """JSON 数据模型里 boolean 不是 number（Python 里 bool 是 int 的子类）。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_integer(value):
    """§4.4：整数是**数学上**定义的，1 与 1.0 都是整数。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and value.is_integer()


def type_matches(value, expected):
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "number":
        return is_number(value)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return is_integer(value)
    return False


def type_ok(value, declared):
    """`type` 可以是单个字符串，也可以是字符串数组（3.1 里 nullable 的写法）。"""
    if isinstance(declared, str):
        return type_matches(value, declared)
    if isinstance(declared, list):
        return any(type_matches(value, t) for t in declared)
    return True


def deprecated_30_keywords(schema):
    """3.0 写法在 3.1 里的遗留物。

    - `nullable: true` 被删除，改成 `type: [T, "null"]`；
    - `exclusiveMinimum` / `exclusiveMaximum` 在 2020-12 里是**数值**，
      3.0 那种"配 `minimum` 一起用布尔值"的写法必须换成数值。
    """
    out = []
    if "nullable" in schema:
        out.append("nullable（3.1 已删除，改用 type: [T, \"null\"]）")
    for key in ("exclusiveMinimum", "exclusiveMaximum"):
        if isinstance(schema.get(key), bool):
            out.append("%s 是布尔（3.0 写法，2020-12 里应为数值）" % key)
    return out


def validate(instance, schema, errors=None, path="$"):
    """一个刻意做小的 2020-12 子集校验器：只实现本 demo 要断言的那些关键字。"""
    if errors is None:
        errors = []
    if not isinstance(schema, dict):
        return errors

    if "type" in schema and not type_ok(instance, schema["type"]):
        errors.append("%s: 类型不符，期望 %r" % (path, schema["type"]))
        return errors

    if "enum" in schema and instance not in schema["enum"]:
        errors.append("%s: 不在 enum %r 内" % (path, schema["enum"]))
    if "const" in schema and instance != schema["const"]:
        errors.append("%s: 不等于 const %r" % (path, schema["const"]))

    if type_matches(instance, "string"):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append("%s: 短于 minLength" % path)
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append("%s: 长于 maxLength" % path)
        # 只有字符串实例才检查字符串关键字；§4.4 要求其余类型一律自动通过
        if "pattern" in schema:
            import re
            if not re.search(schema["pattern"], instance):
                errors.append("%s: 不匹配 pattern" % path)

    if is_number(instance):
        if "exclusiveMinimum" in schema and not instance > schema["exclusiveMinimum"]:
            errors.append("%s: 不大于 exclusiveMinimum" % path)
        if "exclusiveMaximum" in schema and not instance < schema["exclusiveMaximum"]:
            errors.append("%s: 不小于 exclusiveMaximum" % path)
        if "multipleOf" in schema:
            ratio = instance / schema["multipleOf"]
            if abs(ratio - round(ratio)) > 1e-9:
                errors.append("%s: 不是 multipleOf 的整数倍" % path)

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errors.append("%s: 缺 required 属性 %r" % (path, name))
        for name, subschema in (schema.get("properties") or {}).items():
            if name in instance:
                validate(instance[name], subschema, errors, "%s.%s" % (path, name))

    if isinstance(instance, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(instance):
                validate(item, items, errors, "%s[%d]" % (path, i))

    for subschema in schema.get("allOf") or []:
        validate(instance, subschema, errors, path)
    if "anyOf" in schema:
        if not any(_ok(instance, s) for s in schema["anyOf"]):
            errors.append("%s: 不满足任何 anyOf 分支" % path)
    if "oneOf" in schema:
        matched = [i for i, s in enumerate(schema["oneOf"]) if _ok(instance, s)]
        if len(matched) != 1:
            errors.append("%s: oneOf 命中 %d 个分支（应为 1）" % (path, len(matched)))
    return errors


def _ok(instance, schema):
    return not validate(instance, schema, [])


def discriminator_property(schema):
    """§4.8.24.4.1：discriminator.propertyName 指向的属性 MUST 是 required。"""
    disc = schema.get("discriminator")
    if not isinstance(disc, dict):
        return None
    return disc.get("propertyName")


def discriminator_issues(schema):
    """返回 discriminator 的静态问题列表（不牵涉具体实例）。"""
    out = []
    disc = schema.get("discriminator")
    if not isinstance(disc, dict):
        return out
    name = disc.get("propertyName")
    if not name:
        out.append("discriminator 缺 propertyName")
        return out
    if name not in (schema.get("required") or []):
        out.append("discriminator 属性 %r MUST 是 required 字段" % name)
    if "oneOf" not in schema and "anyOf" not in schema:
        out.append("discriminator 用在 oneOf/anyOf 之外时不在本 spec 描述的范围内")
    return out


def pick_by_discriminator(instance, schema):
    """按 discriminator 选分支：用 schema 名、被覆盖的属性值、或显式 mapping。

    返回 (命中的分支下标列表, 选中下标)。注意这只是 hint ——
    §4.8.24.4.1 明确要求 discriminator MUST NOT 改变校验结论。
    """
    disc = schema.get("discriminator") or {}
    name = disc.get("propertyName")
    branches = schema.get("oneOf") or schema.get("anyOf") or []
    if not name or not isinstance(instance, dict):
        return [], None
    value = instance.get(name)
    mapping = disc.get("mapping") or {}
    if value in mapping:
        target = mapping[value]
        idx = [i for i, b in enumerate(branches) if _branch_name(b) == target]
        return idx, (idx[0] if idx else None)
    idx = [i for i, b in enumerate(branches) if _branch_value(b, name) == value]
    return idx, (idx[0] if idx else None)


def _branch_name(branch):
    """分支的"schema 名"：未解析时取 $ref 末段，已解析时取 title。"""
    if not isinstance(branch, dict):
        return None
    ref = branch.get("$ref")
    if isinstance(ref, str):
        return ref.split("/")[-1]
    title = branch.get("title")
    return title if isinstance(title, str) else None


def _branch_value(branch, prop):
    """§4.8.24.4.1：分支的判别值 = 该属性在分支里被覆盖的值，否则取 schema 名。"""
    if not isinstance(branch, dict):
        return None
    prop_schema = (branch.get("properties") or {}).get(prop)
    if isinstance(prop_schema, dict):
        if "const" in prop_schema:
            return prop_schema["const"]
        enum = prop_schema.get("enum")
        if isinstance(enum, list) and enum:
            return enum[0]
    return _branch_name(branch)


def validation_outcome_unchanged(instance, schema):
    """§4.8.24.4.1：discriminator 不得改变 oneOf/anyOf 的校验结论。

    返回 (原始结论, 选中分支自身的结论, 是否一致到"选中即可用"的程度)。
    """
    base = _ok(instance, schema)
    _idx, chosen = pick_by_discriminator(instance, schema)
    branches = schema.get("oneOf") or schema.get("anyOf") or []
    chosen_ok = None
    if chosen is not None and chosen < len(branches):
        chosen_ok = _ok(instance, branches[chosen])
    return base, chosen_ok


def binary_schema(media_type, encoded=False, encoding="base64"):
    """§4.4.2：raw 与 encoded 两种二进制的写法。"""
    if encoded:
        return {"type": "string", "contentMediaType": media_type,
                "contentEncoding": encoding}
    return {"contentMediaType": media_type}


def migrate_from_30(schema, media_type):
    """§4.4.2.1：把 3.0 的 format: binary / byte 迁移到 3.1 写法。"""
    fmt = schema.get("format")
    if fmt == "binary":
        out = dict(schema)
        out.pop("format", None)
        out.pop("type", None)
        out["contentMediaType"] = media_type
        return out
    if fmt == "byte":
        out = dict(schema)
        out.pop("format", None)
        out["type"] = "string"
        out["contentMediaType"] = media_type
        out["contentEncoding"] = "base64"
        return out
    return dict(schema)


def content_media_type_effective(schema, media_type_object_key=None):
    """`contentMediaType` 与 Media Type Object 的 key 矛盾时 SHALL 被忽略。"""
    declared = schema.get("contentMediaType")
    if media_type_object_key is None or declared is None:
        return declared
    if declared != media_type_object_key:
        return None
    return declared
