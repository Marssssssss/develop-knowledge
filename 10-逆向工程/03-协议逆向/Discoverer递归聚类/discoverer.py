#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Discoverer（USENIX Security 2007）三阶段最小复现。

来源：W. Cui, J. Kannan, H. J. Wang, *Discoverer: Automatic Protocol Reverse
Engineering from Network Traces*, 16th USENIX Security Symposium, 2007。

复现内容：
  §3.2.1 标识化（text / binary 两类 token，文本段最短 3 字节，分隔符切分，UTF-16LE 探测）
  §3.2.2 按 token 模式 (dir, class…) 做初始聚类
  §3.3.1 格式推断（属性：常量/变量；语义：length / offset）
  §3.3.2 格式比较（常量-变量、变量-变量的保守匹配策略）
  §3.3.3 递归聚类（FD token 三判据）
  §3.4   基于类型的序列比对合并（gap 约束 + 至多一处失配）

§4.3 Table 3 给出的参数：最大报文前缀 2048 字节、文本段最短 3 字母、
最小簇大小 20 条报文、比对 match/mismatch/gap = 1/0/-2。
"FD token 的最大取值数"在 §4.3 正文被提到但 Table 3 未给数值，本 demo 取 8
并在 README 中标注为 demo 自选值。
"""

MAX_PREFIX = 2048          # §4.3 Table 3: Maximum message prefix
TEXT_MIN = 3               # §4.3 Table 3: Minimum length of text segments
MIN_CLUSTER = 2            # §4.3 Table 3 原文取 20 messages，demo 规模小改为 2
FD_MAX_DISTINCT = 8        # §4.3 正文提到但 Table 3 未列数值 → demo 自选
NW_MATCH, NW_MISMATCH, NW_GAP = 1, 0, -2   # §4.3 Table 3
NEG = -10 ** 6
DELIMS = {0x20, 0x09}      # space, tab（论文举例 space 和 tab）
B, T = "B", "T"

MAGIC = b"\xff\x00\x00\x00"


# ---------------------------------------------------------------- 标识化 §3.2.1
def _printable(b):
    return 0x20 <= b <= 0x7E


def find_utf16le(msg):
    """论文："We also look for Unicode encodings in messages."

    返回 UTF-16LE 文本段的 [(start, end)]：可打印字节后紧跟 0x00。
    """
    segs, i, n = [], 0, len(msg)
    while i < n:
        if _printable(msg[i]) and i + 1 < n and msg[i + 1] == 0x00:
            j = i
            while j + 1 < n and _printable(msg[j]) and msg[j + 1] == 0x00:
                j += 2
            if j - i >= 4:              # 至少 2 个字符才值得当文本段
                segs.append((i, j))
            i = j
        else:
            i += 1
    return segs


def _split_delims(run):
    parts, cur = [], bytearray()
    for b in run:
        if b in DELIMS:
            if cur:
                parts.append(bytes(cur))
                cur = bytearray()
        else:
            cur.append(b)
    if cur:
        parts.append(bytes(cur))
    return parts


def tokenize(msg):
    """§3.2.1 标识化。返回 [(cls, value_bytes, offset)]。"""
    msg = msg[:MAX_PREFIX]
    toks, uni, upos, i, n = [], find_utf16le(msg), 0, 0, len(msg)
    while i < n:
        if upos < len(uni) and uni[upos][0] <= i < uni[upos][1]:
            s, e = uni[upos]
            toks.append((T, msg[s:e], s))
            i, upos = e, upos + 1
            continue
        if _printable(msg[i]):
            j = i
            while j < n and _printable(msg[j]) and not (
                    upos < len(uni) and uni[upos][0] <= j < uni[upos][1]):
                j += 1
            run = msg[i:j]
            # 最短长度限制：低于 TEXT_MIN 的一律退化成单字节 binary token
            if len(run) >= TEXT_MIN:
                off = i
                for p in _split_delims(run):
                    toks.append((T, p, off))
                    off += len(p) + 1
            else:
                for k, b in enumerate(run):
                    toks.append((B, bytes([b]), i + k))
            i = j
        else:
            toks.append((B, bytes([msg[i]]), i))
            i += 1
    return toks


def token_pattern(direction, toks):
    """§3.2.2 token pattern = (dir, class_of_token_1, class_of_token_2, …)。"""
    return (direction,) + tuple(c for c, _, _ in toks)


# ------------------------------------------------------------ 格式推断 §3.3.1
def _as_int(toklists, idxs, ti):
    return int.from_bytes(b"".join(toklists[ti][i][1] for i in idxs), "big")


def _detect(toklists, msgs, ntok, k, maxwidth=4):
    """返回 (semantic, width)；semantic ∈ {"", "length", "offset"}。

    §3.3.1 原文：长度字段的直觉是「候选长度字段的值之差 反映 报文长度之差
    或某些后续 token 的长度之差」；offset 字段则「与后续 token 的偏移之差比较」。
    """
    for w in range(1, min(maxwidth, ntok - k) + 1):
        idxs = list(range(k, k + w))
        if any(toklists[0][i][0] != B for i in idxs):
            continue                     # 只考虑连续 binary token
        vals = [_as_int(toklists, idxs, ti) for ti in range(len(toklists))]
        if len(set(vals)) == 1:
            continue                     # 常量不可能是长度/偏移字段
        pairs = [(a, b) for a in range(len(vals)) for b in range(len(vals))]
        if all(vals[a] - vals[b] == len(msgs[a]) - len(msgs[b]) for a, b in pairs):
            return "length", w
        if any(all(vals[a] - vals[b] ==
                   len(toklists[a][t][1]) - len(toklists[b][t][1]) for a, b in pairs)
               for t in range(k + w, ntok)):
            return "length", w
        if any(all(vals[a] - vals[b] ==
                   toklists[a][t][2] - toklists[b][t][2] for a, b in pairs)
               for t in range(k + w, ntok)):
            return "offset", w
    return "", 1


def infer_format(msgs):
    """§3.3.1：输出 token 规范序列（属性 + 语义）。要求簇内 token 数一致。"""
    toklists = [tokenize(m) for m in msgs]
    ntok = len(toklists[0])
    assert all(len(t) == ntok for t in toklists), "同 token 模式簇内 token 数必须一致"
    fmt, k = [], 0
    while k < ntok:
        vals = [tl[k][1] for tl in toklists]
        cls = toklists[0][k][0]
        sem, w = ("", 1) if cls == T else _detect(toklists, msgs, ntok, k)
        for d in range(w):
            v = [tl[k + d][1] for tl in toklists]
            fmt.append({"cls": cls, "sem": sem, "const": len(set(v)) == 1,
                        "values": frozenset(v), "size": len(v[0])})
        k += w
    return fmt


# ------------------------------------------------------------ 格式比较 §3.3.2
def tok_match(a, b):
    """§3.3.2：语义相同即匹配；否则按「常量-变量」「变量-变量」的保守策略。"""
    if a["sem"] and b["sem"]:
        return a["sem"] == b["sem"]
    if a["const"] and b["const"]:
        return a["values"] == b["values"]
    if a["const"] or b["const"]:
        c, v = (a, b) if a["const"] else (b, a)
        return bool(c["values"] & v["values"])   # 变量至少取过常量的值
    return bool(a["values"] & b["values"])       # 两个变量的取值集合有交集


def format_equal(f1, f2):
    """§3.3.2：逐 token 从左到右比对类型，全中才算同一格式。"""
    return len(f1) == len(f2) and all(tok_match(x, y) for x, y in zip(f1, f2))


# -------------------------------------------------------- 递归聚类 §3.3.3
def find_fd(msgs):
    """§3.3.3 FD 三判据。返回 FD token 下标，找不到返回 None。"""
    fmt = infer_format(msgs)
    for k, spec in enumerate(fmt):
        vals = [tokenize(m)[k][1] for m in msgs]
        if len(set(vals)) >= FD_MAX_DISTINCT:          # 判据 1
            continue
        groups = {}
        for m, v in zip(msgs, vals):
            groups.setdefault(v, []).append(m)
        # 常量 token 只会切出 1 个子簇 —— 等于没切，必须排除，否则递归不终止
        if len(groups) < 2:
            continue
        if max(len(g) for g in groups.values()) < MIN_CLUSTER:   # 判据 2
            continue
        subfmts = [infer_format(g) for g in groups.values()]
        if any(format_equal(subfmts[0], s) for s in subfmts[1:]):   # 判据 3
            continue                                   # 子簇格式相同 → 不该切
        return k
    return None


def recursive_cluster(msgs, depth=0, out=None):
    """§3.3.3：找到 FD → 按取值切簇 → 对每个子簇递归。"""
    if out is None:
        out = []
    k = find_fd(msgs)
    if k is None or depth > 4:
        out.append(msgs)
        return out
    groups = {}
    for m in msgs:
        groups.setdefault(tokenize(m)[k][1], []).append(m)
    for g in groups.values():
        recursive_cluster(g, depth + 1, out)
    return out


# ------------------------------------------- 基于类型的序列比对合并 §3.4
def align(f1, f2):
    """Needleman-Wunsch，且「只允许同 class 的 token 互相对齐」。

    返回 (aligned_pairs, gaps_of_text) ；aligned_pairs 元素为 (i|None, j|None)。
    """
    n, m = len(f1), len(f2)
    F = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        F[i][0] = i * NW_GAP
    for j in range(1, m + 1):
        F[0][j] = j * NW_GAP
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sc = (NW_MATCH if tok_match(f1[i - 1], f2[j - 1]) else NW_MISMATCH) \
                if f1[i - 1]["cls"] == f2[j - 1]["cls"] else NEG
            F[i][j] = max(F[i - 1][j - 1] + sc, F[i - 1][j] + NW_GAP, F[i][j - 1] + NW_GAP)
    pairs, i, j = [], n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and f1[i - 1]["cls"] == f2[j - 1]["cls"]:
            sc = NW_MATCH if tok_match(f1[i - 1], f2[j - 1]) else NW_MISMATCH
            if F[i][j] == F[i - 1][j - 1] + sc:
                pairs.append((i - 1, j - 1)); i -= 1; j -= 1; continue
        if i > 0 and F[i][j] == F[i - 1][j] + NW_GAP:
            pairs.append((i - 1, None)); i -= 1; continue
        pairs.append((None, j - 1)); j -= 1
    return list(reversed(pairs))


def gap_ok(pairs, f1, f2):
    """§3.4 两条额外 gap 约束。

    1) 一段连续 binary token 对上 gap 时，它必须在另一侧「紧邻一个 text token」，
       且其个数 ≤ 该 text token 的大小；
    2) text token 对上 gap 的次数至多 2 次。
    """
    text_gaps = sum(1 for i, j in pairs
                    if (i is not None and j is None and f1[i]["cls"] == T)
                    or (j is not None and i is None and f2[j]["cls"] == T))
    if text_gaps > 2:
        return False
    for side, run in _gap_runs(pairs):
        own, other = (f1, f2) if side == 1 else (f2, f1)
        if all(own[x]["cls"] == B for x in run):
            # 简化判据：另一侧必须存在 size ≥ 该段长度的 text token 才允许这段 binary 对 gap
            if not any(t["cls"] == T and t["size"] >= len(run) for t in other):
                return False
    return True


def _gap_runs(pairs):
    """抽出「某一侧连续对上 gap」的片段，返回 [(side, run)]，side=1 表示 f1 侧。"""
    out, i, n = [], 0, len(pairs)
    while i < n:
        a, b = pairs[i]
        if a is not None and b is None:
            run = []
            while i < n and pairs[i][0] is not None and pairs[i][1] is None:
                run.append(pairs[i][0]); i += 1
            out.append((1, run))
        elif b is not None and a is None:
            run = []
            while i < n and pairs[i][1] is not None and pairs[i][0] is None:
                run.append(pairs[i][1]); i += 1
            out.append((2, run))
        else:
            i += 1
    return out


def mismatch_count(pairs, f1, f2):
    return sum(1 for i, j in pairs
               if i is not None and j is not None and not tok_match(f1[i], f2[j]))


def can_merge(f1, f2):
    """§3.4：gap 约束不满足 → 直接判不匹配；否则至多 1 处失配即合并。"""
    pairs = align(f1, f2)
    if not gap_ok(pairs, f1, f2):
        return False, None
    return mismatch_count(pairs, f1, f2) <= 1, pairs
