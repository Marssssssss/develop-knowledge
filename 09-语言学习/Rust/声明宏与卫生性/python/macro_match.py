"""`macro_rules!` 的匹配与转录（依据 Rust Reference `macros-by-example`）。

落地的官方规则：

1. **匹配时不向前看**（no lookahead）：无法逐 token 判定时直接报 local ambiguity。
2. **最外层定界符不限定种类**：matcher `(())` 能匹配 `{()}`，但内层必须一致。
3. **重复**：`*` / `+` / `?`，`?` 不能带分隔符；转录里的重复必须与匹配里的
   层数、种类、嵌套顺序完全一致；同一层里多个元变量必须绑到**同样多**的片段。
4. **转发**（forwarding）：已匹配的片段在下一个宏里是**不透明 AST**，
   只有 `ident` / `lifetime` / `tt` 还能被字面 token 匹配。

词法与跟随集检查在 `macro_lex.py`。
"""

from __future__ import annotations

from macro_lex import (
    Bindings, DELIMS, FOLLOW, FOLLOW_ANY, FRAGMENTS, MacroError, Opaque,
    _meta_names,
    check_follow_sets, parse_matcher, same_token, tokenize,
)

# ------------------------------------------------------------------ 匹配
def _match_seq(pats, toks, binds, depth, opaque):
    """匹配一串 pattern；返回剩余 token。binds 就地更新。"""
    i = 0
    for p in pats:
        if p[0] == "lit":
            if i >= len(toks) or not _lit_match(p[1], toks[i], opaque):
                raise MacroError("nomatch", "expect %r got %r" % (p[1], toks[i:i + 1]))
            i += 1
        elif p[0] == "crate":
            raise MacroError("nomatch", "$crate in matcher")
        elif p[0] == "meta":
            frag = p[2] or "tt"
            val, used = _match_fragment(frag, toks, i)
            if not opaque and frag in ("expr", "stmt", "ty", "path", "pat",
                                       "pat_param", "literal", "block", "meta"):
                val = Opaque(frag, val)
            binds.set(p[1], val, depth)
            i += used
        elif p[0] == "grp":
            if i >= len(toks) or toks[i][0] != "group" or toks[i][1] != p[1]:
                raise MacroError("nomatch", "expect group %s" % p[1])
            if _match_seq(p[2], toks[i][2], binds, depth, opaque):
                raise MacroError("nomatch", "group content not fully matched")
            i += 1
        elif p[0] == "rep":
            inner, sep, op = p[1], p[2], p[3]
            iterations, pos = [], i
            while True:
                sub = Bindings()
                try:
                    end = _match_seq(inner, toks[pos:], sub, depth + 1, opaque)
                except MacroError:
                    break
                new_pos = len(toks) - len(end)
                if new_pos == pos:      # 没有推进 → 停止，避免空匹配死循环
                    break
                iterations.append((sub, pos, end))
                pos = new_pos
                if sep is not None:
                    if pos < len(toks) and same_token(toks[pos], sep):
                        pos += 1
                    else:
                        break
                if op == "?":
                    break
            if op == "+" and not iterations:
                raise MacroError("nomatch", "repetition needs at least one")
            if op == "?" and len(iterations) > 1:
                iterations = iterations[:1]
            # 即使匹配到 0 次，元变量也算绑定（绑到空序列），只是深度要记对
            declared = _meta_names(inner, depth + 1)
            for nm, dd in declared.items():
                binds.values.setdefault(nm, [])
                binds.depths[nm] = dd
            for sub, _s, _e in iterations:
                for nm in declared:
                    binds.values[nm].append(sub.values[nm])
            i = pos
    return toks[i:]


def _remaining(toks, pos, end):
    return len(end)


