"""Crossplane 组合式平台资源：XRD / Composition / Claim 的定义与选择。

事实来源（本轮实读，全部来自 crossplane/crossplane 官方源码）：
- `apis/apiextensions/v1/xrd_types.go`
  * `CompositeResourceScope` 三值：`Namespaced` / `Cluster` / `LegacyCluster`，**默认 LegacyCluster**
  * CEL 校验：`scope == 'LegacyCluster' || !has(self.claimNames)` —— 只有 LegacyCluster 能开 claim
  * CEL 校验：`scope == 'LegacyCluster' || !has(self.connectionSecretKeys)`
  * `group` 不可变且必须匹配 XRD 的名字（`<names.plural>.<group>`）
  * `names.plural` 必须全小写；`names.singular` 若存在也必须全小写
  * `DefaultCompositeDeletePolicy` 默认 `Background`；`DefaultCompositionUpdatePolicy` 默认 `Automatic`
  * `GetCompositeGroupVersionKind()`：循环里**没有 break**，`v` 被最后一个 `Referenceable`
    版本覆盖 → 多个 referenceable 版本时取**最后一个**
  * `OffersClaim()` 等价于 `Spec.ClaimNames != nil`
  * `GetClaimGroupVersionKind()`：不提供 claim 时返回**空** GVK
- `apis/apiextensions/v1/composition_types.go`
  * `Mode` 枚举只有 `Pipeline`，默认 `Pipeline`
  * `Pipeline` 的 `MinItems=1`、`MaxItems=99`
  * `CompositeTypeRef` 带 `XValidation: self == oldSelf`（不可变）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

SCOPES = ("LegacyCluster", "Namespaced", "Cluster")
DEFAULT_SCOPE = "LegacyCluster"
DEFAULT_DELETE_POLICY = "Background"
DEFAULT_UPDATE_POLICY = "Automatic"
COMPOSITION_MODES = ("Pipeline",)
PIPELINE_MIN_ITEMS = 1
PIPELINE_MAX_ITEMS = 99


class CrossplaneError(ValueError):
    pass


@dataclass
class Names:
    kind: str
    plural: str
    singular: Optional[str] = None


@dataclass
class Version:
    name: str
    referenceable: bool = False
    served: bool = True


@dataclass
class Xrd:
    """CompositeResourceDefinition：平台团队定义的新 API。"""
    name: str                       # metadata.name，应等于 <plural>.<group>
    group: str
    names: Names
    versions: Sequence[Version]
    scope: str = DEFAULT_SCOPE
    claim_names: Optional[Names] = None
    connection_secret_keys: Optional[List[str]] = None
    default_composition_ref: Optional[str] = None
    enforced_composition_ref: Optional[str] = None
    default_composite_delete_policy: str = DEFAULT_DELETE_POLICY
    default_composition_update_policy: str = DEFAULT_UPDATE_POLICY

    def offers_claim(self) -> bool:
        return self.claim_names is not None

    def composite_gvk(self) -> Tuple[str, str, str]:
        """等价于官方 GetCompositeGroupVersionKind：取**最后一个** referenceable 版本。"""
        version = ""
        for v in self.versions:          # 官方实现没有 break，最后一个覆盖前面的
            if v.referenceable:
                version = v.name
        return (self.group, version, self.names.kind)

    def claim_gvk(self) -> Tuple[str, str, str]:
        if not self.offers_claim():
            return ("", "", "")
        group, version, _ = self.composite_gvk()
        return (group, version, self.claim_names.kind)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.scope not in SCOPES:
            errors.append(f"scope must be one of {SCOPES}")
        if self.name != f"{self.names.plural}.{self.group}":
            errors.append("metadata.name must equal <names.plural>.<group>")
        if self.names.plural != self.names.plural.lower():
            errors.append("names.plural must be lowercase")
        if self.names.singular and self.names.singular != self.names.singular.lower():
            errors.append("names.singular must be lowercase")
        # CEL：只有 LegacyCluster 允许 claim 与 connection secret
        if self.scope != "LegacyCluster" and self.claim_names is not None:
            errors.append("Only LegacyCluster composite resources can offer claims")
        if self.scope != "LegacyCluster" and self.connection_secret_keys:
            errors.append("Only LegacyCluster composite resources support connection secrets")
        if not self.versions:
            errors.append("at least one version is required")
        if not any(v.referenceable for v in self.versions):
            errors.append("at least one version must be referenceable")
        return errors


@dataclass
class PipelineStep:
    step: str
    function_ref: str


@dataclass
class Composition:
    """Composition：把 XRD 声明的 API 落到一组被管理资源（或函数流水线）上。"""
    name: str
    composite_type_ref: Tuple[str, str]     # (apiVersion, kind)
    pipeline: List[PipelineStep] = field(default_factory=list)
    mode: str = "Pipeline"
    write_connection_secrets_to_namespace: Optional[str] = None
    labels: Dict[str, str] = field(default_factory=dict)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.mode not in COMPOSITION_MODES:
            errors.append(f"mode must be one of {COMPOSITION_MODES}")
        if not (PIPELINE_MIN_ITEMS <= len(self.pipeline) <= PIPELINE_MAX_ITEMS):
            errors.append(
                f"pipeline length must be in [{PIPELINE_MIN_ITEMS}, {PIPELINE_MAX_ITEMS}]"
            )
        steps = [s.step for s in self.pipeline]
        if len(steps) != len(set(steps)):
            errors.append("pipeline step names must be unique (listMapKey=step)")
        if not self.composite_type_ref[0] or not self.composite_type_ref[1]:
            errors.append("compositeTypeRef requires both apiVersion and kind")
        return errors


@dataclass
class Composite:
    """一个复合资源实例（XR 或 Claim 背后的对象）。"""
    name: str
    composition_ref: Optional[str] = None
    composition_selector: Optional[Dict[str, str]] = None
    composition_revision_selector: Optional[Dict[str, str]] = None
    composition_update_policy: Optional[str] = None
    composite_delete_policy: Optional[str] = None


def select_composition(xrd: Xrd, composite: Composite,
                       compositions: Sequence[Composition]) -> Composition:
    """选择组合：enforced > selector > 实例上的 ref > XRD 默认。

    选择器的匹配结果必须唯一：0 个报错，多于 1 个也报错（避免静默选到第一个）。
    """
    if xrd.enforced_composition_ref:
        for c in compositions:
            if c.name == xrd.enforced_composition_ref:
                return c
        raise CrossplaneError(
            f"enforced composition {xrd.enforced_composition_ref!r} not found")

    if composite.composition_selector:
        matched = [c for c in compositions
                   if all(c.labels.get(k) == v
                          for k, v in composite.composition_selector.items())]
        if len(matched) == 0:
            raise CrossplaneError("compositionSelector matched no composition")
        if len(matched) > 1:
            raise CrossplaneError(
                f"compositionSelector matched {len(matched)} compositions: "
                f"{[c.name for c in matched]}")
        return matched[0]

    if composite.composition_ref:
        for c in compositions:
            if c.name == composite.composition_ref:
                return c
        raise CrossplaneError(f"compositionRef {composite.composition_ref!r} not found")

    if xrd.default_composition_ref:
        for c in compositions:
            if c.name == xrd.default_composition_ref:
                return c
        raise CrossplaneError(
            f"default composition {xrd.default_composition_ref!r} not found")

    raise CrossplaneError("no composition could be selected")


def filter_connection_secret(allowed: Optional[Sequence[str]],
                             produced: Dict[str, str]) -> Dict[str, str]:
    """connectionSecretKeys 是**白名单**：为空表示全部发布，非空则过滤掉名单外的键。"""
    if not allowed:
        return dict(produced)
    return {k: v for k, v in produced.items() if k in set(allowed)}


def resolve_policies(xrd: Xrd, composite: Composite) -> Tuple[str, str]:
    """实例上没写策略时，回落到 XRD 的默认值（Background / Automatic）。"""
    delete_policy = composite.composite_delete_policy or xrd.default_composite_delete_policy
    update_policy = composite.composition_update_policy or xrd.default_composition_update_policy
    return delete_policy, update_policy
