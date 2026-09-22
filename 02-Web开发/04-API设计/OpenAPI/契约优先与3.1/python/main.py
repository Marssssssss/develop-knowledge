"""608 OpenAPI 3.1 契约优先 —— 演示入口。

从一份"契约"出发：先校验文档本身合不合规，再解析 `$ref` 用同一份 Schema Object
去校验实例，最后看 discriminator 与二进制描述这两个 3.1 的改动点。
"""

import json

from oas31 import (
    OAS_DIALECT,
    collect_operation_ids,
    effective_dialect,
    reference_extra_members,
    same_feature_set,
    security_satisfied,
    validate_document,
)
from schema import (
    binary_schema,
    content_media_type_effective,
    discriminator_issues,
    migrate_from_30,
    pick_by_discriminator,
    validate,
    validation_outcome_unchanged,
)

DOC = {
    "openapi": "3.1.1",
    "info": {
        "title": "Order API",
        "version": "2.0.0",
        "license": {"identifier": "Apache-2.0"},
    },
    "jsonSchemaDialect": OAS_DIALECT,
    "tags": [{"name": "orders"}, {"name": "customers"}],
    "paths": {
        "/orders": {
            "get": {"operationId": "listOrders",
                    "responses": {"200": {"description": "ok"}}},
            "post": {"operationId": "createOrder",
                     "responses": {"201": {"description": "created"}}},
        }
    },
    "components": {
        "schemas": {
            "Cat": {
                "title": "Cat",
                "type": "object",
                "required": ["petType", "meow"],
                "properties": {"petType": {"const": "cat"}, "meow": {"type": "boolean"}},
            },
            "Dog": {
                "title": "Dog",
                "type": "object",
                "required": ["petType", "bark"],
                "properties": {"petType": {"const": "dog"}, "bark": {"type": "boolean"}},
            },
            "Pet": {
                "type": "object",
                "required": ["petType"],
                "properties": {"petType": {"type": "string"}},
                "discriminator": {
                    "propertyName": "petType",
                    "mapping": {"dog": "Dog", "cat": "Cat"},
                },
                "oneOf": [
                    {"$ref": "#/components/schemas/Cat"},
                    {"$ref": "#/components/schemas/Dog"},
                ],
            },
        }
    },
}


def resolve(node, root=None):
    """把 `#/components/schemas/X` 这类内引用就地展开（只支持本 demo 用到的形状）。"""
    root = root if root is not None else node
    if isinstance(node, list):
        return [resolve(x, root) for x in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node and isinstance(node["$ref"], str) and node["$ref"].startswith("#/"):
        target = root
        for part in node["$ref"][2:].split("/"):
            target = target.get(part.replace("~1", "/").replace("~0", "~"), {})
        return resolve(target, root)
    return {k: resolve(v, root) for k, v in node.items()}


def main():
    print("== 文档级校验（§4.8.1 / §1.1 / §1.2）==")
    print("  校验:", validate_document(DOC) or "OK")
    print("  OAD 至少含三者之一:", any(k in DOC for k in ("paths", "components", "webhooks")))
    print("  有效方言:", effective_dialect(DOC))
    print("  3.1.0 与 3.1.1 同一特性集:", same_feature_set("3.1.0", "3.1.1"))
    print("  operationId:", collect_operation_ids(DOC))

    bad = {"openapi": "3.1.1", "info": {"title": "x", "version": "1",
                                        "license": {"identifier": "MIT",
                                                    "url": "https://example.com/l"}}}
    print("  license 同时给 identifier 与 url:", validate_document(bad))
    print("  缺 paths/components/webhooks:", validate_document({"openapi": "3.1.1"}))
    dup = {"openapi": "3.1.1",
           "paths": {"/a": {"get": {"operationId": "same"}},
                     "/b": {"get": {"operationId": "same"}}}}
    print("  operationId 重复:", validate_document(dup))

    print()
    print("== Reference Object（§4.8.23）==")
    ref = {"$ref": "#/components/schemas/Pet", "summary": "覆盖摘要", "x-ignored": 1}
    print("  额外成员（SHALL 被忽略）:", reference_extra_members(ref))

    print()
    print("== security 是备选而非全部满足（§4.8.1）==")
    scopes = {"oauth": {"read"}, "apiKey": set()}
    print("  满足其一即通过:",
          security_satisfied([{"oauth": ["write"]}, {"apiKey": []}],
                             lambda n: scopes.get(n)))
    print("  空对象 = 可不鉴权:", security_satisfied([{}], lambda n: None))
    print("  一个都不满足:", security_satisfied([{"oauth": ["write"]}],
                                        lambda n: scopes.get(n)))

    print()
    print("== Schema Object（§4.4 / §4.8.24）==")
    print("  1 与 1.0 都是整数:",
          validate(1, {"type": "integer"}) == [] and validate(1.0, {"type": "integer"}) == [])
    print("  true 不是整数:", validate(True, {"type": "integer"}) != [])
    print("  type 数组表达 nullable:", validate(None, {"type": ["string", "null"]}) == [])
    print("  pattern 对非字符串自动通过:", validate(3, {"pattern": "^a"}) == [])
    print("  format 不参与断言（注解）:", validate("nope", {"type": "string",
                                              "format": "email"}) == [])

    pet = resolve(DOC["components"]["schemas"]["Pet"], DOC)
    print("  discriminator 静态检查:", discriminator_issues(pet) or "OK")
    for inst in ({"petType": "dog", "bark": True}, {"petType": "cat", "meow": True},
                 {"petType": "dog", "meow": True}, {"petType": "bird"}):
        _idx, chosen = pick_by_discriminator(inst, pet)
        base, chosen_ok = validation_outcome_unchanged(inst, pet)
        print("    %-34s 选中=%s 原结论=%s 选中分支=%s"
              % (json.dumps(inst), chosen, base, chosen_ok))

    print()
    print("== 二进制描述（§4.4.2）==")
    print("  raw    :", binary_schema("image/png"))
    print("  encoded:", binary_schema("image/png", encoded=True))
    print("  3.0 binary 迁移:", migrate_from_30({"type": "string", "format": "binary"},
                                        "image/png"))
    print("  3.0 byte   迁移:", migrate_from_30({"type": "string", "format": "byte"},
                                        "image/png"))
    print("  contentMediaType 与 key 矛盾:",
          content_media_type_effective({"contentMediaType": "image/png"}, "application/json"))
    print("  与 key 一致:",
          content_media_type_effective({"contentMediaType": "image/png"}, "image/png"))


if __name__ == "__main__":
    main()
