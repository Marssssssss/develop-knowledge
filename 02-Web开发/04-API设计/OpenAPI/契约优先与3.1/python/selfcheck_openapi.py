"""608 自检：OpenAPI 3.1 契约优先（文档级 + Schema Object 级）。

所有断言都对着本轮实读的 spec.openapis.org/oas/v3.1.1 原文；
"3.0 写法"的负向用例专门用来钉住 3.1 的几处 breaking change。
"""

import copy
import sys

from harness import check, expect_errors

from oas31 import (
    OAS_DIALECT,
    collect_operation_ids,
    effective_dialect,
    is_reference,
    is_uri,
    parse_version,
    reference_extra_members,
    resolve_reference,
    same_feature_set,
    security_satisfied,
    validate_document,
)
from schema import (
    JSON_SCHEMA_2020_12,
    binary_schema,
    content_media_type_effective,
    deprecated_30_keywords,
    discriminator_issues,
    is_integer,
    is_number,
    migrate_from_30,
    pick_by_discriminator,
    type_matches,
    validate,
    validation_outcome_unchanged,
)

MIN_DOC = {"openapi": "3.1.1", "info": {"title": "t", "version": "1"},
           "paths": {}}
FULL_DOC = {
    "openapi": "3.1.1",
    "info": {"title": "Order API", "version": "2.0.0",
             "license": {"identifier": "Apache-2.0"}},
    "jsonSchemaDialect": OAS_DIALECT,
    "tags": [{"name": "orders"}, {"name": "customers"}],
    "webhooks": {"newOrder": {"post": {"responses": {"200": {"description": "ok"}}}}},
    "paths": {
        "/orders": {
            "get": {"operationId": "listOrders",
                    "responses": {"200": {"description": "ok"}}},
            "post": {"operationId": "createOrder",
                     "responses": {"201": {"description": "created"}}},
        }
    },
    "components": {"schemas": {"Pet": {"type": "object"}}},
}

# --------------------------------------------------------- §1.2 版本号

check(parse_version("3.1.1") == (3, 1, 1), "§1.2 major.minor.patch")
check(parse_version("3.1") is None, "缺 patch 不是合法版本串")
check(parse_version("v3.1.1") is None, "带 v 前缀非法")
check(parse_version(311) is None, "非字符串非法")
check(same_feature_set("3.1.0", "3.1.1"), "§1.2 patch 只纠错，同一特性集")
check(not same_feature_set("3.0.4", "3.1.1"), "major.minor 决定特性集")
check(not same_feature_set("3.1.1", "4.0.0"), "major 变化即不同特性集")

# ---------------------------------------------------- §4.8.1 文档结构

check(validate_document(MIN_DOC) == [], "只有 paths 就是合法 OAD")
check(validate_document({"openapi": "3.1.1", "components": {}}) == [], "只有 components 也合法")
check(validate_document({"openapi": "3.1.1", "webhooks": {}}) == [], "只有 webhooks 也合法")
check(validate_document(FULL_DOC) == [], "完整文档合法")
expect_errors(validate_document({"openapi": "3.1.1"}),
              ["MUST 至少含 paths / components / webhooks 之一"], label="§1.1 OAD 下限")
expect_errors(validate_document({"info": {}}), ["缺 REQUIRED 的 openapi"],
              label="§4.8.1 openapi REQUIRED")
expect_errors(validate_document({"openapi": "3.1"}), ["major.minor.patch"],
              label="openapi 必须是三段版本号")
expect_errors(validate_document({"openapi": "3.1.1", "jsonSchemaDialect": "nope",
                                 "paths": {}}), ["MUST 是 URI 形式"],
              label="§4.8.24.5 jsonSchemaDialect MUST 是 URI")
expect_errors(validate_document({"openapi": "3.1.1", "webhooks": [], "paths": {}}),
              ["Map[string, Path Item Object]"], label="webhooks 是映射")
expect_errors(validate_document({"openapi": "3.1.1", "security": {}, "paths": {}}),
              ["security: MUST 是数组"], label="security 是数组")

check(is_uri(OAS_DIALECT), "OAS 方言 id 是 URI")
check(is_uri("urn:example:dialect"), "urn: 形式也是 URI")
check(not is_uri("oas/3.1/dialect"), "裸路径不是 URI")

check(effective_dialect({"openapi": "3.1.1"}) == OAS_DIALECT,
      "§4.8.24.5 未声明时用 OAS 方言")
