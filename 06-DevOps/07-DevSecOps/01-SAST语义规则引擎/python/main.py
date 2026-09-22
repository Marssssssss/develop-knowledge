#!/usr/bin/env python3
"""Semgrep 通用匹配引擎的最小转写（对应 semgrep/semgrep@develop src/matching/）。

建模范围（全部可在 Matching_generic.ml / Normalize_generic.ml 中逐行对位）：

* ``m_list_with_dots``            —— 列表里 ``...`` 的匹配与 ``[..., P, ...]`` 优化
* ``m_list_with_dots_and_metavar_ellipsis`` —— ``$...ARGS`` 的切分枚举
* ``m_list_in_any_order``         —— 结合律/交换律(AC)匹配
* ``check_and_add_metavar_binding`` / ``equal_ast_bound_code`` —— 元变量绑定一致性
* ``Normalize_generic.full_module_names`` —— import 归一化

匹配结果是**一串环境**（tin 的列表），即源码里的 ``tout``：空列表 = fail。
``>||>`` 是列表并置，``>>=`` 是逐个环境继续。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# 1. 极简 AST（只保留能演示上面 5 个机制的部分）
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


def equal_ast_bound_code(a: Node, b: Node, cfg: "MatchConfig") -> bool:
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


class MatchConfig:
    def __init__(self, unify_ids_strictly: bool = False):
        # 源码默认 unify_ids_strictly = false（见 Rule_options.t）
        self.unify_ids_strictly = unify_ids_strictly


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
# 4. 列表匹配：... 与 $...ARGS
# --------------------------------------------------------------------------


def inits_and_rest_of_list(xs: List[Any]) -> List[Tuple[List[Any], List[Any]]]:
    return [(xs[:i], xs[i:]) for i in range(1, len(xs) + 1)]


def inits_and_rest_of_list_empty_ok(xs: List[Any]) -> List[Tuple[List[Any], List[Any]]]:
    """对应 inits_and_rest_of_list_empty_ok：

    ``[] -> [([], [])]``；非空时先给「空前缀 + 全体剩余」再给所有非空前缀。
    所以切分个数 = len(xs) + 1。
    """
    if not xs:
        return [([], [])]
    return [([], xs)] + inits_and_rest_of_list(xs)


def all_elem_and_rest_of_list(xs: List[Any]) -> List[Tuple[Any, List[Any]]]:
    return [(x, xs[:i] + xs[i + 1:]) for i, x in enumerate(xs)]


def m_list_with_dots(pat: List[Node], tgt: List[Node], env: Env,
                     less_is_ok: bool, f: Callable) -> List[Env]:
    """对应 m_list_with_dots（外层带 [..., P, ...] 优化的那一个）。"""
    # Optimization: [..., PAT, ...] —— 只需把 PAT 对 xsb 的每个元素试一遍
    if len(pat) == 3 and is_dots(pat[0]) and is_dots(pat[2]):
        out: List[Env] = []
        for xb in tgt:
            out += f(pat[1], xb, env)
        return out
    return _m_list_with_dots(pat, tgt, env, less_is_ok, f)


def _m_list_with_dots(pat: List[Node], tgt: List[Node], env: Env,
                      less_is_ok: bool, f: Callable) -> List[Env]:
    if not pat and not tgt:
        return [env]
    if not pat and tgt:
        return [env] if less_is_ok else []
    if len(pat) == 1 and is_dots(pat[0]) and not tgt:
        return [env]
    if pat and tgt and is_dots(pat[0]):
        # 可以吃掉 0 个（模式前进）
        out = _m_list_with_dots(pat[1:], tgt, env, less_is_ok, f)
        # 也可以继续吃（目标前进）
        out += _m_list_with_dots(pat, tgt[1:], env, less_is_ok, f)
        return out
    if pat and tgt:
        out = []
        for e in f(pat[0], tgt[0], env):
            out += _m_list_with_dots(pat[1:], tgt[1:], e, less_is_ok, f)
        return out
    return []


def m_list_with_dots_and_metavar_ellipsis(pat: List[Node], tgt: List[Node],
                                          env: Env, less_is_ok: bool,
                                          f: Callable, cfg: MatchConfig) -> List[Env]:
    """对应 m_list_with_dots_and_metavar_ellipsis。

    注意源码里 ``[a], xs when is_metavar_ellipsis a && not less_is_ok`` 那条
    优化是**被注释掉的**（``(* opti: ... *)`` 直到 ``*)``），因此不走短路，
    一律枚举全部切分。
    """
    def aux(p: List[Node], t: List[Node], e: Env) -> List[Env]:
        if not p and not t:
            return [e]
        if not p and t:
            return [e] if less_is_ok else []
        if len(p) == 1 and is_dots(p[0]) and not t:
            return [e]
        name = is_metavar_ellipsis(p[0]) if p else None
        if name is not None:
            out: List[Env] = []
            for inits, rest in inits_and_rest_of_list_empty_ok(t):
                # 元变量绑定的是「列表」本身，这里用 Lit(list) 承载
                ne = check_and_add_metavar_binding(e, name, Lit(inits), cfg)
                if ne is None:
                    continue
                out += aux(p[1:], rest, ne)
            return out
        if p and t and is_dots(p[0]):
            return aux(p[1:], t, e) + aux(p, t[1:], e)
        if p and t:
            out = []
            for e2 in f(p[0], t[0], e):
                out += aux(p[1:], t[1:], e2)
            return out
        return []

    return aux(pat, tgt, env)


def m_list_in_any_order(pat: List[Node], tgt: List[Node], env: Env,
                        less_is_ok: bool, f: Callable) -> List[Env]:
    """对应 m_list_in_any_order：AC 匹配，逐元素挑、剩余递归。"""
    if not pat and not tgt:
        return [env]
    if not pat and tgt:
        return [env] if less_is_ok else []
    if pat:
        out: List[Env] = []
        for b, rest in all_elem_and_rest_of_list(tgt):
            for e in f(pat[0], b, env):
                out += m_list_in_any_order(pat[1:], rest, e, less_is_ok, f)
        return out
    return []


# --------------------------------------------------------------------------
# 5. 单个元素的匹配（m_any 的极简版）
# --------------------------------------------------------------------------


def match_any(p: Node, c: Node, env: Env, cfg: Optional[MatchConfig] = None) -> List[Env]:
    cfg = cfg or MatchConfig()
    if is_metavar(p):
        ne = check_and_add_metavar_binding(env, p.name, c, cfg)
        return [] if ne is None else [ne]
    if is_dots(p) or is_metavar_ellipsis(p) is not None:
        return []           # 省略号不应出现在元素位置
    if p.kind == "Call" and c.kind == "Call":
        if isinstance(p.fname, str) or isinstance(c.fname, str):
            envs = [env] if p.fname == c.fname else []
        else:
            envs = match_any(p.fname, c.fname, env, cfg)
        out: List[Env] = []
        for e in envs:
            out += m_list_with_dots_and_metavar_ellipsis(p.args, c.args, e,
                                                         False, match_any, cfg)
        return out
    if p.kind == "Id" and c.kind == "Id":
        return [env] if equal_ast_bound_code(p, c, cfg) else []
    if p.kind == "E" and c.kind == "E":
        return [env] if p.value == c.value else []
    return []


def match_args(pat: List[Node], tgt: List[Node], env: Optional[Env] = None,
               less_is_ok: bool = False, cfg: Optional[MatchConfig] = None) -> List[Env]:
    env = {} if env is None else env
    cfg = cfg or MatchConfig()

    def elem(p: Node, c: Node, e: Env) -> List[Env]:
        return match_any(p, c, e, cfg)

    return m_list_with_dots_and_metavar_ellipsis(pat, tgt, env, less_is_ok,
                                                 elem, cfg)


# --------------------------------------------------------------------------
# 6. import 归一化（Normalize_generic.full_module_names / normalize_import_opt）
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
