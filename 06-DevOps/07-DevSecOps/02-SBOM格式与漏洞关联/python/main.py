#!/usr/bin/env python3
"""CycloneDX 与 SPDX 两种 SBOM 的图模型、VEX 判定与跨格式关联。

本文件只做三件事，且每件事都对应规范里的一处具体条文：

1. ``vers`` 版本区间判定的一个子集（CycloneDX ``affects[].versions[].range``）
2. CycloneDX 的 VEX 判定：``bom-ref`` → ``affects`` → ``status`` / ``analysis.state``
3. 两种格式的依赖图互转：CycloneDX ``dependencies`` ↔ SPDX ``DEPENDS_ON``

PURL 的解析与规范化见 ``purl.py``。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from purl import canonical_purl, purl_key

# --------------------------------------------------------------------------
# 1. vers 区间判定（子集）
# --------------------------------------------------------------------------
#
# 口径说明（官方未在本 demo 覆盖的范围内给实现，此处是模型选的读法）：
# CycloneDX 的 ``range`` 字段指向 package-url/vers-spec，语法形如
#   vers:<type>/<constraint>[|<constraint>]*
# 本 demo 只实现 ``>=`` ``>`` ``<=`` ``<`` ``!=`` 与裸版本六种约束，
# 多个约束用 '|' 分隔并取**合取**（都要满足）。未支持的写法会抛 ValueError，
# 不做静默放行。


def _parse_version(v: str) -> Tuple[int, ...]:
    parts = []
    for seg in v.split("."):
        m = "".join(ch for ch in seg if ch.isdigit())
        parts.append(int(m) if m else 0)
    return tuple(parts)


def _cmp(a: str, b: str) -> int:
    ta, tb = _parse_version(a), _parse_version(b)
    n = max(len(ta), len(tb))
    ta = ta + (0,) * (n - len(ta))
    tb = tb + (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)


def vers_matches(range_str: str, version: str) -> bool:
    """判定 version 是否落在 vers 区间内（本 demo 支持的子集）。"""
    if not range_str.startswith("vers:"):
        raise ValueError("not a vers range: %r" % range_str)
    body = range_str[len("vers:"):]
    if "/" not in body:
        raise ValueError("missing vers type: %r" % range_str)
    _, constraints = body.split("/", 1)
    for c in constraints.split("|"):
        c = c.strip()
        if not c:
            continue
        for op in (">=", "<=", "!=", ">", "<", "=="):
            if c.startswith(op):
                operand = c[len(op):]
                r = _cmp(version, operand)
                if op == ">=" and not r >= 0:
                    return False
                if op == ">" and not r > 0:
                    return False
                if op == "<=" and not r <= 0:
                    return False
                if op == "<" and not r < 0:
                    return False
                if op == "!=" and r == 0:
                    return False
                if op == "==" and r != 0:
                    return False
                break
        else:
            # 裸版本：精确相等
            if _cmp(version, c) != 0:
                return False
    return True


# --------------------------------------------------------------------------
# 2. CycloneDX 的组件 / 依赖 / 漏洞
# --------------------------------------------------------------------------


class Component:
    """CycloneDX component：required 只有 type 与 name（schema 实读）。"""

    def __init__(self, bom_ref: str, type_: str, name: str,
                 version: str = "", purl: str = ""):
        self.bom_ref = bom_ref
        self.type = type_
        self.name = name
        self.version = version
        self.purl = purl


class Affect:
    """vulnerability.affects[] 的一项。

    ``ref`` 可以是 bom-ref，也可以是 BOM-Link（``urn:cdx:...``）。
    ``versions[]`` 每项必须给出 ``version`` 或 ``range`` 之一（oneOf），
    ``status`` 缺省为 ``affected``。
    """

    def __init__(self, ref: str, versions: List[Dict[str, str]]):
        self.ref = ref
        self.versions = versions


class Vulnerability:
    """CycloneDX vulnerability：analysis.state / justification 的取值来自 schema 枚举。"""

    STATES = ("resolved", "resolved_with_pedigree", "exploitable",
              "in_triage", "false_positive", "not_affected")
    JUSTIFICATIONS = ("code_not_present", "code_not_reachable",
                      "requires_configuration", "requires_dependency",
                      "requires_environment", "protected_by_compiler",
                      "protected_at_runtime", "protected_at_perimeter",
                      "protected_by_mitigating_control")

    def __init__(self, vuln_id: str, affects: List[Affect],
                 state: Optional[str] = None,
                 justification: Optional[str] = None):
        self.id = vuln_id
        self.affects = affects
        self.state = state
        self.justification = justification


class Bom:
    """CycloneDX BOM 的最小子集。"""

    def __init__(self, components: List[Component],
                 dependencies: Optional[Dict[str, List[str]]] = None,
                 vulnerabilities: Optional[List[Vulnerability]] = None):
        self.components = {c.bom_ref: c for c in components}
        self.dependencies = dependencies or {}
        self.vulnerabilities = vulnerabilities or []

    def validate(self) -> List[str]:
        """按 schema 的 required / enum 做最小校验，返回错误列表。"""
        errs: List[str] = []
        for ref, c in self.components.items():
            if not c.type or not c.name:
                errs.append("component %s: type 与 name 是 required" % ref)
        for v in self.vulnerabilities:
            if v.state is not None and v.state not in Vulnerability.STATES:
                errs.append("vuln %s: analysis.state 不在枚举内" % v.id)
            if v.justification is not None and v.justification not in Vulnerability.JUSTIFICATIONS:
                errs.append("vuln %s: justification 不在枚举内" % v.id)
            for a in v.affects:
                for item in a.versions:
                    has_v = "version" in item
                    has_r = "range" in item
                    if has_v == has_r:      # oneOf：恰好一个
                        errs.append("vuln %s: versions 项必须给出 version 或 range 之一" % v.id)
        return errs


def vex_status(bom: Bom, bom_ref: str, version: str) -> str:
    """判定某个组件版本在该 BOM 的 VEX 信息下的受影响状态。

    返回 ``affected`` / ``unaffected`` / ``unknown``。
    口径：按 vulnerabilities 顺序扫描，**首个能匹配的条目胜出**；
    条目内 ``status`` 缺省取 ``affected``。
    """
    for v in bom.vulnerabilities:
        for a in v.affects:
            if a.ref != bom_ref:
                continue
            for item in a.versions:
                hit = False
                if "version" in item:
                    hit = _cmp(version, item["version"]) == 0
                elif "range" in item:
                    hit = vers_matches(item["range"], version)
                if hit:
                    return item.get("status", "affected")
    return "unknown"


def dep_closure(bom: Bom, root: str) -> List[str]:
    """CycloneDX dependencies（ref -> dependsOn）的传递闭包，广度优先。"""
    seen, stack, order = set(), [root], []
    while stack:
        cur = stack.pop(0)
        for nxt in bom.dependencies.get(cur, []):
            if nxt not in seen:
                seen.add(nxt)
                order.append(nxt)
                stack.append(nxt)
    return order


# --------------------------------------------------------------------------
# 3. SPDX 侧：关系模型与外部引用
# --------------------------------------------------------------------------

SPDX_DEPENDS_ON = "DEPENDS_ON"
SPDX_DEPENDENCY_OF = "DEPENDENCY_OF"
SPDX_DESCRIBES = "DESCRIBES"
SPDX_CONTAINS = "CONTAINS"


class SpdxDoc:
    """SPDX 2.3 的最小子集：packages + relationships。"""

    ID_RE = None

    def __init__(self, document_id: str, packages: Dict[str, str],
                 relationships: List[Tuple[str, str, str]]):
        self.document_id = document_id
        self.packages = packages          # SPDXID -> purl（可为空串）
        self.relationships = relationships

    def validate(self) -> List[str]:
        errs: List[str] = []
        if not self.document_id.startswith("SPDXRef-"):
            errs.append("document SPDXID 必须以 SPDXRef- 开头")
        for sid in self.packages:
            if not sid.startswith("SPDXRef-"):
                errs.append("package SPDXID 必须以 SPDXRef- 开头: %s" % sid)
        # SPDX 2.3：文档含多于一个 package 时必须有 DESCRIBES / DESCRIBED_BY
        if len(self.packages) > 1:
            kinds = {r[1] for r in self.relationships}
            if SPDX_DESCRIBES not in kinds and "DESCRIBED_BY" not in kinds:
                errs.append("多于一个 package 时必须给出 DESCRIBES 或 DESCRIBED_BY")
        return errs

    def depends_on(self) -> List[Tuple[str, str]]:
        """把 DEPENDS_ON 与 DEPENDENCY_OF 归一化成 (from, to) 依赖边。"""
        edges: List[Tuple[str, str]] = []
        for a, kind, b in self.relationships:
            if kind == SPDX_DEPENDS_ON:
                edges.append((a, b))
            elif kind == SPDX_DEPENDENCY_OF:
                edges.append((b, a))      # A DEPENDENCY_OF B  ⇔  B DEPENDS_ON A
        return edges


def cdx_to_spdx_relationships(bom: Bom) -> List[Tuple[str, str, str]]:
    """CycloneDX 的 dependencies（ref -> dependsOn）转成 SPDX 的 DEPENDS_ON 边。"""
    out: List[Tuple[str, str, str]] = []
    for ref, deps in sorted(bom.dependencies.items()):
        for d in deps:
            out.append((ref, SPDX_DEPENDS_ON, d))
    return out


def cross_format_key(cdx_purl: str, spdx_purl: str) -> bool:
    """两种格式里同一个组件能否靠规范化后的 PURL 对齐。"""
    return purl_key(cdx_purl) == purl_key(spdx_purl)