check(effective_dialect({"jsonSchemaDialect": "urn:x"}) == "urn:x", "jsonSchemaDialect 生效")
check(effective_dialect({"jsonSchemaDialect": "urn:x"}, {"$schema": JSON_SCHEMA_2020_12})
      == JSON_SCHEMA_2020_12, "资源根上的 $schema 永远覆盖默认值")

expect_errors(validate_document({"openapi": "3.1.1", "paths": {},
                                 "info": {"license": {"identifier": "MIT",
                                                      "url": "https://x"}}}),
              ["identifier 与 url 互斥"], label="§4.8.2.1 license 二选一")
expect_errors(validate_document({"openapi": "3.1.1", "paths": {},
                                 "info": {"license": {"url": "example.com/l"}}}),
              ["url: MUST 是 URI 形式"], label="license.url 必须是 URI")
check(validate_document({"openapi": "3.1.1", "paths": {},
                         "info": {"license": {"identifier": "MIT"}}}) == [],
      "只用 SPDX identifier 合法")

expect_errors(validate_document({"openapi": "3.1.1", "paths": {},
                                 "tags": [{"name": "a"}, {"name": "a"}]}),
              ["tag 名 'a' MUST 唯一"], label="§4.8.1 tag 名唯一")

DUP = {"openapi": "3.1.1",
       "paths": {"/a": {"get": {"operationId": "same"}},
                 "/b": {"get": {"operationId": "same"}}}}
expect_errors(validate_document(DUP), ["operationId 'same' 重复"],
              label="§4.8.9.1 operationId 唯一")
CASE = {"openapi": "3.1.1",
        "paths": {"/a": {"get": {"operationId": "list"}},
                  "/b": {"get": {"operationId": "List"}}}}
check(validate_document(CASE) == [], "§4.8.9.1 operationId 大小写敏感，List ≠ list")
check(collect_operation_ids(CASE) == ["List", "list"], "operationId 收集（按字典序）")
LINK_CLASH = {"openapi": "3.1.1", "paths": {"/a": {"get": {
    "operationId": "x",
    "links": {"l": {"operationId": "y", "operationRef": "#/paths/~1a/get"}}}}}}
expect_errors(validate_document(LINK_CLASH), ["operationRef 与 operationId 互斥"],
              label="§4.8.14.1 Link 二选一")

# ------------------------------------------------ §4.8.23 Reference Object

check(is_reference({"$ref": "#/components/schemas/Pet"}), "含 $ref 即 Reference Object")
check(not is_reference({"type": "string"}), "普通 Schema Object 不是 Reference")
check(reference_extra_members({"$ref": "#/x", "x-foo": 1}) == ["x-foo"],
      "§4.8.23 额外属性 SHALL 被忽略")
check(reference_extra_members({"$ref": "#/x", "summary": "s", "description": "d"}) == [],
      "summary / description 是合法成员")
check(resolve_reference({"$ref": "#/x"}, summary="覆盖") == {"$ref": "#/x", "summary": "覆盖"},
      "§4.8.23 summary 默认覆盖被引用组件")

# ------------------------------------------------------- §4.8.1 security

SCOPES = {"oauth": {"read", "write"}, "apiKey": set()}
check(security_satisfied([{"oauth": ["write"]}], lambda n: SCOPES.get(n))[0],
      "单一要求满足即通过")
check(security_satisfied([{"oauth": ["admin"]}, {"apiKey": []}],
                         lambda n: SCOPES.get(n)) == (True, "apiKey"),
      "§4.8.1 只需满足其中一个（备选而非全部）")
check(security_satisfied([{}], lambda n: None)[0], "空对象显式表示可不鉴权")
check(not security_satisfied([{"oauth": ["admin"]}], lambda n: SCOPES.get(n))[0],
      "scope 不足即不满足")
check(not security_satisfied([{"unknown": []}], lambda n: SCOPES.get(n))[0],
      "未知 scheme 视为未提供")
check(not security_satisfied([], lambda n: SCOPES.get(n))[0], "空 security 数组不授权")

# --------------------------------------------------------- §4.4 数据类型

check(is_integer(1) and is_integer(1.0), "§4.4 整数按数学定义，1 与 1.0 都是")
check(not is_integer(1.5), "1.5 不是整数")
check(not is_integer(True), "true 不是整数")
check(not is_integer("1"), "字符串不是整数")
check(is_number(1) and is_number(1.5), "number 含整数与浮点")
check(not is_number(True), "boolean 不是 number（Python 里 bool 是 int 子类）")
check(type_matches(None, "null"), "null")
check(type_matches(True, "boolean"), "boolean")
check(type_matches({}, "object"), "object")
check(type_matches([], "array"), "array")
check(type_matches(1.0, "number"), "number")
check(type_matches("s", "string"), "string")
check(type_matches(1, "integer"), "integer")