def _lit_match(pat, tok, opaque):
    if isinstance(tok, Opaque):
        # 转发规则：只有 ident / lifetime / tt 还能被字面 token 匹配
        return pat[0] == "ident" and tok.frag in ("ident", "lifetime", "tt")
    if pat[0] == "group":
        # 内层定界符必须一致（官方：只有**最外层**的定界符不限定种类）
        return tok[0] == "group" and tok[1] == pat[1] and tok[2] == pat[2]
    return same_token(pat, tok)


def _match_fragment(frag, toks, i):
    if i >= len(toks):
        raise MacroError("nomatch", "no token for " + str(frag))
    tok = toks[i]
    if isinstance(tok, Opaque):
        return tok, 1
    if frag == "tt":
        return [tok], 1
    if frag == "ident":
        if tok[0] != "ident":
            raise MacroError("nomatch", "expect ident got %r" % (tok,))
        return [tok], 1
    if frag == "lifetime":
        if tok[0] != "lifetime":
            raise MacroError("nomatch", "expect lifetime got %r" % (tok,))
        return [tok], 1
    if frag == "literal":
        if tok[0] != "lit":
            raise MacroError("nomatch", "expect literal got %r" % (tok,))
        return [tok], 1
    if frag == "expr":
        return _match_expr(toks, i)
    if frag in ("ty", "path"):
        return _match_ty(toks, i)
    if frag in ("pat", "pat_param"):
        return _match_pat(toks, i)
    if frag == "block":
        if tok[0] != "group" or tok[1] != "{":
            raise MacroError("nomatch", "expect block")
        return [tok], 1
    return [tok], 1


def _match_expr(toks, i):
    """极简表达式识别：一个初等项，后面可跟二元运算符继续。"""
    if toks[i][0] in ("lit", "ident"):
        j = i + 1
        while j + 1 < len(toks) and toks[j][0] == "punct" and toks[j][1] in "+-*/%":
            j += 2
        return toks[i:j], j - i
    if toks[i][0] == "group":
        return [toks[i]], 1
    raise MacroError("nomatch", "expect expr got %r" % (toks[i],))


def _match_ty(toks, i):
    if toks[i][0] == "ident":
        j = i + 1
        if j < len(toks) and toks[j][0] == "punct" and toks[j][1] == "<":
            depth = 0
            while j < len(toks):
                if toks[j][0] == "punct" and toks[j][1] == "<":
                    depth += 1
                elif toks[j][0] == "punct" and toks[j][1] == ">":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                elif toks[j][0] == "punct" and toks[j][1] == ">>":
                    depth -= 2
                    j += 1
                    if depth <= 0:
                        break
                    continue
                j += 1
        return toks[i:j], j - i
    raise MacroError("nomatch", "expect ty got %r" % (toks[i],))


def _match_pat(toks, i):
    return _match_expr(toks, i)


def _match_seq_no_lookahead(pats, toks):
    """官方：matcher 里不能出现「无法逐 token 判定」的结构。"""
    for k, p in enumerate(pats):
        if p[0] == "rep" and p[3] == "*":
            rest = pats[k + 1:]
            if rest and rest[0][0] == "meta":
                return ("local ambiguity", "$%s:%s 前的 `$(..)*` 需要向前看才能判定"
                        % (rest[0][1], rest[0][2]))
    return None


def match_rule(matcher_src, input_src):
    mtoks, itoks = tokenize(matcher_src), tokenize(input_src)
    # 最外层定界符不参与匹配（官方原文）：只看里面的内容
    mbody = mtoks[0][2] if mtoks and mtoks[0][0] == "group" else mtoks
    ibody = itoks[0][2] if itoks and itoks[0][0] == "group" else itoks
    pats = parse_matcher(mbody)
    amb = _match_seq_no_lookahead(pats, ibody)
    if amb:
        raise MacroError(amb[0], amb[1])
    binds = Bindings()
    rest = _match_seq(pats, ibody, binds, 0, opaque=False)
    if rest:
        raise MacroError("nomatch", "unconsumed %r" % (rest,))
    return binds

from macro_trans import render, transcribe  # noqa: E402,F401  （供外部直接引用）
