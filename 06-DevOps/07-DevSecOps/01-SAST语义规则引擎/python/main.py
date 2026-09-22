#!/usr/bin/env python3
"""Semgrep 通用匹配引擎的最小转写（对应 semgrep/semgrep@develop src/matching/）。

建模范围（全部可在 Matching_generic.ml 中逐行对位）：

* ``m_list_with_dots``            —— 列表里 ``...`` 的匹配与 ``[..., P, ...]`` 优化
* ``m_list_with_dots_and_metavar_ellipsis`` —— ``$...ARGS`` 的切分枚举
* ``m_list_in_any_order``         —— 结合律/交换律(AC)匹配

匹配结果是**一串环境**（tin 的列表），即源码里的 ``tout``：空列表 = fail。
``>||>`` 是列表并置，``>>=`` 是逐个环境继续。

节点模型、元变量绑定判据与 import 归一化见 ``astmodel.py``。
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from astmodel import (  # noqa: F401  （re-export，selfcheck 直接从 main 导入）
    Call, DottedName, Ellipsis, Env, FileName, Id, IdInfo, ImportAs, ImportFrom,
    Lit, MatchConfig, Metavar, MetavarEllipsis, Node, Text,
    check_and_add_metavar_binding, equal_ast_bound_code, full_module_names,
    is_anonymous_metavar, is_dots, is_metavar, is_metavar_ellipsis,
    normalize_import, render_module,
)

# --------------------------------------------------------------------------
# 1. 切分枚举
# --------------------------------------------------------------------------


def inits_and_rest_of_list(xs: List[Any]) -> List[tuple]:
    return [(xs[:i], xs[i:]) for i in range(1, len(xs) + 1)]


def inits_and_rest_of_list_empty_ok(xs: List[Any]) -> List[tuple]:
    """对应 inits_and_rest_of_list_empty_ok：

    ``[] -> [([], [])]``；非空时先给「空前缀 + 全体剩余」再给所有非空前缀。
    所以切分个数 = len(xs) + 1。
    """
    if not xs:
        return [([], [])]
    return [([], xs)] + inits_and_rest_of_list(xs)


def all_elem_and_rest_of_list(xs: List[Any]) -> List[tuple]:
    return [(x, xs[:i] + xs[i + 1:]) for i, x in enumerate(xs)]


# --------------------------------------------------------------------------
# 2. 列表匹配：... 与 $...ARGS
# --------------------------------------------------------------------------


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
                                          f: Callable,
                                          cfg: MatchConfig) -> List[Env]:
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
# 3. 单个元素的匹配（m_any 的极简版）
# --------------------------------------------------------------------------


def match_any(p: Node, c: Node, env: Env,
              cfg: Optional[MatchConfig] = None) -> List[Env]:
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
               less_is_ok: bool = False,
               cfg: Optional[MatchConfig] = None) -> List[Env]:
    env = {} if env is None else env
    cfg = cfg or MatchConfig()

    def elem(p: Node, c: Node, e: Env) -> List[Env]:
        return match_any(p, c, e, cfg)

    return m_list_with_dots_and_metavar_ellipsis(pat, tgt, env, less_is_ok,
                                                 elem, cfg)
