# -*- coding: utf-8 -*-
"""Ansible Collections 的 FQCN 解析、collections 搜索路径作用域与 runtime.yml 元数据。

口径全部来自实际读过的官方原文（见 README 参考资料）：
  - Developing collections / Collection structure：galaxy.yml 定义命名空间、module_utils 导入约定、
    meta/runtime.yml 的 requires_ansible 与 plugin_routing
  - Using collections in a playbook：`collections:` 是**有序搜索路径**、只作用于 action/module 与角色引用、
    **角色不继承 playbook 的 collections**
  - Galaxy user guide：requirements.yml 里声明集合依赖
"""
from __future__ import annotations

# 官方原文："an FQCN is still required for non-action or module plugins
# (for example, lookups, filters, and tests)"
NON_SEARCHABLE_TYPES = {"lookup", "filter", "test", "connection", "callback", "vars", "cache"}


class Routing:
    OFF = "none"
    REDIRECT = "redirect"
    DEPRECATED = "deprecation"
    TOMBSTONE = "tombstone"


def parse_fqcn(ref: str):
    """拆成 (namespace, collection, 剩余路径列表)。非 FQCN 返回 None。"""
    parts = ref.split(".")
    if len(parts) < 3:
        return None
    return parts[0], parts[1], parts[2:]


def is_valid_playbook_name(name: str) -> bool:
    """官方原文：集合里的 playbook 名**不允许**出现连字符。"""
    return "-" not in name and name != ""


def module_utils_import(namespace: str, collection: str, util: str) -> str:
    """官方约定：from ansible_collections.{ns}.{coll}.plugins.module_utils.{util} import ..."""
    return "ansible_collections.%s.%s.plugins.module_utils.%s" % (namespace, collection, util)


def resolve_plugin(short_name: str, collections, available, ptype="module"):
    """按 `collections:` 列表的顺序在可用集合里找短名插件。

    返回 (fqcn, reason)。两条官方约束：
      1) 官方："The `collections` keyword merely creates an ordered 'search path' for
         non-namespaced plugin and role references."
      2) 非 action/module 类型**即使**在搜索路径里也必须用 FQCN。
    """
    if parse_fqcn(short_name) is not None:
        return (short_name, "fqcn") if short_name in available else (None, "not_found")
    if ptype in NON_SEARCHABLE_TYPES:
        return None, "requires_fqcn"
    for coll in collections:
        candidate = "%s.%s" % (coll, short_name)
        if candidate in available:
            return candidate, "search_path"
    return None, "not_found"


def resolve_in_role(short_name, playbook_collections, role_collections, available,
                    ptype="module"):
    """官方："any roles you call in your playbook define their own collections search
    order; they do not inherit the calling playbook's settings. This is true even if
    the role does not define its own `collections`."

    因此角色内解析**只看角色自己的列表**，playbook 的列表不参与。
    """
    return resolve_plugin(short_name, role_collections, available, ptype)


def _release_tuple(v: str):
    """截掉预发布段（官方：Ansible 会 truncate prerelease segments）。"""
    core = v.split("+")[0]
    for sep in ("a", "b", "rc", "dev", ".post"):
        idx = core.find(sep, 1)
        if idx > 0:
            core = core[:idx]
            break
    core = core.rstrip(".")
    out = []
    for p in core.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def _cmp(a, b):
    return (a > b) - (a < b)


def requires_ansible_ok(spec: str, ansible_version: str) -> bool:
    """meta/runtime.yml 的 requires_ansible：PEP440 说明符，逗号分隔。

    官方原文："although the version is a PEP440 Version Specifier under the hood, Ansible
    deviates from PEP440 behavior by truncating prerelease segments from the Ansible
    version. This means that Ansible 2.11.0b1 is compatible with something that
    requires_ansible: '>=2.11'."
    """
    ver = _release_tuple(ansible_version)
    for raw in [c for c in spec.split(",") if c.strip()]:
        c = raw.strip()
        for op in (">=", "<=", "!=", "=", ">", "<"):
            if c.startswith(op):
                want = _release_tuple(c[len(op):].strip())
                r = _cmp(ver, want)
                ok = {">=": r >= 0, "<=": r <= 0, "=": r == 0, "!=": r != 0,
                      ">": r > 0, "<": r < 0}[op]
                if not ok:
                    return False
                break
        else:
            raise ValueError("无法识别的说明符: %r" % c)
    return True


def route_plugin(runtime_yml: dict, ptype: str, plugin_name: str):
    """应用 plugin_routing，返回 (实际目标, warnings, fatal)。

    官方三种动作：
      - redirect：改从另一个位置加载
      - deprecation：给自定义警告 + removal version/date（改名/挪位时通常同时给 redirect）
      - tombstone：整个移除，致命错误 + removal_version + warning_text
    """
    routing = runtime_yml.get("plugin_routing", {}).get(ptype, {}).get(plugin_name)
    warnings, fatal = [], None
    if not routing:
        return plugin_name, warnings, fatal
    if "tombstone" in routing:
        t = routing["tombstone"]
        fatal = "%s 已被移除（removal_version=%s）：%s" % (
            plugin_name, t.get("removal_version"), t.get("warning_text", ""))
        return None, warnings, fatal
    if "deprecation" in routing:
        warnings.append(routing["deprecation"].get("warning_text", ""))
    if "redirect" in routing:
        return routing["redirect"], warnings, fatal
    return plugin_name, warnings, fatal
