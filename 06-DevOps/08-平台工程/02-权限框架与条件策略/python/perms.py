"""Backstage 权限框架：权限、策略决策与条件（criteria）求值。

事实来源（本轮实读）：
- `@backstage/plugin-permission-common` 的 `dist/index.d.ts`（jsDelivr 取 npm 包类型声明）：
  `AuthorizeResult` 三个字面量是 **"ALLOW" / "DENY" / "CONDITIONAL"**（大写字符串）；
  `DefinitivePolicyDecision = { result: ALLOW | DENY }`；
  `ConditionalPolicyDecision = { result: CONDITIONAL, pluginId, resourceType, conditions }`；
  `PermissionCondition = { resourceType, rule, params? }`；
  `AllOfCriteria/AnyOfCriteria` 的 children 是 **NonEmptyArray**（`[T, ...T[]]`）；
  `NotCriteria = { not: PermissionCriteria }`。
- backstage.io《Writing a permission policy》《Concepts》：策略是**决策方**、插件是**执行方**；
  默认策略 allow-all；条件决策把「判定」委托回拥有资源的插件；
  「条件全部为真才允许」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

ALLOW = "ALLOW"
DENY = "DENY"
CONDITIONAL = "CONDITIONAL"

ACTIONS = ("create", "read", "update", "delete")


class PermissionError(ValueError):
    pass


@dataclass(frozen=True)
class Permission:
    """权限：name + attributes（action）+ 可选 resourceType（资源权限）。"""
    name: str
    action: str
    resource_type: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.name:
            raise PermissionError("permission name is required")
        if self.action not in ACTIONS:
            raise PermissionError(f"action must be one of {ACTIONS}, got {self.action!r}")

    @property
    def is_resource_permission(self) -> bool:
        return self.resource_type is not None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"name": self.name, "attributes": {"action": self.action}}
        if self.resource_type:
            d["resourceType"] = self.resource_type
        return d


def is_resource_permission(p: Permission, resource_type: str) -> bool:
    return p.resource_type == resource_type


def is_permission(p: Permission, other: Permission) -> bool:
    """官方同名工具：按 name 比较（同一权限对象在不同包里可能是两个实例）。"""
    return p.name == other.name


# ------------------------------------------------------------------ 决策校验

def validate_decision(decision: Dict[str, Any]) -> List[str]:
    """校验策略决策的形状，返回错误列表。"""
    errors: List[str] = []
    if not isinstance(decision, dict) or "result" not in decision:
        return ["decision must be an object with a result"]
    result = decision["result"]
    if result not in (ALLOW, DENY, CONDITIONAL):
        errors.append(f"unknown result {result!r}")
        return errors

    if result == CONDITIONAL:
        for key in ("pluginId", "resourceType", "conditions"):
            if key not in decision:
                errors.append(f"conditional decision requires {key}")
        if "conditions" in decision:
            errors.extend(_validate_criteria(decision["conditions"]))
        if "pluginId" in decision and not isinstance(decision["pluginId"], str):
            errors.append("pluginId must be a string")
        if "resourceType" in decision and not isinstance(decision["resourceType"], str):
            errors.append("resourceType must be a string")
    else:
        for key in ("pluginId", "resourceType", "conditions"):
            if key in decision:
                errors.append(f"definitive decision must not carry {key}")
    return errors


def _validate_criteria(criteria: Any, depth: int = 0) -> List[str]:
    if depth > 32:
        return ["criteria nested too deeply"]
    if not isinstance(criteria, dict):
        return [f"criteria must be an object, got {type(criteria).__name__}"]

    if "allOf" in criteria or "anyOf" in criteria:
        key = "allOf" if "allOf" in criteria else "anyOf"
        children = criteria[key]
        if not isinstance(children, (list, tuple)) or len(children) == 0:
            return [f"{key} must be a non-empty array (NonEmptyArray)"]
        errors: List[str] = []
        for child in children:
            errors.extend(_validate_criteria(child, depth + 1))
        return errors

    if "not" in criteria:
        return _validate_criteria(criteria["not"], depth + 1)

    if "rule" in criteria:
        errors = []
        if not isinstance(criteria["rule"], str) or not criteria["rule"]:
            errors.append("condition rule must be a non-empty string")
        if "resourceType" in criteria and not isinstance(criteria["resourceType"], str):
            errors.append("condition resourceType must be a string")
        if "params" in criteria and not isinstance(criteria["params"], (dict, list)):
            errors.append("condition params must be an object or array")
        return errors

    return ["criteria must be one of allOf / anyOf / not / a condition with rule"]


# -------------------------------------------------------------------- 规则库

# 规则：(resource, params) -> bool。官方 catalog 插件对应 isEntityOwner / hasAnnotation 等。
Rule = Callable[[Dict[str, Any], Any], bool]


def _rule_is_entity_owner(resource: Dict[str, Any], params: Any) -> bool:
    claims = set((params or {}).get("claims", []))
    if not claims:  # 空 claims：任何人都不是 owner（匿名/未解析身份）
        return False
    return bool(claims & set(resource.get("owners", [])))


def _rule_has_annotation(resource: Dict[str, Any], params: Any) -> bool:
    key = (params or {}).get("key")
    value = (params or {}).get("value")
    annotations = resource.get("annotations", {})
    if key not in annotations:
        return False
    if value is None:
        return True
    return annotations[key] == value


def _rule_is_entity_kind(resource: Dict[str, Any], params: Any) -> bool:
    kinds = set((params or {}).get("kinds", []))
    return resource.get("kind") in kinds


CATALOG_RULES: Dict[str, Rule] = {
    "IS_ENTITY_OWNER": _rule_is_entity_owner,
    "HAS_ANNOTATION": _rule_has_annotation,
    "IS_ENTITY_KIND": _rule_is_entity_kind,
}


def eval_criteria(criteria: Any, resource: Dict[str, Any],
                  rules: Dict[str, Rule] = CATALOG_RULES) -> bool:
    """对资源求值条件树。

    语义取自官方「Permission requests that result in a conditional decision are allowed
    if all of the provided conditions evaluate to be true」。
    """
    if "allOf" in criteria:
        return all(eval_criteria(c, resource, rules) for c in criteria["allOf"])
    if "anyOf" in criteria:
        return any(eval_criteria(c, resource, rules) for c in criteria["anyOf"])
    if "not" in criteria:
        return not eval_criteria(criteria["not"], resource, rules)
    rule = rules.get(criteria["rule"])
    if rule is None:
        raise PermissionError(f"unknown rule {criteria['rule']!r}")
    return bool(rule(resource, criteria.get("params")))


# -------------------------------------------------------------------- 授权

@dataclass
class PolicyQuery:
    permission: Permission
    resource_ref: Optional[str] = None


@dataclass
class PolicyContext:
    """策略可用的上下文：用户的归属声明（ownershipEntityRefs）。"""
    claims: Sequence[str] = field(default_factory=tuple)


def default_policy(query: PolicyQuery, ctx: PolicyContext) -> Dict[str, Any]:
    """官方脚手架生成的默认策略：无条件 allow-all。"""
    return {"result": ALLOW}


def authorize(query: PolicyQuery, ctx: PolicyContext, resource: Dict[str, Any],
              policy: Callable[[PolicyQuery, PolicyContext], Dict[str, Any]] = default_policy
              ) -> str:
    """决策 + 执行：CONDITIONAL 由插件（这里即本函数）求值后收敛成 ALLOW/DENY。"""
    decision = policy(query, ctx)
    errors = validate_decision(decision)
    if errors:
        raise PermissionError(f"invalid decision: {errors}")
    if decision["result"] != CONDITIONAL:
        return decision["result"]
    if decision["resourceType"] != query.permission.resource_type:
        raise PermissionError("conditional decision resourceType mismatches permission")
    return ALLOW if eval_criteria(decision["conditions"], resource) else DENY


def owner_only_policy(resource_type: str, plugin_id: str = "catalog"
                      ) -> Callable[[PolicyQuery, PolicyContext], Dict[str, Any]]:
    """「只有 owner 能操作」的条件策略：把判定委托回 catalog 插件。"""
    def policy(query: PolicyQuery, ctx: PolicyContext) -> Dict[str, Any]:
        if not is_resource_permission(query.permission, resource_type):
            return {"result": ALLOW}
        return {
            "result": CONDITIONAL,
            "pluginId": plugin_id,
            "resourceType": resource_type,
            "conditions": {
                "rule": "IS_ENTITY_OWNER",
                "params": {"claims": list(ctx.claims)},
            },
        }
    return policy
