#!/usr/bin/env python3
"""demo571 的节点模型、元变量绑定判据与 import 归一化。

对应源码：
* ``src/matching/Matching_generic.ml`` 的 add_mv_capture /
  equal_ast_bound_code / check_and_add_metavar_binding
* ``src/matching/Normalize_generic.ml`` 的 full_module_names /
  normalize_import_opt
"""

from typing import Any, Dict, List, Optional, Sequence

# --------------------------------------------------------------------------
# 1. 极简 AST（只保留能演示匹配机制的部分）
# --------------------------------------------------------------------------


class IdInfo:
    """对应 AST_generic 的 id_info。

    resolved 为 None 表示「已建 info 但未解析」；info 整体为 None 表示「没有 id_info」。
    源码里这两者是**不同分支**，判定不对称。
    """

    def __init__(self, resolved: Optional[str] = None, sid: int = -1,
                 case_insensitive: bool = False):
        self.resolved = resolved
        self.sid = sid
        self.case_insensitive = case_insensitive


class Node:
    kind = "?"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Node) and self.__dict__ == other.__dict__

    def __hash__(self) -> int:
        return hash((self.kind, str(self.__dict__)))

    def __repr__(self) -> str:
        return "%s(%s)" % (self.kind, self.__dict__)


class Id(Node):
    """mvalue 里的 MV.Id。"""

    kind = "Id"

    def __init__(self, name: str, info: Optional[IdInfo] = None):
        self.name = name
        self.info = info


class Lit(Node):
    """mvalue 里的 MV.E (L lit)。"""

    kind = "E"

    def __init__(self, value: Any):
        self.value = value


class Text(Node):
    """mvalue 里的 MV.Text（Ruby atom 之类）。"""

    kind = "Text"

    def __init__(self, s: str):
        self.s = s


class Call(Node):
    kind = "Call"

    def __init__(self, fname: Any, args: Sequence[Node]):
        self.fname = fname          # str（普通标识符）或 Metavar
        self.args = list(args)


class ImportFrom(Node):
    kind = "ImportFrom"

    def __init__(self, module: "DottedName", names: List[str]):
        self.module = module
        self.names = names


class ImportAs(Node):
    kind = "ImportAs"

    def __init__(self, module: "DottedName"):
        self.module = module


class DottedName(Node):
    kind = "DottedName"

    def __init__(self, idents: List[str]):
        self.idents = list(idents)


class FileName(Node):
    kind = "FileName"

    def __init__(self, s: str):
        self.s = s


# --------------------------------------------------------------------------
# 2. 模式侧节点
# --------------------------------------------------------------------------


class Metavar(Node):
    kind = "Metavar"

    def __init__(self, name: str):
        self.name = name


class Ellipsis(Node):
    """``...``"""

    kind = "Ellipsis"


class MetavarEllipsis(Node):
    """``$...ARGS``"""

    kind = "MetavarEllipsis"

    def __init__(self, name: str):
        self.name = name


def is_dots(a: Node) -> bool:
    return isinstance(a, Ellipsis)


def is_metavar_ellipsis(a: Node) -> Optional[str]:
    return a.name if isinstance(a, MetavarEllipsis) else None


def is_metavar(a: Node) -> bool:
    return isinstance(a, Metavar)


ANONYMOUS_METAVARS = {"_"}


def is_anonymous_metavar(name: str) -> bool:
    return name in ANONYMOUS_METAVARS


# --------------------------------------------------------------------------
# 3. 环境（tin）与元变量绑定
# --------------------------------------------------------------------------

Env = Dict[str, Node]


def add_mv_capture(env: Env, key: str, value: Node) -> Env:
    """对应 add_mv_capture：匿名元变量不进环境，因此**不参与合一**。

    这是 "f($_,$_) 能匹配 f(a,b)" 的唯一原因。
    """
    if is_anonymous_metavar(key):
        return env
    out = dict(env)
    out[key] = value
    return out


class MatchConfig:
    def __init__(self, unify_ids_strictly: bool = False):
        # 源码默认 unify_ids_strictly = false（见 Rule_options.t）
        self.unify_ids_strictly = unify_ids_strictly


def equal_ast_bound_code(a: Node, b: Node, cfg: MatchConfig) -> bool:
    """对应 equal_ast_bound_code：两边都是**已绑定代码**（不含元变量）。"""
    if a.kind == "Id" and b.kind == "Id":
        i1, i2 = a.info, b.info
        if (i1 is not None and i2 is not None
                and i1.case_insensitive and i2.case_insensitive):
            name_eq = a.name.lower() == b.name.lower()
        else:
            name_eq = a.name == b.name

        def unresolved(i: Optional[IdInfo]) -> bool:
            return i is not None and i.resolved is None

        # 分支顺序严格照抄 OCaml 的 match：
        #   | Some {id_resolved=None}, _ | _, Some {id_resolved=None} | None, _ -> true
        #   | Some i1, Some i2 -> (not unify_ids_strictly) || equal_id_info
        #   | Some _, None -> false
        if unresolved(i1) or unresolved(i2) or i1 is None:
            scope_ok = True
        elif i1 is not None and i2 is not None:
            scope_ok = (not cfg.unify_ids_strictly) or (
                i1.resolved == i2.resolved and i1.sid == i2.sid)
        else:
            scope_ok = False
        return name_eq and scope_ok

    if {a.kind, b.kind} == {"Id", "Text"}:
        ident = a if a.kind == "Id" else b
        if ident.info is not None and ident.info.resolved is None:
            return a.name == b.s
        return False

    return a == b


def check_and_add_metavar_binding(env: Env, mvar: str, valu: Node,
                                  cfg: MatchConfig) -> Optional[Env]:
    """对应 check_and_add_metavar_binding。

    返回 None 表示 fail；已绑定时用 equal_ast_bound_code 比对，
    **见证值保持在第一次绑定时的那个**（源码注释 "valu remains the metavar witness"）。
    """
    if mvar in env:
        return env if equal_ast_bound_code(env[mvar], valu, cfg) else None
    return add_mv_capture(env, mvar, valu)


# --------------------------------------------------------------------------
# 4. import 归一化（Normalize_generic）
# --------------------------------------------------------------------------


def full_module_names(is_pattern: bool, module: Node,
                      imports: Optional[List[str]]) -> Optional[List[Node]]:
    """对应 full_module_names。返回 None 表示该 import **不参与匹配**。"""
    if isinstance(module, DottedName):
        if imports is not None:
            return [DottedName(module.idents + [n]) for n in imports]
        return [DottedName(module.idents)]
    if isinstance(module, FileName):
        if imports is None:
            return [FileName(module.s)]
        # bugfix: JS 的 `import x from "path"` 不应被当成 "path"
        if not is_pattern:
            return [FileName(module.s)]
        return None
    return None


def normalize_import(is_pattern: bool, node: Node) -> Optional[List[Node]]:
    """对应 normalize_import_opt：丢掉本地别名后交给 full_module_names。"""
    if isinstance(node, ImportFrom):
        return full_module_names(is_pattern, node.module, node.names)
    if isinstance(node, ImportAs):
        return full_module_names(is_pattern, node.module, None)
    return None


def render_module(m: Node) -> str:
    if isinstance(m, DottedName):
        return ".".join(m.idents)
    if isinstance(m, FileName):
        return "FileName(%s)" % m.s
    return "?"
