"""KubeVela OAM：Application / ComponentDefinition / DefinitionRevision 的模型约束。

事实来源（本轮实读，全部来自 kubevela/kubevela 官方源码）：
- `apis/core.oam.dev/v1beta1/application_types.go`
  * `ApplicationSpec.Components` 必填（json 标签无 omitempty）；`Sources` / `Policies` / `Workflow` 可选
  * 注释原文：「If workflow is specified, Vela won't apply any resource, but provide rendered
    output in AppRevision」——**有 workflow 时不直接下发资源**
  * `AppPolicy{Name?, Type, Properties?}`、`Workflow{Ref?, Mode?, Steps?}`
  * `ApplicationSourceStatusPolicy.ExposeConsumedValues`：注释「Unset means expose」
  * `ApplicationSource.AutoUpdate`：未设置时看 feature gate；`publishVersion` pin 双向压过它
- `apis/core.oam.dev/common/types.go`
  * `Schematic.CUE.Template` 是**必填**（`json:"template"`，无 omitempty）
  * `Schematic.Terraform.Type` 枚举 `hcl;json;remote`，默认 `hcl`
  * `DefinitionReference.Version` 注释：「by default it will use the first one if not specified」
  * `ApplicationComponent.Traits` 是**数组**（注释：type must be array to keep the order）
  * `ApplicationComponent.ReplicaKey` 的 json 标签是 `"-"`——**不会出现在 YAML 里**
  * `Revision{Name, Revision int64, RevisionHash?}`；`DefinitionType` 枚举 5 个值
  * `ParameterValueType` 三值：string / number / boolean
- `apis/core.oam.dev/v1beta1/definitionrevision_types.go`
  * `DefinitionRevisionSpec` 同时有 5 个快照字段（Component/Trait/Policy/WorkflowStep/Source），
    实际只应填与 `definitionType` 对应的那一个
- `apis/core.oam.dev/v1beta1/componentdefinition_types.go`
  * `ComponentDefinitionSpec` 的 `Workload` 必填；`Restrictions` 非空会覆盖
    `definition.oam.dev/restrict-namespaces` 注解
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFINITION_TYPES = ("Component", "Trait", "Policy", "WorkflowStep", "Source")
TERRAFORM_TYPES = ("hcl", "json", "remote")
DEFAULT_TERRAFORM_TYPE = "hcl"
PARAMETER_TYPES = ("string", "number", "boolean")


class OamError(ValueError):
    pass


# ------------------------------------------------------------------ 参数类型

def parameter_value_type(value: Any) -> str:
    """按 ParameterValueType 三值归类；布尔必须排在整数之前判断。"""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    raise OamError(f"unsupported parameter value type: {type(value).__name__}")


# -------------------------------------------------------------------- 原理图

@dataclass
class Cue:
    template: str

    def validate(self) -> List[str]:
        return ["schematic.cue.template is required"] if not self.template else []


@dataclass
class Terraform:
    configuration: str
    type: str = DEFAULT_TERRAFORM_TYPE

    def validate(self) -> List[str]:
        errors = []
        if not self.configuration:
            errors.append("schematic.terraform.configuration is required")
        if self.type not in TERRAFORM_TYPES:
            errors.append(f"schematic.terraform.type must be one of {TERRAFORM_TYPES}")
        return errors


@dataclass
class Schematic:
    cue: Optional[Cue] = None
    terraform: Optional[Terraform] = None

    def validate(self) -> List[str]:
        if self.cue is None and self.terraform is None:
            return ["schematic must define at least one of cue / terraform"]
        errors: List[str] = []
        if self.cue is not None:
            errors.extend(self.cue.validate())
        if self.terraform is not None:
            errors.extend(self.terraform.validate())
        return errors


# ------------------------------------------------------------------ 应用模型

@dataclass
class Trait:
    type: str
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Component:
    name: str
    type: str
    properties: Dict[str, Any] = field(default_factory=dict)
    traits: List[Trait] = field(default_factory=list)   # 数组：顺序敏感
    depends_on: List[str] = field(default_factory=list)
    scopes: Dict[str, str] = field(default_factory=dict)
    external_revision: Optional[str] = None
    replica_key: str = ""                                # json:"-"，不进 YAML

    def to_manifest(self) -> Dict[str, Any]:
        """序列化成 Application 的 component 片段（ReplicaKey 不出现）。"""
        out: Dict[str, Any] = {"name": self.name, "type": self.type}
        if self.properties:
            out["properties"] = self.properties
        if self.traits:
            out["traits"] = [{"type": t.type, **({"properties": t.properties}
                                                 if t.properties else {})}
                             for t in self.traits]
        if self.depends_on:
            out["dependsOn"] = self.depends_on
        if self.scopes:
            out["scopes"] = self.scopes
        if self.external_revision:
            out["externalRevision"] = self.external_revision
        return out


@dataclass
class Policy:
    type: str
    name: str = ""
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Workflow:
    ref: str = ""
    steps: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Application:
    components: List[Component]
    policies: List[Policy] = field(default_factory=list)
    workflow: Optional[Workflow] = None
    sources: List[Dict[str, Any]] = field(default_factory=list)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if not self.components:
            errors.append("spec.components is required")
        names = [c.name for c in self.components]
        if len(names) != len(set(names)):
            errors.append("component names must be unique")
        for c in self.components:
            if not c.name or not c.type:
                errors.append("each component requires name and type")
            for dep in c.depends_on:
                if dep not in names or dep == c.name:
                    errors.append(f"component {c.name!r} dependsOn unknown {dep!r}")
        for p in self.policies:
            if not p.type:
                errors.append("each policy requires type")
        return errors

    @property
    def has_workflow(self) -> bool:
        return self.workflow is not None

    def render(self) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """有 workflow 时**不下发**资源，只产出 AppRevision 中的渲染结果。"""
        resources = [c.to_manifest() for c in self.components]
        revision = {"components": resources,
                    "policies": [{"type": p.type, "name": p.name} for p in self.policies]}
        if self.has_workflow:
            revision["workflow"] = {"ref": self.workflow.ref,
                                    "steps": self.workflow.steps}
            return [], revision
        return resources, revision


# ------------------------------------------------------------ 定义引用与修订

@dataclass
class DefinitionReference:
    name: str
    version: str = ""

    def resolve_version(self, available: Sequence[str]) -> str:
        """注释口径：未指定 version 时用**第一个**可用版本。"""
        if self.version:
            if self.version not in available:
                raise OamError(f"version {self.version!r} not served by {self.name!r}")
            return self.version
        if not available:
            raise OamError(f"definition {self.name!r} serves no version")
        return available[0]


def revision_hash(spec: Dict[str, Any]) -> str:
    """计算 revisionHash。

    口径说明：官方算法本轮未读，这里用「规范化 JSON 的 sha256 前 16 位」代替，
    只用于「spec 变了 hash 就该变」这类判等断言，不与线上值比对。
    """
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class DefinitionRevision:
    name: str
    revision: int
    definition_type: str
    snapshot_field: str
    spec: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.definition_type not in DEFINITION_TYPES:
            errors.append(f"definitionType must be one of {DEFINITION_TYPES}")
        expected = f"{self.definition_type[0].lower()}{self.definition_type[1:]}Definition"
        if self.snapshot_field != expected:
            errors.append(f"{self.definition_type} revisions must fill {expected!r}, "
                          f"got {self.snapshot_field!r}")
        if self.revision < 1:
            errors.append("revision must be >= 1")
        return errors

    @property
    def hash(self) -> str:
        return revision_hash(self.spec)


def next_revision(previous: Optional[DefinitionRevision],
                  spec: Dict[str, Any]) -> Tuple[int, str]:
    """spec 变化才递增 revision；未变则沿用旧 revision 与 hash。"""
    new_hash = revision_hash(spec)
    if previous is not None and previous.hash == new_hash:
        return previous.revision, new_hash
    return (1 if previous is None else previous.revision + 1), new_hash


# ------------------------------------------------------------ 源状态可见性

def expose_source_values(policy: Optional[Dict[str, Any]],
                         consumed: Dict[str, Any]) -> Dict[str, Any]:
    """`ExposeConsumedValues` 未设置即暴露；`maskPaths` 按点号路径额外打码。"""
    mask: List[str] = (policy or {}).get("maskPaths", []) or []
    if policy is None or policy.get("exposeConsumedValues") is None:
        exposed = dict(consumed)          # Unset means expose
    elif policy.get("exposeConsumedValues") is False:
        exposed = {k: "***" for k in consumed}
    else:
        exposed = dict(consumed)
    for path in mask:
        head, _, rest = path.partition(".")
        if head in exposed:
            exposed[head] = "***" if not rest else _mask_nested(exposed[head], rest)
    return exposed


def _mask_nested(node: Any, rest: str) -> Any:
    head, _, tail = rest.partition(".")
    if not isinstance(node, dict) or head not in node:
        return node
    node = dict(node)
    node[head] = "***" if not tail else _mask_nested(node[head], tail)
    return node
