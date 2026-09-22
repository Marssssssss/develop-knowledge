"""`macro_rules!` 的转录（transcribe）：把绑定的元变量展开回 token。

从 `macro_match.py` 拆出（单文件 300 行约束）。核心是官方那条限制：
转录里的重复必须与匹配里的**层数、种类、嵌套顺序完全一致**，
且同一层重复里的多个元变量必须绑到同样多的片段。
"""

from __future__ import annotations

from macro_lex import (
    Bindings, DELIMS, MacroError, Opaque, parse_matcher, tokenize,
)


# ------------------------------------------------------------------ 转录
def transcribe(trans_src, binds):
    toks = tokenize(trans_src)
    # 转录器最外层的定界符同样不参与（只是把整体包起来）
    body = toks[0][2] if toks and toks[0][0] == "group" else toks
    pats = parse_matcher(body)
    return _trans_seq(pats, binds, 0)


def _metas_at_level(pats):
    """本层重复里出现的元变量（穿过分组，但不穿过更内层的重复）。"""
    out = []
    for q in pats:
        if q[0] == "meta":
            out.append(q[1])
        elif q[0] == "grp":
            out.extend(_metas_at_level(q[2]))
    return out


def _metas_deep(pats):
    """任意深度的元变量（用于「这一层至少要有一个元变量」的检查）。"""
    out = []
    for q in pats:
        if q[0] == "meta":
            out.append(q[1])
        elif q[0] == "grp":
            out.extend(_metas_deep(q[2]))
        elif q[0] == "rep":
            out.extend(_metas_deep(q[1]))
    return out


def _trans_seq(pats, binds, depth):
    out = []
    for p in pats:
        if p[0] == "lit":
            out.append(p[1])
        elif p[0] == "crate":
            out.append(("ident", "$crate"))
        elif p[0] == "meta":
            if p[1] not in binds.values:
                raise MacroError("unbound", "unknown metavariable $" + p[1])
            want = binds.depths[p[1]]
            if want != depth:
                raise MacroError(
                    "mismatch", "元变量 $%s 的重复层数不一致（匹配 %d 层，转录 %d 层）"
                    % (p[1], want, depth))
            out.extend(_flatten(binds.values[p[1]]))
        elif p[0] == "grp":
            out.append(("group", p[1], _trans_seq(p[2], binds, depth)))
        elif p[0] == "rep":
            inner, sep, _op = p[1], p[2], p[3]
            deep = _metas_deep(inner)
            if not deep:
                raise MacroError("mismatch", "转录的重复里至少要有一个元变量")
            names = _metas_at_level(inner)
            if names:
                lengths = set()
                for nm in names:
                    if nm not in binds.values:
                        raise MacroError("unbound", "unknown metavariable $" + nm)
                    if binds.depths[nm] != depth + 1:
                        raise MacroError(
                            "mismatch", "元变量 $%s 的重复层数不一致（匹配 %d 层）"
                            % (nm, binds.depths[nm]))
                    lengths.add(len(binds.values[nm]))
                if len(lengths) > 1:
                    raise MacroError("mismatch",
                                     "同一层重复里的元变量数量不同：%s" % sorted(lengths))
                n = lengths.pop()
            else:
                # 本层没有直接元变量：次数由更内层的元变量的**外层**列表长度决定
                owned = [nm for nm in deep if binds.depths.get(nm, 0) >= depth + 1]
                lens = {len(binds.values[nm]) for nm in owned}
                if len(lens) > 1:
                    raise MacroError("mismatch", "嵌套重复的次数不一致：%s" % sorted(lens))
                n = lens.pop() if lens else 0
            for k in range(n):
                if k > 0 and sep is not None:
                    out.append(sep)
                sub = Bindings()
                for nm in binds.values:
                    d = binds.depths.get(nm, 0)
                    if d >= depth + 1:      # 属于本层或更内层的重复 → 取第 k 份
                        sub.values[nm] = binds.values[nm][k]
                        sub.depths[nm] = d
                    else:
                        sub.values[nm] = binds.values[nm]
                        sub.depths[nm] = d
                out.extend(_trans_seq(inner, sub, depth + 1))
    return out


def _flatten(v):
    if isinstance(v, Opaque):
        return list(v.toks)
    if isinstance(v, list):
        return v
    return [v]


def render(toks):
    out = []
    for t in toks:
        if isinstance(t, Opaque):
            out.append(render(t.toks))
        elif t[0] == "group":
            out.append(t[1] + render(t[2]) + DELIMS[t[1]])
        else:
            out.append(t[1])
    return " ".join(out)
