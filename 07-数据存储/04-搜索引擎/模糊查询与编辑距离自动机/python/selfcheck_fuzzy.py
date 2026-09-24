"""模糊查询与编辑距离自动机 —— 自检。

期望值来自 FuzzyQuery.java / LevenshteinAutomata.java / Lev*ParametricDescription.java
源码实读 + 手算，并已实跑校准。

运行：python selfcheck_fuzzy.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fuzzy import (FuzzyQuery, float_to_edits, LevenshteinAutomata,
                   Lev1ParametricDescription, Lev1TParametricDescription,
                   Lev2ParametricDescription, MAXIMUM_SUPPORTED_DISTANCE,
                   CHARACTER_MAX_CODE_POINT)
from editdist import levenshtein, damerau_osa, distance

PASS = 0
FAIL = []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(name)


def eq(name, got, want):
    check("%s (got=%r want=%r)" % (name, got, want), got == want)


# ============================================== 1. FuzzyQuery 默认值与校验
eq("defaultMaxEdits", FuzzyQuery.defaultMaxEdits, 2)
eq("defaultPrefixLength", FuzzyQuery.defaultPrefixLength, 0)
eq("defaultMaxExpansions", FuzzyQuery.defaultMaxExpansions, 50)
eq("defaultTranspositions", FuzzyQuery.defaultTranspositions, True)
eq("MAXIMUM_SUPPORTED_DISTANCE", MAXIMUM_SUPPORTED_DISTANCE, 2)
eq("defaultMaxEdits 就是上限",
   FuzzyQuery.defaultMaxEdits, MAXIMUM_SUPPORTED_DISTANCE)

q = FuzzyQuery("lucene")
eq("全默认 maxEdits", q.max_edits, 2)
eq("全默认 prefixLength", q.prefix_length, 0)
eq("全默认 maxExpansions", q.max_expansions, 50)
eq("全默认 transpositions", q.transpositions, True)

for bad, msg in ((3, "maxEdits"), (-1, "maxEdits")):
    try:
        FuzzyQuery("x", max_edits=bad)
        check("maxEdits=%d 应抛异常" % bad, False)
    except ValueError as e:
        check("maxEdits=%d 抛异常且消息含 maxEdits" % bad, "maxEdits must be between" in str(e))
try:
    FuzzyQuery("x", prefix_length=-1)
    check("prefixLength=-1 应抛异常", False)
except ValueError as e:
    check("prefixLength=-1 抛异常", "prefixLength cannot be negative" in str(e))
try:
    FuzzyQuery("x", max_expansions=0)
    check("maxExpansions=0 应抛异常", False)
except ValueError as e:
    check("maxExpansions=0 抛异常", "maxExpansions must be positive" in str(e))
# 边界值合法
eq("maxEdits=0 合法", FuzzyQuery("x", max_edits=0).max_edits, 0)
eq("maxEdits=2 合法", FuzzyQuery("x", max_edits=2).max_edits, 2)
eq("maxExpansions=1 合法", FuzzyQuery("x", max_expansions=1).max_expansions, 1)

# maxEdits == 0 → 只能精确匹配
eq("maxEdits=0 走 SingleTermsEnum", FuzzyQuery("x", max_edits=0).terms_enum_kind,
   "SingleTermsEnum")
eq("maxEdits=1 走 FuzzyTermsEnum", FuzzyQuery("x", max_edits=1).terms_enum_kind,
   "FuzzyTermsEnum")

# =========================================================== 2. floatToEdits
eq("similarity=0 → 0（是精确，不是无限）", float_to_edits(0.0, 10), 0)
eq("similarity=1 → 1", float_to_edits(1.0, 10), 1)
eq("similarity=2 → 2", float_to_edits(2.0, 10), 2)
eq("similarity=5 被夹到 2", float_to_edits(5.0, 10), 2)
# 二进制浮点：1 - 0.8 = 0.19999999999999996，乘 10 = 1.9999999999999996
# → (int) 截断成 **1** 而不是 2。这是源码 `(int)((1D - minimumSimilarity) * termLen)`
# 的真实行为，不是四舍五入。
eq("similarity=0.8 termLen=10 → 1（浮点截断）", float_to_edits(0.8, 10), 1)
# (1 - 0.7) * 10 = 3.0000000000000004 → 截断成 3，再被夹到 2
eq("similarity=0.7 termLen=10 → 夹到 2", float_to_edits(0.7, 10), 2)
# (1 - 0.9) * 5 = 0.5 → 0
eq("similarity=0.9 termLen=5 → 0", float_to_edits(0.9, 5), 0)
# 词越短，同样相似度允许的编辑数越少
check("termLen 越小编辑数越小", float_to_edits(0.6, 4) <= float_to_edits(0.6, 20))

# ============================================ 3. 编辑距离：transpositions 开关
# "ab" ↔ "ba"：经典 Levenshtein 要 2 次（两次替换），OSA 只需 1 次交换
eq("OSA: ab→ba = 1", damerau_osa("ab", "ba"), 1)
eq("Levenshtein: ab→ba = 2", levenshtein("ab", "ba"), 2)
eq("空串到 abc", levenshtein("", "abc"), 3)
eq("相等为 0", distance("same", "same", True), 0)
# OSA 的「一个字符只能参与一次交换」：CA 与 ABC
# 真正的 Damerau-Levenshtein 给 2，OSA 给 3
eq("OSA 对 CA→ABC 给 3（区别于真 DL 的 2）", damerau_osa("CA", "ABC"), 3)
check("transpositions 开时距离不大于经典",
      distance("ab", "ba", True) <= distance("ab", "ba", False))
eq("相同输入下关闭开关等于经典", distance("kitten", "sitting", False),
   levenshtein("kitten", "sitting"))
eq("kitten→sitting = 3", levenshtein("kitten", "sitting"), 3)

# ================================================ 4. LevenshteinAutomata 构造
la = LevenshteinAutomata([ord(c) for c in "abca"])
eq("字母表去重排序", la.alphabet, [97, 98, 99])
eq("补集区间数", la.num_ranges, 2)
eq("补集下界", la.range_lower, [0, 100])
eq("补集上界", la.range_upper, [96, CHARACTER_MAX_CODE_POINT])

la2 = LevenshteinAutomata([ord(c) for c in "aa"])
eq("单字符字母表", la2.alphabet, [97])
eq("单字符补集区间数", la2.num_ranges, 2)
eq("单字符补集下界", la2.range_lower, [0, 98])

la3 = LevenshteinAutomata([ord("a")], alpha_max=ord("a"))
eq("alphaMax 刚好等于字符 → 只有一段补集", la3.num_ranges, 1)
eq("该补集是 [0, a-1]", [la3.range_lower[0], la3.range_upper[0]], [0, 96])

try:
    LevenshteinAutomata([ord("b")], alpha_max=ord("a"))
    check("alphaMax 越界应抛异常", False)
except ValueError as e:
    check("alphaMax 越界抛异常", "alphaMax exceeded" in str(e))

# ============================================ 5. 参数化描述：状态数与接受态
d1 = Lev1ParametricDescription(5)
eq("Lev1 minErrors", d1.min_errors, [0, 1, 0, -1, -1])
eq("Lev1 size = 5*(w+1)", d1.size(), 5 * 6)
d1t = Lev1TParametricDescription(5)
eq("Lev1T minErrors", d1t.min_errors, [0, 1, 0, -1, -1, -1])
eq("Lev1T size = 6*(w+1)", d1t.size(), 6 * 6)
d2 = Lev2ParametricDescription(5)
eq("Lev2 minErrors 长度", len(d2.min_errors), 30)
eq("Lev2 size = 30*(w+1)", d2.size(), 30 * 6)

# getPosition = absState % (w+1)
w = 5
for s in (0, 1, 6, 7, 29):
    eq("getPosition(%d)" % s, d1.get_position(s), s % (w + 1))

# isAccept: w - offset + minErrors[state] <= n
for s in range(d1.size()):
    want = (w - (s % (w + 1)) + d1.min_errors[s // (w + 1)]) <= 1
    eq("Lev1 isAccept(%d)" % s, d1.is_accept(s), want)
check("Lev1 至少有一个接受态", len(d1.accept_states()) > 0)
check("Lev2 接受态不少于 Lev1", len(d2.accept_states()) >= len(d1.accept_states()))
# 状态 0：offset=0, minErrors[0]=0 → w - 0 + 0 = 5 > 1 → 不接受
eq("Lev1 状态 0 不接受", d1.is_accept(0), False)
# offset == w 且 minErrors = 0 → w - w + 0 = 0 <= 1 → 接受
eq("Lev1 offset==w 且 err=0 接受", d1.is_accept(0 * (w + 1) + w), True)

# 关闭 transpositions 后 n=1 的描述换成 Lev1
la_nt = LevenshteinAutomata([ord(c) for c in "abca"], with_transpositions=False)
eq("非 T 变体 n=1 用 Lev1", la_nt.descriptions[1].size(), 5 * 5)
la_t = LevenshteinAutomata([ord(c) for c in "abca"], with_transpositions=True)
eq("T 变体 n=1 用 Lev1T", la_t.descriptions[1].size(), 6 * 5)

# ==================================================== 6. toAutomaton 的计划量
p0 = la.to_automaton_plan(0)
eq("n=0 退化成 makeString", p0["kind"], "makeString")
eq("n=0 拼出 prefix+word", p0["value"], "abca")
p0p = la.to_automaton_plan(0, prefix="pre")
eq("n=0 带前缀", p0p["value"], "preabca")
eq("n=3 不支持（超过 descriptions 长度）", la.to_automaton_plan(3)["kind"], "unsupported")

p1 = la.to_automaton_plan(1)
eq("n=1 range = 2n+1", p1["range"], 3)
eq("n=1 状态数", p1["numStates"], 6 * 5)
eq("n=1 转移数 = numStates * min(1+2n, |alphabet|)",
   p1["numTransitions"], 30 * min(3, 3))
p2 = la.to_automaton_plan(2)
eq("n=2 range", p2["range"], 5)
eq("n=2 转移数按 min(5, 3)=3", p2["numTransitions"], la.descriptions[2].size() * 3)
p1p = la.to_automaton_plan(1, prefix="xy")
eq("带前缀时状态数 +前缀长度", p1p["totalStates"], p1["numStates"] + 2)

# ==================================================== 7. 特征向量
# word = a b c a, getVector(x, pos, end)：从 pos 到 end 左移累积
# word = a b c a；每步是「先左移再判等置位」，所以最早进来的位在最高位
eq("vector(a,0,3) = 100b", la.get_vector(97, 0, 3), 0b100)
eq("vector(b,0,3) = 010b", la.get_vector(98, 0, 3), 0b010)
eq("vector(c,0,4) = 010b", la.get_vector(99, 0, 4), 0b010)
eq("vector(不在词里的字符,0,4) = 0", la.get_vector(122, 0, 4), 0)
# 窗口右移一位后，命中位也跟着变
eq("vector(a,1,4) = 001b", la.get_vector(97, 1, 4), 0b001)
# n=1 时窗口 = 2n+1 = 3
v, end = la.vector_window(97, 0, 1)
eq("n=1 窗口长度 3", end, 3)
eq("n=1 窗口内向量", v, 0b100)
# 窗口末尾不会越界：pos=3 时 min(w-pos, 3) = 1
v2, end2 = la.vector_window(97, 3, 2)
eq("窗口在词尾被夹住", end2, 4)
eq("末位命中 → 1", v2, 1)

print("PASS=%d FAIL=%d" % (PASS, len(FAIL)))
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
