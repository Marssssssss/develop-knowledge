"""演示：三种策略（allow-all / deny-all delete / only-owner）对同一请求的结果。

运行：python python/main.py
"""

from perms import (
    ALLOW, CONDITIONAL, DENY, Permission, PolicyContext, PolicyQuery,
    authorize, default_policy, owner_only_policy, validate_decision,
)

DELETE = Permission("catalog.entity.delete", "delete", "catalog-entity")
CREATE = Permission("catalog.entity.create", "create")

ENTITY = {
    "kind": "Component",
    "owners": ["group:default/platform", "user:default/alice"],
    "annotations": {"backstage.io/managed-by-location": "file:/tmp/catalog-info.yaml"},
}


def deny_delete(query: PolicyQuery, ctx: PolicyContext) -> dict:
    if query.permission.name == "catalog.entity.delete":
        return {"result": DENY}
    return {"result": ALLOW}


def main() -> None:
    query = PolicyQuery(DELETE, resource_ref="component:default/artist-web")
    scenarios = [
        ("匿名 + 默认策略", PolicyContext([]), default_policy),
        ("alice + 禁删策略", PolicyContext(["user:default/alice"]), deny_delete),
        ("alice + owner-only", PolicyContext(["user:default/alice"]), owner_only_policy("catalog-entity")),
        ("bob + owner-only", PolicyContext(["user:default/bob"]), owner_only_policy("catalog-entity")),
        ("匿名 + owner-only", PolicyContext([]), owner_only_policy("catalog-entity")),
        ("平台组 + owner-only", PolicyContext(["group:default/platform"]), owner_only_policy("catalog-entity")),
    ]
    for label, ctx, policy in scenarios:
        decision = policy(query, ctx)
        result = authorize(query, ctx, ENTITY, policy)
        print(f"{label:22} 策略={decision['result']:11} 最终={result}")

    decision = owner_only_policy("catalog-entity")(query, PolicyContext(["user:default/alice"]))
    print()
    print("条件决策原文：", decision)
    print("形状校验：", validate_decision(decision) or "OK")
    print("resourceType 收窄后基础权限不受条件约束：",
          authorize(PolicyQuery(CREATE), PolicyContext([]), ENTITY,
                    owner_only_policy("catalog-entity")), "（应为 ALLOW）")
    print("CONDITIONAL 字面量是：", repr(CONDITIONAL))


if __name__ == "__main__":
    main()