check(validate(1, {"type": "integer"}) == [], "type 单值")
check(validate(1.0, {"type": "integer"}) == [], "§4.4 1.0 也是 integer")
check(validate("s", {"type": "integer"}) != [], "类型不符即报错")
check(validate(None, {"type": ["string", "null"]}) == [], "3.1 用 type 数组表达可空")
check(validate("s", {"type": ["string", "null"]}) == [], "type 数组任一命中")
check(validate(3, {"type": ["string", "null"]}) != [], "type 数组全不命中即报错")
check(deprecated_30_keywords({"nullable": True}) != [], "3.1 已删除 nullable")
check(deprecated_30_keywords({"exclusiveMinimum": True}) != [],
      "3.0 的布尔 exclusiveMinimum 在 2020-12 里应为数值")
check(deprecated_30_keywords({"type": ["string", "null"]}) == [], "type 数组不是遗留写法")

check(validate(5, {"exclusiveMinimum": 5}) != [], "2020-12 的 exclusiveMinimum 是严格大于")
check(validate(5.1, {"exclusiveMinimum": 5}) == [], "大于即通过")
check(validate(5, {"exclusiveMinimum": 4}) == [], "exclusiveMinimum 不含等于")
check(validate(3, {"multipleOf": 1.5}) == [], "multipleOf 命中")
check(validate(3.1, {"multipleOf": 1.5}) != [], "multipleOf 未命中")
check(validate("ab", {"minLength": 2}) == [], "minLength 边界（闭区间）")
check(validate("a", {"minLength": 2}) != [], "短于 minLength")
check(validate("ab", {"maxLength": 2}) == [], "maxLength 边界")
check(validate("abc", {"maxLength": 2}) != [], "长于 maxLength")
check(validate("abc", {"pattern": "^a"}) == [], "pattern 命中")
check(validate(3, {"pattern": "^a"}) == [],
      "§4.4 字符串关键字对非字符串自动通过")
check(validate("nope", {"type": "string", "format": "email"}) == [],
      "§4.4.1 format 默认是注解，不参与断言")
check(validate("x", {"enum": ["x", "y"]}) == [], "enum 命中")
check(validate("z", {"enum": ["x", "y"]}) != [], "enum 未命中")
check(validate("x", {"const": "x"}) == [], "const 命中")
check(validate({"a": 1}, {"required": ["a"]}) == [], "required 命中")
check(validate({}, {"required": ["a"]}) != [], "required 缺失")
check(validate({"a": "s"}, {"properties": {"a": {"type": "string"}}}) == [], "properties")
check(validate([1, 2], {"items": {"type": "integer"}}) == [], "items")
check(validate([1, "s"], {"items": {"type": "integer"}}) != [], "items 逐元素")

# ---------------------------------------------------- §4.8.24.4 组合

check(validate({"a": 1}, {"allOf": [{"required": ["a"]}, {"required": ["b"]}]}) != [],
      "allOf 全部满足")
check(validate({"a": 1, "b": 2}, {"allOf": [{"required": ["a"]}, {"required": ["b"]}]}) == [],
      "allOf 各分支独立校验后合并")
check(validate({"a": 1}, {"anyOf": [{"required": ["a"]}, {"required": ["z"]}]}) == [],
      "anyOf 任一命中")
check(validate({"q": 1}, {"anyOf": [{"required": ["a"]}, {"required": ["z"]}]}) != [],
      "anyOf 全不命中")
check(validate({"a": 1}, {"oneOf": [{"required": ["a"]}, {"required": ["z"]}]}) == [],
      "oneOf 恰好命中一个")
check(validate({"a": 1, "z": 2}, {"oneOf": [{"required": ["a"]}, {"required": ["z"]}]}) != [],
      "oneOf 命中两个即失败")

# ------------------------------------------------ §4.8.24.4.1 discriminator

CAT = {"title": "Cat", "type": "object", "required": ["petType", "meow"],
       "properties": {"petType": {"const": "cat"}, "meow": {"type": "boolean"}}}
