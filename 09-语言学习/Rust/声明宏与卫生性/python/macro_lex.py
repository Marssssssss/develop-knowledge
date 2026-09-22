"""`macro_rules!` 的词法层：token 化、matcher 解析、跟随集检查。

从 `macro_match.py` 拆出（单文件 300 行约束）。依据 Rust Reference
`macros-by-example` 的片段分类符与跟随集（follow-set ambiguity）清单。
"""

from __future__ import annotations

PUNCT_RUN = set("+-*/%=<>!&|^~?:;,.$#@")
DELIMS = {"(": ")", "[": "]", "{": "}"}

FRAGMENTS = ["block", "expr", "expr_2021", "ident", "item", "lifetime", "literal",
             "meta", "pat", "pat_param", "path", "stmt", "tt", "ty", "vis"]

# Reference 的跟随集清单
FOLLOW = {
    "expr": {"=>", ",", ";"},
    "stmt": {"=>", ",", ";"},
    "pat_param": {"=>", ",", "=", "|", "if", "in"},
    "pat": {"=>", ",", "=", "if", "in"},
    "path": {"=>", ",", "=", "|", ";", ":", ">", ">>", "[", "{", "as", "where"},
    "ty": {"=>", ",", "=", "|", ";", ":", ">", ">>", "[", "{", "as", "where"},
}
# 其余片段（block/ident/item/lifetime/literal/meta/tt/vis）后面可以跟任意 token
FOLLOW_ANY = {"block", "ident", "item", "lifetime", "literal", "meta", "tt", "vis"}





def _meta_names(pats, depth):
    """收集模式里声明的元变量及其重复嵌套层数。"""
    out = {}
    for p in pats:
        if p[0] == "meta":
            out[p[1]] = depth
        elif p[0] == "rep":
            out.update(_meta_names(p[1], depth + 1))
    return out


class Bindings:
    def __init__(self):
        self.values = {}
        self.depths = {}

    def set(self, name, value, depth):
        self.values[name] = value
        self.depths[name] = depth


class Opaque:
    """已被匹配成片段的 token 序列：对后续宏的匹配器不透明。"""

    def __init__(self, frag, toks):
        self.frag = frag
        self.toks = toks

    def __repr__(self):
        return "Opaque(%s, %r)" % (self.frag, self.toks)


class MacroError(Exception):
    def __init__(self, kind, detail):
        super().__init__("%s: %s" % (kind, detail))
        self.kind = kind
        self.detail = detail


# ------------------------------------------------------------------ 词法
def tokenize(src):
    toks, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c.isspace():
            i += 1
            continue
        if c in DELIMS:
            depth, j = 0, i
            while j < n:
                if src[j] in DELIMS:
                    depth += 1
                elif src[j] in ")]}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            toks.append(("group", c, tokenize(src[i + 1:j])))
            i = j + 1
            continue
        if c in ")]}":
            raise MacroError("unbalanced", src[i:])
        if c == "'":
            j = i + 1
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            toks.append(("lifetime", src[i:j]))
            i = j
            continue
        if c.isdigit() or (c == '"'):
            j = i
            if c == '"':
                j = src.index('"', i + 1) + 1
            else:
                while j < n and (src[j].isdigit() or src[j] == "_"):
                    j += 1
            toks.append(("lit", src[i:j]))
            i = j
            continue
        j = i
        while j < n and not src[j].isspace() and src[j] not in DELIMS \
                and src[j] not in ")]}" and src[j] not in PUNCT_RUN:
            j += 1
        if j > i:
            toks.append(("ident", src[i:j]))
            i = j
            continue
        # 只有少数多字符运算符要合并；`,` `*` `;` 这类必须各自成 token
        if src[i:i + 2] in ("=>", "->", "::", ">>", "<<", "<-"):
            toks.append(("punct", src[i:i + 2]))
            i += 2
        else:
            toks.append(("punct", c))
            i += 1
    return toks


def same_token(a, b):
    """字面 token 比较：种类与文本都要一致（外层定界符除外）。"""
    if a[0] != b[0]:
        return False
    if a[0] == "group":
        return a[2] == b[2]          # 内容一致即可，定界符种类不参与
    return a[1] == b[1]


# ------------------------------------------------------------------ 模式解析
def parse_matcher(toks):
    pats, i, n = [], 0, len(toks)
    while i < n:
        t = toks[i]
        if t[0] == "punct" and t[1] == "$":
            nxt = toks[i + 1]
            if nxt[0] == "group":
                inner = parse_matcher(nxt[2])
                sep, op, i = None, None, i + 2
                if i < n and toks[i][0] == "punct" and toks[i][1] in "*+?":
                    op = toks[i][1]
                    i += 1
                elif i + 1 < n and toks[i + 1][0] == "punct" \
                        and toks[i + 1][1] in "*+?":
                    sep = toks[i]
                    op = toks[i + 1][1]
                    i += 2
                if op is None:
                    raise MacroError("syntax", "repetition needs * + or ?")
                if op == "?" and sep is not None:
                    raise MacroError("syntax", "`?` 不能带分隔符")
                pats.append(("rep", inner, sep, op))
                continue
            if nxt[0] == "ident":
                if nxt[1] == "crate":
                    pats.append(("crate",))
                    i += 2
                    continue
                if i + 2 < n and toks[i + 2][0] == "punct" and toks[i + 2][1] == ":" \
                        and toks[i + 3][0] == "ident":
                    frag = toks[i + 3][1]
                    if frag not in FRAGMENTS:
                        raise MacroError("syntax", "unknown fragment " + frag)
                    pats.append(("meta", nxt[1], frag))
                    i += 4
                    continue
                pats.append(("meta", nxt[1], None))
                i += 2
                continue
        if t[0] == "group":
            pats.append(("grp", t[1], parse_matcher(t[2])))
        else:
            pats.append(("lit", t))
        i += 1
    return pats


def check_follow_sets(pats):
    """按 Reference 的清单检查每个元变量后面允许出现什么。"""
    bad = []

    def walk(ps, trail):
        for k, p in enumerate(ps):
            if p[0] == "meta":
                frag = p[2]
                if frag not in FOLLOW:
                    continue
                rest = ps[k + 1:] + list(trail)
                nxt = rest[0] if rest else None
                if nxt is None:
                    continue
                if nxt[0] == "grp":
                    # 分组的**左定界符**才是紧跟在片段后面的那个 token
                    tok = nxt[1]
                elif nxt[0] == "rep":
                    inner_first = nxt[1][0] if nxt[1] else None
                    if inner_first is None:
                        continue
                    if inner_first[0] != "lit":
                        continue
                    tok = inner_first[1][1]
                elif nxt[0] == "lit":
                    tok = nxt[1][1]
                else:
                    continue
                if tok not in FOLLOW[frag]:
                    bad.append("元变量 $%s:%s 后面不能跟 %r" % (p[1], frag, tok))
            elif p[0] in ("rep", "grp"):
                walk(p[2] if p[0] == "grp" else p[1], ps[k + 1:] + list(trail))
    walk(pats, [])
    return bad


