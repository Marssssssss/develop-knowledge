"""BinPRE §3.5 —— 语义精化（Semantic Refinement）。

三段流水线：

1. **Format-based clustering**（Algorithm 2）：遍历所有字段位置，拿它当候选 command
   field 聚类，用 `NW_f`（在**字段边界序列**上跑 Needleman-Wunsch）给每种分法打分，
   取最高分的那个位置作为 command 字段；
2. **Entropy-based type refinement**（§3.5.2）：同一簇内算每个字段的 Shannon 熵，
   取簇内所有字段熵的**中位数**作判据——Static 的熵必须**小于**中位数，
   Bytes 必须**大于**中位数，否则该推断被撤掉；类型未知的字段，取簇内熵最接近的
   字段的类型；
3. **Type-based function refinement**（§3.5.3）：用 Table 3 的「功能 ⇒ 类型」约束
   清掉不合理的功能推断（如 Length 却不是 Integer）。

运行：python main.py
"""

from __future__ import annotations

import math
from collections import Counter

MA_SCORE = 1      # 边界命中
MISMA_SCORE = -1  # 边界不命中
GAP_SCORE = -2    # 断点惩罚（论文默认值）

# Table 3：语义功能 -> 允许的类型
FUNCTION_TYPE_CONSTRAINT = {
    "Command": {"Group"},
    "Length": {"Integer"},
    "Delim": {"Static", "Group"},
    "Aligned": {"Group", "Bytes"},
    "Checksum": {"Integer"},
    "Filename": {"String"},
}


# ---------------------------------------------------------------- NW_f

