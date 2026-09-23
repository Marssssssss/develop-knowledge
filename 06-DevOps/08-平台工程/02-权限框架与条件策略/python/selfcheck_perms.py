"""自检：Backstage 权限框架（perms.py）。成对构造误报集/漏报集，避免恒真断言。"""

from perms import (
    ALLOW, CONDITIONAL, DENY, CATALOG_RULES, Permission, PermissionError,
    PolicyContext, PolicyQuery, authorize, default_policy, eval_criteria,
    is_permission, is_resource_permission, owner_only_policy, validate_decision,
)

PASS = 0
FAIL = []


def check(label, got, expect):
    global PASS
    if got == expect:
        PASS += 1
    else:
        FAIL.append(f"{label}: got {got!r}, expect {expect!r}")


def raises(label, fn, *a, **kw):
    global PASS
    try:
        fn(*a, **kw)
    except PermissionError:
        PASS += 1
    except Exception as e:
        FAIL.append(f"{label}: raised {type(e).__name__} instead of PermissionError")
    else:
        FAIL.append(f"{label}: expected PermissionError")


# ------------------------------------------------------------------ 权限对象
delete_perm = Permission("catalog.entity.delete", "delete", "catalog-entity")
read_perm = Permission("catalog.entity.read", "read", "catalog-entity")
create_perm = Permission("catalog.entity.create", "create")
check("资源权限识别", delete_perm.is_resource_permission, True)
check("基础权限没有 resourceType", create_perm.is_resource_permission, False)
check("按 name 判等", is_permission(delete_perm, Permission("catalog.entity.delete", "read")), True)
check("resourceType 收窄", is_resource_permission(delete_perm, "catalog-entity"), True)
check("resourceType 不匹配", is_resource_permission(delete_perm, "scaffolder-action"), False)
check("action 序列化进 attributes", delete_perm.to_dict()["attributes"], {"action": "delete"})
raises("非法 action 被拒", Permission, "x", "explode")
raises("空 name 被拒", Permission, "", "read")

# ---------------------------------------------------------------- 决策形状
check("确定决策 ALLOW", validate_decision({"result": ALLOW}), [])
check("确定决策 DENY", validate_decision({"result": DENY}), [])
check("未知 result", validate_decision({"result": "MAYBE"}),
      ["unknown result 'MAYBE'"])
check("条件决策缺 pluginId", "conditional decision requires pluginId"
      in validate_decision({"result": CONDITIONAL, "resourceType": "catalog-entity",
                            "conditions": {"rule": "IS_ENTITY_OWNER"}}), True)
check("确定决策携带 conditions 被拒",
      any("conditions" in e for e in validate_decision(
          {"result": ALLOW, "conditions": {"rule": "X"}})), True)
check("条件决策完整", validate_decision(
    {"result": CONDITIONAL, "pluginId": "catalog", "resourceType": "catalog-entity",
     "conditions": {"rule": "IS_ENTITY_OWNER", "params": {"claims": ["group:default/a"]}}}), [])

# ---------------------------------------------------------------- 条件树校验
check("allOf 空数组被拒", any("non-empty" in e for e in
      validate_decision({"result": CONDITIONAL, "pluginId": "catalog",
                         "resourceType": "catalog-entity", "conditions": {"allOf": []}})), True)
check("anyOf 空数组被拒", any("non-empty" in e for e in
      validate_decision({"result": CONDITIONAL, "pluginId": "catalog",
                         "resourceType": "catalog-entity", "conditions": {"anyOf": []}})), True)
check("裸对象既非组合也非条件", validate_decision(
    {"result": CONDITIONAL, "pluginId": "catalog", "resourceType": "catalog-entity",
     "conditions": {"foo": 1}}),
      ["criteria must be one of allOf / anyOf / not / a condition with rule"])
check("嵌套非法递归上报", any("non-empty" in e for e in validate_decision(
    {"result": CONDITIONAL, "pluginId": "catalog", "resourceType": "catalog-entity",
     "conditions": {"not": {"allOf": [{"allOf": []}]}}})), True)

# -------------------------------------------------------------------- 求值
entity = {
    "kind": "Component",
    "owners": ["group:default/platform", "user:default/alice"],
    "annotations": {"backstage.io/managed-by-location": "file:/tmp/catalog-info.yaml"},
}
check("owner 命中", eval_criteria(
    {"rule": "IS_ENTITY_OWNER", "params": {"claims": ["user:default/alice"]}}, entity), True)
check("owner 未命中", eval_criteria(
    {"rule": "IS_ENTITY_OWNER", "params": {"claims": ["user:default/bob"]}}, entity), False)
check("空 claims 不是 owner（负控）", eval_criteria(
    {"rule": "IS_ENTITY_OWNER", "params": {"claims": []}}, entity), False)
check("hasAnnotation 只给 key", eval_criteria(
    {"rule": "HAS_ANNOTATION", "params": {"key": "backstage.io/managed-by-location"}}, entity), True)
check("hasAnnotation key 缺失", eval_criteria(
    {"rule": "HAS_ANNOTATION", "params": {"key": "nope"}}, entity), False)