DOG = {"title": "Dog", "type": "object", "required": ["petType", "bark"],
       "properties": {"petType": {"const": "dog"}, "bark": {"type": "boolean"}}}
PET = {"type": "object", "required": ["petType"],
       "properties": {"petType": {"type": "string"}},
       "discriminator": {"propertyName": "petType",
                         "mapping": {"dog": "Dog", "cat": "Cat"}},
       "oneOf": [CAT, DOG]}

check(discriminator_issues(PET) == [], "discriminator 属性是 required 时合法")
expect_errors(discriminator_issues({"discriminator": {"propertyName": "t"}}),
              ["MUST 是 required 字段"],
              label="§4.8.24.4.1 discriminator 属性 MUST required")
expect_errors(discriminator_issues({"required": ["t"], "discriminator": {"propertyName": "t"}}),
              ["不在本 spec 描述的范围内"],
              label="discriminator 脱离 oneOf/anyOf 时规范未定义")
expect_errors(discriminator_issues({"required": ["t"], "discriminator": {}}),
              ["缺 propertyName"], label="discriminator 缺 propertyName")

check(pick_by_discriminator({"petType": "dog"}, PET)[1] == 1, "mapping dog → Dog（下标 1）")
check(pick_by_discriminator({"petType": "cat"}, PET)[1] == 0, "mapping cat → Cat（下标 0）")
check(pick_by_discriminator({"petType": "bird"}, PET) == ([], None), "未知值不选中任何分支")
check(pick_by_discriminator("not-an-object", PET) == ([], None), "非对象实例不参与选择")

IMPLICIT = {"type": "object", "required": ["kind"],
            "properties": {"kind": {"type": "string"}},
            "discriminator": {"propertyName": "kind"},
            "oneOf": [{"title": "A", "properties": {"kind": {"const": "A"}}},
                      {"title": "B", "properties": {"kind": {"const": "B"}}}]}
check(pick_by_discriminator({"kind": "A"}, IMPLICIT)[1] == 0,
      "无 mapping 时用属性被覆盖的值匹配")
check(pick_by_discriminator({"kind": "B"}, IMPLICIT)[1] == 1, "隐式选择第二个分支")

check(validation_outcome_unchanged({"petType": "dog", "bark": True}, PET) == (True, True),
      "选中分支自身也校验通过")
check(validation_outcome_unchanged({"petType": "dog", "meow": True}, PET) == (False, False),
      "选中分支自身失败时整体结论仍是 False")

NO_DISC = copy.deepcopy(PET)
NO_DISC.pop("discriminator")
for inst in ({"petType": "dog", "bark": True}, {"petType": "cat", "meow": True},
             {"petType": "dog", "meow": True}, {"petType": "bird"}):
    check(validate(inst, PET, []) == validate(inst, NO_DISC, []),
          "§4.8.24.4.1 discriminator MUST NOT 改变校验结论（%r）" % (inst,))

# --------------------------------------------------------- §4.4.2 二进制

check(binary_schema("image/png") == {"contentMediaType": "image/png"}, "raw 二进制不写 type")
check(binary_schema("image/png", encoded=True) ==
      {"type": "string", "contentMediaType": "image/png", "contentEncoding": "base64"},
      "encoded 二进制 = string + contentMediaType + contentEncoding")
check(migrate_from_30({"type": "string", "format": "binary"}, "image/png")
      == {"contentMediaType": "image/png"}, "§4.4.2.1 format: binary → contentMediaType")
check(migrate_from_30({"type": "string", "format": "byte"}, "image/png") ==
      {"type": "string", "contentMediaType": "image/png", "contentEncoding": "base64"},
      "§4.4.2.1 format: byte → string + base64")
check(migrate_from_30({"type": "string"}, "image/png") == {"type": "string"},
      "无 3.0 遗留 format 时原样返回")
check(content_media_type_effective({"contentMediaType": "image/png"}, "application/json")
      is None, "§4.4.2 与 Media Type Object 的 key 矛盾时 SHALL 被忽略")
check(content_media_type_effective({"contentMediaType": "image/png"}, "image/png")
      == "image/png", "一致时保留")
check(content_media_type_effective({"contentMediaType": "image/png"}) == "image/png",
      "无 Media Type Object 时保留")

if __name__ == "__main__":
    print()
    import harness
    if harness.FAILURES:
        print("FAILED %d: %s" % (len(harness.FAILURES), harness.FAILURES[:5]))
        sys.exit(1)
    print("ALL PASS (%d 断言)" % harness.count())