def nw_format(fa, fb):
    """在字段边界序列上做全局对齐，返回最优得分。

    C(m,n) = MA  若两条边界的**偏移**相同，否则 MISMA；空位罚 GAP。
    """
    n, m = len(fa), len(fb)
    prev = [j * GAP_SCORE for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [i * GAP_SCORE] + [0] * m
        for j in range(1, m + 1):
            diag = prev[j - 1] + (MA_SCORE if fa[i - 1] == fb[j - 1]
                                  else MISMA_SCORE)
            cur[j] = max(diag, prev[j] + GAP_SCORE, cur[j - 1] + GAP_SCORE)
        prev = cur
    return prev[m]


def align_score(clusters, formats):
    """论文 §3.5.1：簇内**两两** NW_f 的平均值。"""
    total, pairs = 0.0, 0
    for cl in clusters:
        for i in range(len(cl)):
            for j in range(i + 1, len(cl)):
                total += nw_format(formats[cl[i]], formats[cl[j]])
                pairs += 1
    return total / pairs if pairs else 0.0


def cluster_by(messages, lo, hi):
    """按 [lo, hi) 这段字节的取值聚类，返回下标分簇。"""
    groups = {}
    for i, msg in enumerate(messages):
        groups.setdefault(bytes(msg[lo:hi]), []).append(i)
    return [v for v in groups.values() if len(v) >= 1]


def explore_optimal(messages, formats):
    """Algorithm 2：找使簇内平均对齐分最高的那个字段位置。

    候选位置来自**任意消息**的任意一对相邻边界（论文 line 6-7）。
    返回 (clusters, command_pos, best_score)。
    """
    cands = set()
    for f in formats:
        for k in range(len(f) - 1):
            cands.add((f[k], f[k + 1]))
    best, best_pos, best_score = None, None, float("-inf")
    for pos in sorted(cands):
        cl = cluster_by(messages, pos[0], pos[1])
        sc = align_score(cl, formats)
        if sc > best_score:
            best, best_pos, best_score = cl, pos, sc
    return best, best_pos, best_score


# ---------------------------------------------------------------- 熵

def shannon_entropy(values):
    """按取值频率算 Shannon 熵（单位 bit）。"""
    if not values:
        return 0.0
    n = len(values)
    cnt = Counter(values)
    return -sum((c / n) * math.log2(c / n) for c in cnt.values())


def median(xs):
    if not xs:
        return 0.0
    s = sorted(xs)
    mid = len(s) // 2
    if len(s) % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def entropy_type_refinement(fields, inferred):
    """§3.5.2：用簇内熵的中位数修正类型推断。

    `fields` 是 [(name, values)]，`inferred` 是 {name: type}。
    返回 (修正后的类型表, 每个字段的熵, 中位数)。
    """
    ent = {name: shannon_entropy(vals) for name, vals in fields}
    med = median(list(ent.values()))
    out = dict(inferred)
    for name, _vals in fields:
        t = inferred.get(name)
        if t == "Static" and not ent[name] < med:
            del out[name]
        elif t == "Bytes" and not ent[name] > med:
            del out[name]
    return out, ent, med


def fill_unknown_by_nearest_entropy(fields, inferred, ent):
    """类型未知的字段：取簇内熵最接近的那个字段的类型。"""
    out = dict(inferred)
    known = [(n, ent[n]) for n, _ in fields if n in inferred]
    for name, _vals in fields:
        if name in inferred or not known:
            continue
        nearest = min(known, key=lambda kv: abs(kv[1] - ent[name]))
        out[name] = inferred[nearest[0]]
    return out


# ---------------------------------------------------------------- 功能约束

def function_refinement(types, functions):
    """§3.5.3 + Table 3：砍掉类型对不上的功能。"""
    kept, dropped = {}, []
    for field, fn in functions.items():
        allowed = FUNCTION_TYPE_CONSTRAINT.get(fn)
        t = types.get(field)
        if allowed is None or t in allowed:
            kept[field] = fn
        else:
            dropped.append((field, fn, t))
    return kept, dropped


# ---------------------------------------------------------------- 演示

MSG = [
    bytes.fromhex("00010000 0006ff03 0002 0008".replace(" ", "")),
    bytes.fromhex("00020000 0006ff03 0002 0001".replace(" ", "")),
    bytes.fromhex("00030000 0007ff05 0000 fe 0002".replace(" ", "")),
    bytes.fromhex("00040000 0009ff10 0001 0001 0200 0f".replace(" ", "")),
    bytes.fromhex("00050000 0006ff05 0000 fd 00".replace(" ", "")),
]

# 字段边界：示例格式（每条报文的边界偏移序列）
FORMATS = [
    [0, 2, 6, 7, 8, 10, 13],
    [0, 2, 6, 7, 8, 10, 13],
    [0, 2, 6, 7, 9, 11, 13],
    [0, 2, 6, 7, 9, 11, 15],
    [0, 2, 6, 7, 9, 11, 12],
]


def demo():
    cl, pos, sc = explore_optimal(MSG, FORMATS)
    print("最优 command 字段位置 :", pos, " 平均分 = %.3f" % sc)
    for c in cl:
        print("   簇:", c, " command =", MSG[c[0]][pos[0]:pos[1]].hex())
    # 第一簇内 f4,5（偏移 4..6）恒定 -> 熵为 0 -> Bytes 推断被撤
    fields = [("f0,2", [bytes(m[0:2]) for m in MSG]),
              ("f2,6", [bytes(m[2:6]) for m in MSG]),
              ("f6,7", [bytes(m[6:7]) for m in MSG]),
              ("f7,9", [bytes(m[7:9]) for m in MSG])]
    inferred = {"f0,2": "Integer", "f2,6": "Bytes", "f6,7": "Static"}
    refined, ent, med = entropy_type_refinement(fields, inferred)
    print("熵     :", {k: round(v, 3) for k, v in ent.items()}, "中位数 =", med)
    print("修正前 :", inferred)
    print("修正后 :", refined)
    fn = {"f0,2": "Length", "f2,6": "Checksum", "f6,7": "Delim"}
    kept, dropped = function_refinement(refined, fn)
    print("功能保留:", kept, " 砍掉:", dropped)


if __name__ == "__main__":
    demo()