check("hasAnnotation 值不匹配", eval_criteria(
    {"rule": "HAS_ANNOTATION",
     "params": {"key": "backstage.io/managed-by-location", "value": "url:other"}}, entity), False)
check("isEntityKind 命中", eval_criteria(
    {"rule": "IS_ENTITY_KIND", "params": {"kinds": ["Component", "API"]}}, entity), True)
raises("未知规则抛 PermissionError", eval_criteria, {"rule": "NOPE"}, entity)

combo = {"allOf": [
    {"rule": "IS_ENTITY_KIND", "params": {"kinds": ["Component"]}},
    {"anyOf": [
        {"rule": "IS_ENTITY_OWNER", "params": {"claims": ["user:default/bob"]}},
        {"rule": "HAS_ANNOTATION", "params": {"key": "backstage.io/managed-by-location"}},
    ]},
    {"not": {"rule": "IS_ENTITY_OWNER", "params": {"claims": ["group:default/other"]}}},
]}
check("组合条件为真", eval_criteria(combo, entity), True)
check("去掉救场的 annotation 后为假", eval_criteria(combo, {**entity, "annotations": {}}), False)
check("not 翻转生效", eval_criteria(
    {"not": {"rule": "IS_ENTITY_OWNER", "params": {"claims": ["user:default/alice"]}}}, entity), False)
check("not 嵌套 not", eval_criteria(
    {"not": {"not": {"rule": "IS_ENTITY_KIND", "params": {"kinds": ["Component"]}}}}, entity), True)

# -------------------------------------------------------------------- 授权
owner_policy = owner_only_policy("catalog-entity")
q = PolicyQuery(delete_perm, resource_ref="component:default/artist-web")
check("owner 授权通过", authorize(q, PolicyContext(["user:default/alice"]), entity, owner_policy), ALLOW)
check("非 owner 被拒", authorize(q, PolicyContext(["user:default/bob"]), entity, owner_policy), DENY)
check("匿名（无 claims）被拒", authorize(q, PolicyContext([]), entity, owner_policy), DENY)
check("默认策略 allow-all", authorize(q, PolicyContext([]), entity, default_policy), ALLOW)
check("资源类型不匹配时视为基础权限", authorize(
    PolicyQuery(create_perm), PolicyContext([]), entity, owner_policy), ALLOW)
raises("条件决策与权限 resourceType 不一致", authorize,
       PolicyQuery(read_perm), PolicyContext(["user:default/alice"]), entity,
       lambda query, ctx: {"result": CONDITIONAL, "pluginId": "catalog",
                           "resourceType": "scaffolder-action",
                           "conditions": {"rule": "IS_ENTITY_OWNER"}})

# 策略返回非法决策 → 授权阶段直接失败（不让非法决策静默放行）
raises("非法决策在 authorize 被拦下", authorize, q, PolicyContext([]), entity,
       lambda query, ctx: {"result": CONDITIONAL, "pluginId": "catalog",
                           "resourceType": "catalog-entity", "conditions": {"anyOf": []}})

check("规则库三条规则齐全", sorted(CATALOG_RULES), ["HAS_ANNOTATION", "IS_ENTITY_KIND", "IS_ENTITY_OWNER"])

# ---------------------------------------------------- 补：组合语义与形状边界
check("单元素 anyOf 等价裸条件", eval_criteria(
    {"anyOf": [{"rule": "IS_ENTITY_KIND", "params": {"kinds": ["Component"]}}]}, entity), True)
check("allOf 一个假则假", eval_criteria({"allOf": [
    {"rule": "IS_ENTITY_KIND", "params": {"kinds": ["Component"]}},
    {"rule": "IS_ENTITY_KIND", "params": {"kinds": ["API"]}}]}, entity), False)
check("allOf 全真才真", eval_criteria({"allOf": [
    {"rule": "IS_ENTITY_KIND", "params": {"kinds": ["Component"]}},
    {"rule": "IS_ENTITY_KIND", "params": {"kinds": ["API", "Component"]}}]}, entity), True)
check("params 缺失等价于空 claims", eval_criteria({"rule": "IS_ENTITY_OWNER"}, entity), False)
check("criteria 非对象被拒", any("must be an object" in e for e in validate_decision(
    {"result": CONDITIONAL, "pluginId": "catalog", "resourceType": "catalog-entity",
     "conditions": ["IS_ENTITY_OWNER"]})), True)
check("params 类型非法被拒", any("params" in e for e in validate_decision(
    {"result": CONDITIONAL, "pluginId": "catalog", "resourceType": "catalog-entity",
     "conditions": {"rule": "IS_ENTITY_OWNER", "params": "group:default/a"}})), True)
check("pluginId 非字符串被拒", any("pluginId" in e for e in validate_decision(
    {"result": CONDITIONAL, "pluginId": 42, "resourceType": "catalog-entity",
     "conditions": {"rule": "IS_ENTITY_OWNER"}})), True)
check("默认策略对 delete 也放行", authorize(
    PolicyQuery(delete_perm), PolicyContext([]), entity, default_policy), ALLOW)

if FAIL:
    print(f"FAILED {len(FAIL)} / {PASS + len(FAIL)}")
    for f in FAIL:
        print("  -", f)
    raise SystemExit(1)
print(f"perms selfcheck: {PASS} assertions passed")
