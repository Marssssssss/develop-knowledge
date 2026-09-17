"""Argo CD diff 定制与去噪(可执行)。配套模块 ``argocd_sync.py`` 覆盖同步/自愈/prune。

权威依据: argo-cd.readthedocs.io «Diff Customization»(user-guide/diffing/)

核心事实(全部有原文支撑):
1. 忽略规则四元组 ``group / kind / name / namespace`` 是**与**关系;``group`` 可以写
   ``*``。**core 组的 group 是空串 ``""``, 不是 ``v1``**。
2. ``status`` 字段默认被整体忽略(``ignoreResourceStatusField`` 默认 ``all``), 可设
   ``crd`` / ``none`` 改变口径。
3. ``jsonPointers`` 走 RFC6902 JSON Pointer, 转义符 ``~1`` -> ``/``、``~0`` -> ``~``。
4. ``jqPathExpressions`` 是真 jq(gojq);本 demo 实现一个可解释子集: 多级路径、
   ``[]`` / ``[]?`` 数组展开、``| select(.k == "v")`` 过滤。**数组必须显式写 ``[]``
   才会展开**, 省略时路径直接落空。
5. ``managedFieldsManagers`` 按字段所有权忽略(如 HPA 拥有 ``/spec/replicas``), 用来
   消除"双方都在写同一字段"造成的噪声。
6. ``ignoreDifferences`` 的 ``knownTypeFields`` + ``core/Quantity`` 规整: ``100m`` 与
   ``0.1`` 是同一个量, 不规整就是假漂移。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

from argocd_sync import ArgoError


# ------------------------------------------------------------------ diff 定制

def unescape_pointer_token(tok: str) -> str:
    """RFC6902: ``~1`` -> ``/``、``~0`` -> ``~``(必须按此顺序)。"""
    return tok.replace("~1", "/").replace("~0", "~")


def delete_pointer(obj, pointer: str):
    """按 JSON Pointer 就地删除(不存在则静默返回)。"""
    toks = [unescape_pointer_token(t) for t in pointer.split("/")[1:]]
    cur = obj
    for t in toks[:-1]:
        if isinstance(cur, dict) and t in cur:
            cur = cur[t]
        elif isinstance(cur, list) and t.isdigit() and int(t) < len(cur):
            cur = cur[int(t)]
        else:
            return obj
    last = toks[-1]
    if isinstance(cur, dict):
        cur.pop(last, None)
    elif isinstance(cur, list):
        for i, item in enumerate(cur):
            if isinstance(item, dict) and item.get("name") == last:
                cur.pop(i)
                break
    return obj


def parse_jq_path(expr: str):
    """本 demo 支持的 jq 子集: ``.a.b``、``.a[].b``、``.a[].b | select(.k == "v")``。

    返回 ``(parts, wildcard_index, select)`` —— ``wildcard_index`` 是该段在 ``parts``
    中的下标(``[]`` / ``[]?`` 可以出现在任意一层, 不是只能在末尾), 无通配则为 ``None``。
    """
    expr = expr.strip()
    select = None
    if "|" in expr:
        head, tail = expr.split("|", 1)
        m = re.match(r'\s*select\(\s*\.([A-Za-z0-9_]+)\s*==\s*"([^"]*)"\s*\)\s*$', tail)
        if not m:
            raise ArgoError("不支持的 select 子句: %s" % tail)
        select = (m.group(1), m.group(2))
        expr = head.strip()
    if not expr.startswith("."):
        raise ArgoError("jq 路径必须以 . 开头: %s" % expr)
    parts, wildcard = [], None
    for seg in expr[1:].split("."):
        if not seg:
            continue
        if seg.endswith("[]?"):
            wildcard = len(parts)
            parts.append(seg[:-3])
        elif seg.endswith("[]"):
            wildcard = len(parts)
            parts.append(seg[:-2])
        else:
            parts.append(seg)
    return parts, wildcard, select


def apply_jq_path(obj, expr: str):
    """按 jq 子集就地删除匹配项;返回被处理的对象。

    ``[]`` / ``[]?`` 出现在**任意一层**时, 都把后半段路径分发到该数组的每个元素上;
    只有显式写 ``[]`` 才迭代数组 —— 省略 ``[]`` 时 jq 不会自动展开数组, 路径直接落空
    (这就是 ``.webhooks.clientConfig`` 与 ``.webhooks[].clientConfig`` 的区别)。
    通配落在最后一层时, 语义是"过滤数组元素本身"(配合 ``select`` 即删除匹配项)。
    """
    parts, wild, select = parse_jq_path(expr)
    if wild is None:
        cur = obj
        for p in parts[:-1]:
            if not isinstance(cur, dict) or p not in cur:
                return obj
            cur = cur[p]
        if isinstance(cur, dict):
            cur.pop(parts[-1], None)
        return obj

    node = obj
    for p in parts[:wild]:
        if not isinstance(node, dict) or p not in node:
            return obj
        node = node[p]
    if not isinstance(node, dict):
        return obj
    arr = node.get(parts[wild])
    if not isinstance(arr, list):
        return obj
    rest = parts[wild + 1:]
    if rest:
        for item in arr:
            _delete_rest(item, rest)
        return obj
    keep = []
    for item in arr:
        if select and isinstance(item, dict) and item.get(select[0]) == select[1]:
            continue
        keep.append(item)
    node[parts[wild]] = keep
    return obj


def _delete_rest(node, rest: list):
    """在单个数组元素内部删除 ``rest`` 指定的叶子字段(元素不是对象时静默跳过)。"""
    cur = node
    for p in rest[:-1]:
        if not isinstance(cur, dict) or p not in cur:
            return
        cur = cur[p]
    if isinstance(cur, dict):
        cur.pop(rest[-1], None)


def ignore_differences(live: dict, target: dict, rules: list, ignore_status: str = "all",
                       kind: str = None, aggregated_roles_ignored: bool = False):
    """返回去噪后的 ``(live, target)`` 副本。

    ``ignore_status``: ``all``(默认, 所有资源忽略 status 字段) / ``crd`` / ``none``。
    ``rules``: [{group, kind, name, namespace, jsonPointers, jqPathExpressions,
    managedFieldsManagers}] —— 只有 group/kind(/name/namespace) 匹配的规则才生效。
    ``kind`` 省略时取 live 对象自己的 ``kind``。
    """
    live, target = copy.deepcopy(live), copy.deepcopy(target)
    kind = kind or live.get("kind", "")
    api = live.get("apiVersion", "")
    group = api.split("/")[0] if "/" in api else ""   # core 组的 group 是空串
    for r in rules:
        if r.get("group", "*") not in ("*", group):
            continue
        if r.get("kind", "*") not in ("*", kind):
            continue
        if r.get("name") and r["name"] != live.get("metadata", {}).get("name"):
            continue
        if r.get("namespace") and r["namespace"] != live.get("metadata", {}).get("namespace",
                                                                                "default"):
            continue
        for ptr in r.get("jsonPointers", []) or []:
            delete_pointer(live, ptr)
            delete_pointer(target, ptr)
        for expr in r.get("jqPathExpressions", []) or []:
            apply_jq_path(live, expr)
            apply_jq_path(target, expr)
        for mgr in r.get("managedFieldsManagers", []) or []:
            for ptr in ownership_of(mgr, kind):
                delete_pointer(live, ptr)
                delete_pointer(target, ptr)
    if should_ignore_status(ignore_status, kind):
        live.pop("status", None)
        target.pop("status", None)
    return live, target


def ownership_of(manager: str, kind: str) -> list:
    """本 demo 口径: 用一张静态表模拟 ``metadata.managedFields`` 的字段所有权。"""
    table = {
        "kube-controller-manager": {"/spec/replicas": ["Deployment", "ReplicaSet"],
                                    "/spec/template/spec/containers/0/resources": ["Deployment"]},
        "horizontal-pod-autoscaler": {"/spec/replicas": ["Deployment"]},
    }
    return sorted(p for p, kinds in table.get(manager, {}).items() if kind in kinds)


def should_ignore_status(mode: str, kind: str) -> bool:
    if mode == "none":
        return False
    if mode == "all":
        return True
    if mode == "crd":
        return kind in ("CustomResourceDefinition",)
    raise ArgoError("ignoreResourceStatusField 只能是 crd/all/none")


def is_out_of_sync(live: dict, target: dict, rules=None, ignore_status="all",
                   kind: str = None) -> bool:
    a, b = ignore_differences(live, target, rules or [], ignore_status, kind)
    return json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)


# ------------------------------------------------------------------ 已知类型规整

def _is_decimal(s: str) -> bool:
    """只认 ``[0-9]+(\\.[0-9]*)?`` —— 与 Go 版口径一致, 不接受正负号与科学计数。"""
    if not s:
        return False
    seen_dot = False
    for ch in s:
        if "0" <= ch <= "9":
            continue
        if ch == "." and not seen_dot:
            seen_dot = True
            continue
        return False
    return True


def canonicalize_quantity(v):
    """``core/Quantity`` 的两种等价写法: ``100m`` 与 ``0.1``。

    只规整十进制写法;``1Gi`` / ``1e3`` 这类本 demo 不展开, 原样返回并标注
    (官方 Quantity 支持这些后缀, 但规整它们需要完整实现 Quantity 的解析规则)。
    """
    if not isinstance(v, str):
        return v
    if v.endswith("m") and _is_decimal(v[:-1]):
        return float(v[:-1]) / 1000.0
    if _is_decimal(v):
        return float(v)
    return v


KNOWN_TYPE_PATHS = {
    "argoproj.io/Rollout": {"spec.template.spec": "core/v1/PodSpec"},
}


def canonicalize_known_types(obj, group_kind: str, field_path: str):
    """把指定字段按 Kubernetes 内建类型规整, 消除自定义 marshaler 造成的假漂移。"""
    if KNOWN_TYPE_PATHS.get(group_kind, {}).get(field_path) != "core/v1/PodSpec":
        raise ArgoError("未登记的 knownTypeFields: %s %s" % (group_kind, field_path))
    cur = obj
    for p in field_path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return obj
        cur = cur[p]
    containers = cur.get("containers", []) if isinstance(cur, dict) else []
    for c in containers:
        req = c.get("resources", {}).get("requests", {})
        for k in list(req):
            req[k] = canonicalize_quantity(req[k])
    return obj


def content_digest(data: bytes, algorithm: str = "sha256") -> str:
    return "%s:%s" % (algorithm, hashlib.new(algorithm, data).hexdigest())
