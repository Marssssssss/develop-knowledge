"""Backstage 软件目录实体模型：信封/元数据校验、实体引用解析、关系推导。

事实来源（本轮实读）：
- backstage.io《Descriptor Format of Catalog Entities》正文（name/namespace/tags/labels/
  annotations/links 的长度与字符集、relations 与 status 为只读、各 kind 的默认 kind 与
  生成的正向/反向关系类型）
- backstage/backstage `packages/catalog-model/src/kinds/relations.ts`（8 组 well-known
  关系对及其 fromKind/toKind 约束）
- backstage/backstage `packages/catalog-model/src/schema/EntityMeta.schema.json`（uid/etag
  只读、name 必填、links.url 必填、tags 元素 minLength 1）

口径说明：官方 JSON Schema 只约束了 minLength，不写字符集正则；字符集来自文档正文，
本 demo 依正文实现并保留 `strict=False` 的宽松通道以对照。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------- 字符集规则

NAME_RE = re.compile(r"^[a-zA-Z0-9]+(?:[-_.][a-zA-Z0-9]+)*$")
NAMESPACE_RE = re.compile(r"^[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*$")
TAG_RE = re.compile(r"^[a-z0-9:+#]+(?:-[a-z0-9:+#]+)*$")
# 标签/注解的键：可选前缀 <小写域名> + "/" + name，name 部分 [a-zA-Z0-9] 由 [-_.] 分隔
KEY_NAME_RE = re.compile(r"^[a-zA-Z0-9]+(?:[-_.][a-zA-Z0-9]+)*$")
DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")

MAX_NAME = 63          # name / namespace / tag / label name 部分的上限
MAX_KEY_PREFIX = 253   # 标签与注解的前缀（域名）上限

RESERVED_PREFIX = "backstage.io/"

CORE_KINDS = (
    "API", "Component", "Domain", "Group", "Location", "Resource", "System", "User",
)


def valid_name(value: str) -> bool:
    """metadata.name：1..63，[a-z0-9A-Z] 序列由单个 [-_.] 分隔（首尾必须是字母数字）。"""
    if not isinstance(value, str) or not value or len(value) > MAX_NAME:
        return False
    return bool(NAME_RE.match(value))


def valid_namespace(value: str) -> bool:
    """metadata.namespace：1..63，只允许 [a-zA-Z0-9] 与单个 '-' 分隔。

    与 name 的关键差别：namespace 的分隔符**只有 hyphen**，不含 '_' 与 '.'，且大小写不敏感
    （多数位置渲染成小写）。
    """
    if not isinstance(value, str) or not value or len(value) > MAX_NAME:
        return False
    return bool(NAMESPACE_RE.match(value))


def valid_tag(value: str) -> bool:
    """metadata.tags 元素：1..63，[a-z0-9:+#] 由 '-' 分隔（强制小写，含 ':' '+' '#'）。"""
    if not isinstance(value, str) or not value or len(value) > MAX_NAME:
        return False
    return bool(TAG_RE.match(value))


def valid_key(key: str) -> bool:
    """labels/annotations 的键：[<前缀>/]<name>。

    前缀必须整体是小写域名且 ≤253；name 部分 ≤63。
    """
    if not isinstance(key, str) or not key:
        return False
    if "/" in key:
        prefix, _, name = key.partition("/")
        if not prefix or len(prefix) > MAX_KEY_PREFIX:
            return False
        if not DOMAIN_RE.match(prefix):
            return False
    else:
        name = key
    if not name or len(name) > MAX_NAME:
        return False
    return bool(KEY_NAME_RE.match(name))


def valid_label_value(value: str) -> bool:
    """labels 的值：与 metadata.name 同一套限制（含 63 上限）。"""
    return valid_name(value)


def valid_annotation_value(value: str) -> bool:
    """annotations 的值：只要字符串，无长度上限（与 label 值形成对比）。"""
    return isinstance(value, str)


def is_reserved_key(key: str) -> bool:
    return key.startswith(RESERVED_PREFIX)


# ---------------------------------------------------------------- 实体引用

@dataclass(frozen=True)
class EntityRef:
    kind: str
    namespace: str
    name: str

    def __str__(self) -> str:  # 字符串形式始终写全 kind 与 namespace
        return f"{self.kind.lower()}:{self.namespace}/{self.name}"


class RefError(ValueError):
    pass


def parse_entity_ref(
    ref: str,
    default_kind: str,
    default_namespace: str = "default",
    valid_kinds: Sequence[str] = CORE_KINDS,
) -> EntityRef:
    """解析 `[<kind>:][<namespace>/]<name>`。

    - kind 缺失时取 `default_kind`；大小写不敏感，但输出统一成官方拼写（首字母大写）。
    - namespace 缺失时取 `default_namespace`，默认 "default"。
    - 注意：`<namespace>/<name>` 中若出现多个 '/'，按**最后一个**切分（name 不含 '/'）。
    """
    if not isinstance(ref, str) or not ref:
        raise RefError("entity reference must be a non-empty string")

    kind: Optional[str] = None
    rest = ref
    if ":" in rest:
        raw_kind, _, rest = rest.partition(":")
        if not raw_kind:
            raise RefError(f"empty kind in reference {ref!r}")
        kind = _canonical_kind(raw_kind, valid_kinds)

    namespace = default_namespace
    if "/" in rest:
        raw_ns, _, name = rest.rpartition("/")
        if not raw_ns or not name:
            raise RefError(f"malformed namespace/name in reference {ref!r}")
        if not valid_namespace(raw_ns):
            raise RefError(f"invalid namespace {raw_ns!r} in reference {ref!r}")
        namespace = raw_ns.lower()
    else:
        name = rest

    if not valid_name(name):
        raise RefError(f"invalid name {name!r} in reference {ref!r}")

    if kind is None:
        kind = _canonical_kind(default_kind, valid_kinds)
    return EntityRef(kind=kind, namespace=namespace, name=name)


def _canonical_kind(raw: str, valid_kinds: Sequence[str]) -> str:
    for k in valid_kinds:
        if k.lower() == raw.lower():
            return k
    raise RefError(f"unknown kind {raw!r}")


# ---------------------------------------------------------------- 关系模型

@dataclass(frozen=True)
class RelationPair:
    """来自 relations.ts 的一组对称关系（forward / reverse）。"""
    forward: str
    reverse: str
    from_kinds: Tuple[str, ...]
    to_kinds: Tuple[str, ...]


WELL_KNOWN_RELATIONS: Dict[str, RelationPair] = {
    "ownedBy": RelationPair("ownedBy", "ownerOf", CORE_KINDS, ("Group", "User")),
    "providesApi": RelationPair("providesApi", "apiProvidedBy", ("Component",), ("API",)),
    "consumesApi": RelationPair("consumesApi", "apiConsumedBy", ("Component",), ("API",)),
    "dependsOn": RelationPair(
        "dependsOn", "dependencyOf", ("Component", "Resource"), ("Component", "Resource")
    ),
    "parentOf": RelationPair("parentOf", "childOf", ("Group",), ("Group",)),
    "memberOf": RelationPair("memberOf", "hasMember", ("User",), ("Group",)),
    "partOf": RelationPair(
        "partOf", "hasPart", ("Component", "API", "Resource", "System", "Domain"),
        ("Component", "System", "Domain"),
    ),
}

# spec 字段 → (默认 kind, 关系对主键, 方向)。方向为 reverse 时，本字段在实体一侧写出的是
# 反向关系（例如 spec.dependencyOf 写出的是 `dependsOn` 对的反向 `dependencyOf`）。
SPEC_FIELDS: Dict[str, Tuple[str, str, str]] = {
    "owner": ("Group", "ownedBy", "forward"),
    "system": ("System", "partOf", "forward"),
    "subcomponentOf": ("Component", "partOf", "forward"),
    "providesApis": ("API", "providesApi", "forward"),
    "consumesApis": ("API", "consumesApi", "forward"),
    "dependsOn": ("Component", "dependsOn", "forward"),
    "dependencyOf": ("Component", "dependsOn", "reverse"),
    "memberOf": ("Group", "memberOf", "forward"),
    "parent": ("Group", "parentOf", "reverse"),
    "children": ("Group", "parentOf", "forward"),
    "domain": ("Domain", "partOf", "forward"),
}

READ_ONLY_ROOT_FIELDS = ("relations", "status", "uid", "etag")


@dataclass
class Entity:
    kind: str
    name: str
    namespace: str = "default"
    spec: Dict[str, object] = field(default_factory=dict)

    @property
    def ref(self) -> EntityRef:
        return EntityRef(kind=self.kind, namespace=self.namespace, name=self.name)


def _spec_refs(spec_value: object) -> List[str]:
    if isinstance(spec_value, str):
        return [spec_value]
    if isinstance(spec_value, (list, tuple)):
        return [v for v in spec_value if isinstance(v, str)]
    return []


def deduce_relations(entity: Entity) -> List[Tuple[EntityRef, str, EntityRef]]:
    """按 spec 字段推导关系，正向与反向成对产出。

    返回三元组 `(source, type, target)` 列表；反向关系挂在**目标实体**身上（真实 catalog
    由处理循环写入，这里按同一口径成对给出，便于验证对称性）。
    """
    out: List[Tuple[EntityRef, str, EntityRef]] = []
    for field_name, (default_kind, pair_key, direction) in SPEC_FIELDS.items():
        pair = WELL_KNOWN_RELATIONS[pair_key]
        source_type = pair.forward if direction == "forward" else pair.reverse
        target_type = pair.reverse if direction == "forward" else pair.forward
        for raw in _spec_refs(entity.spec.get(field_name)):
            target = parse_entity_ref(raw, default_kind=default_kind,
                                      default_namespace=entity.namespace)
            if direction == "forward":
                src_kinds, tgt_kinds = pair.from_kinds, pair.to_kinds
            else:  # 反向字段：实体落在关系对的 to 端
                src_kinds, tgt_kinds = pair.to_kinds, pair.from_kinds
            if entity.kind not in src_kinds or target.kind not in tgt_kinds:
                raise RefError(
                    f"relation {source_type} forbids {entity.kind} -> {target.kind}"
                )
            out.append((entity.ref, source_type, target))
            out.append((target, target_type, entity.ref))
    return out


def check_envelope(envelope: Dict[str, object]) -> List[str]:
    """校验信封与元数据，返回错误列表（空列表表示合法）。"""
    errors: List[str] = []

    if envelope.get("apiVersion") != "backstage.io/v1alpha1":
        errors.append("apiVersion must be backstage.io/v1alpha1")
    if "metadata" not in envelope:
        errors.append("metadata is required")
        return errors
    meta = envelope["metadata"]
    if not isinstance(meta, dict):
        errors.append("metadata must be an object")
        return errors
    if not valid_name(str(meta.get("name", ""))):
        errors.append("metadata.name invalid (1..63, [a-zA-Z0-9] separated by [-_.])")
    if "namespace" in meta and not valid_namespace(str(meta.get("namespace"))):
        errors.append("metadata.namespace invalid ([a-zA-Z0-9] separated by '-')")

    for f in READ_ONLY_ROOT_FIELDS:
        if f in envelope:
            errors.append(f"{f} is read-only and must not appear in a descriptor file")
        if f in meta and f in ("uid", "etag"):
            errors.append(f"metadata.{f} is read-only")

    for tag in meta.get("tags", []) or []:
        if not valid_tag(str(tag)):
            errors.append(f"invalid tag {tag!r}")

    for key, value in (meta.get("labels") or {}).items():
        if not valid_key(key):
            errors.append(f"invalid label key {key!r}")
        if not valid_label_value(str(value)):
            errors.append(f"invalid label value for {key!r}")

    for key, value in (meta.get("annotations") or {}).items():
        if not valid_key(key):
            errors.append(f"invalid annotation key {key!r}")
        if not valid_annotation_value(value):
            errors.append(f"annotation {key!r} value must be a string")

    for link in meta.get("links", []) or []:
        if not isinstance(link, dict) or not str(link.get("url", "")):
            errors.append("every link requires a url")

    return errors


def kinds_of(entities: Iterable[Entity]) -> Dict[str, int]:
    """统计各 kind 的实体数量（用于目录容量与孤儿检测的输入）。"""
    counts: Dict[str, int] = {}
    for e in entities:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    return counts
