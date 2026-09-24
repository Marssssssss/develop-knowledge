"""高亮器与摘要片段生成 —— 自检。

每条断言都对应 Lucene 源码里一处**可判定的**行为，期望值由源码公式手算 + 实跑双向校准。
浮点容差 1e-9（PassageScorer 全程 float，转写侧已逐点 round 到 binary32）。

运行：python selfcheck_highlight.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from breakiter import BreakIterator, LengthGoalBreakIterator, DONE
from passage import Passage, PassageScorer
from highlighter import (FieldHighlighter, OffsetsEnum, DefaultPassageFormatter,
                         get_offset_source, get_optimized_offset_source,
                         POSTINGS, TERM_VECTORS, ANALYSIS,
                         POSTINGS_WITH_TERM_VECTORS, NONE_NEEDED,
                         DOCS_AND_FREQS_AND_POSITIONS_AND_OFFSETS)

PASS = 0
FAIL = []


def check(name, cond):
    global PASS
    if cond:
        PASS += 1
    else:
        FAIL.append(name)


def close(name, got, want, tol=1e-9):
    check("%s (got=%r want=%r)" % (name, got, want), abs(got - want) <= tol)


def eq(name, got, want):
    check("%s (got=%r want=%r)" % (name, got, want), got == want)


def mk(bounds, n=60):
    b = BreakIterator()
    b.set_boundaries(n, bounds)
    b.set_text("y" * n)
    return b


# ============================================================ 1. PassageScorer
S = PassageScorer()

eq("默认 k1", S.k1, 1.2000000476837158)
eq("默认 b", S.b, 0.75)
eq("默认 pivot", S.pivot, 87.0)

# weight(100, 2)：numDocs = 1 + 100/87，再 (k1+1)*log(1 + (numDocs+0.5)/(tf+0.5))
close("weight(100,2)", S.weight(100, 2), 1.589707612991333)
# 全文越长 → 近似 numDocs 越大 → 词越"稀有"，weight 越大
check("weight 随 contentLength 单调增", S.weight(1000, 2) > S.weight(100, 2))
# 词在全文出现越多 → 越不稀有 → weight 越小
check("weight 随 totalTermFreq 单调减", S.weight(100, 20) < S.weight(100, 2))

# tf(2, 20)：norm = k1*((1-b) + b*(20/87))
close("tf(2,20)", S.tf(2, 20), 0.7977991700172424)
check("tf 饱和：freq 越大增益越小",
      S.tf(4, 20) - S.tf(2, 20) < S.tf(2, 20) - S.tf(1, 20))
# 长 passage 的长度归一化会压低 tf
check("tf 随 passageLen 递减", S.tf(2, 200) < S.tf(2, 20))

# norm(start) = 1 + 1/log(pivot + start)：越靠前越大
close("norm(0)", S.norm(0), 1.2239186763763428)
check("norm 随 startOffset 递减", S.norm(1000) < S.norm(0))
# pivot + start 必须 > 1，否则 log 非正；start=0 时 log(87) 正常

# 组合：同一 passage 两词
p = Passage()
p.set_start_offset(0)
p.set_end_offset(20)
p.add_match(0, 5, "alpha", 3)
p.add_match(6, 10, "beta", 1)
close("两词 passage 打分", S.score(p, 60), 2.4765143394470215)
# 同一词出现两次：只算一个 term，但 tf 用出现次数 2，freqInDoc 取首次记录值
p2 = Passage()
p2.set_start_offset(0)
p2.set_end_offset(20)
p2.add_match(0, 5, "alpha", 3)
p2.add_match(6, 10, "alpha", 3)
check("重复词只贡献一个 term 但 tf 更高", S.score(p2, 60) > S.score(
    (lambda q: (q.set_start_offset(0), q.set_end_offset(20),
                q.add_match(0, 5, "alpha", 3), q)[-1])(Passage()), 60))

# Passage 本身
eq("Passage 初始 startOffset", Passage().start_offset, -1)
eq("Passage 初始 endOffset", Passage().end_offset, -1)
p3 = Passage()
p3.set_start_offset(5)
p3.set_end_offset(15)
eq("getLength", p3.length(), 10)
p3.set_score(2.5)
eq("toString 相对偏移", str(p3), "Passage[5-15]{}score=2.5")
p3.reset()
eq("reset 后 startOffset", p3.start_offset, -1)
eq("reset 后 numMatches", p3.num_matches(), 0)

# ================================================== 2. BreakIterator 基本语义
b = mk([20, 40])
eq("first", b.first(), 0)
eq("last", b.last(), 60)
b.first()
eq("next", b.next(), 20)
eq("preceding 严格小于", b.preceding(25), 20)
eq("following 严格大于", b.following(25), 40)
eq("preceding(0) = DONE", b.preceding(0), DONE)
eq("following(end) = DONE", b.following(60), DONE)
check("0 与 endIndex 恒为边界", b._bounds[0] == 0 and b._bounds[-1] == 60)

# ======================================== 3. LengthGoalBreakIterator 两种模式
# lengthGoal=10, alignment=0.5 → delta = 5
# preceding(23): target = 22-5 = 17；before=10, after=20 → 距 after(3) < 距 before(7)
mn = LengthGoalBreakIterator.create_min_length(mk([10, 20, 30, 40, 50]), 10, 0.5)
cl = LengthGoalBreakIterator.create_closest_to_length(mk([10, 20, 30, 40, 50]), 10, 0.5)
eq("minLength preceding 永不欠冲", mn.preceding(23), 10)
eq("closestToLength preceding 取最近", cl.preceding(23), 20)

mn2 = LengthGoalBreakIterator.create_min_length(mk([10, 20, 30, 40, 50]), 10, 0.5)
cl2 = LengthGoalBreakIterator.create_closest_to_length(mk([10, 20, 30, 40, 50]), 10, 0.5)
# following(5): target = 6 + int(10*(1-0.5)) = 11 → before=10 更近，但 min 不允许欠冲
eq("minLength following 永不欠冲", mn2.following(5), 20)
eq("closestToLength following 取最近", cl2.following(5), 10)

# fragmentAlignment 越界
try:
    LengthGoalBreakIterator.create_min_length(mk([10]), 10, 1.5)
    check("alignment>1 应抛异常", False)
except ValueError:
    check("alignment>1 抛 ValueError", True)

# alignment=0 时命中紧贴片段左缘，alignment=1 时紧贴右缘
a0 = LengthGoalBreakIterator.create_min_length(mk([10, 20, 30, 40, 50]), 10, 0.0)
a1 = LengthGoalBreakIterator.create_min_length(mk([10, 20, 30, 40, 50]), 10, 1.0)
eq("alignment=0 preceding 目标右移", a0.preceding(23), 20)
eq("alignment=1 preceding 目标左移", a1.preceding(23), 10)

# ================================================ 4. OffsetSource 判定
FI_OFFSETS = {"index_options": DOCS_AND_FREQS_AND_POSITIONS_AND_OFFSETS,
              "has_term_vectors": False}
eq("offsets+无TV → POSTINGS", get_offset_source(FI_OFFSETS), POSTINGS)
eq("offsets+有TV → POSTINGS_WITH_TERM_VECTORS",
   get_offset_source({"index_options": DOCS_AND_FREQS_AND_POSITIONS_AND_OFFSETS,
                      "has_term_vectors": True}), POSTINGS_WITH_TERM_VECTORS)
eq("只有TV → TERM_VECTORS",
   get_offset_source({"index_options": "DOCS", "has_term_vectors": True}), TERM_VECTORS)
eq("啥都没有 → ANALYSIS", get_offset_source({"index_options": "DOCS"}), ANALYSIS)
eq("字段不存在 → ANALYSIS", get_offset_source(None), ANALYSIS)
# 优化：POSTINGS 遇上需要扫全部 term 的多词查询会**降级**为 ANALYSIS
eq("POSTINGS+MTQ 降级", get_optimized_offset_source(POSTINGS, True, ["a"]), ANALYSIS)
eq("POSTINGS 无 MTQ 保持", get_optimized_offset_source(POSTINGS, False, ["a"]), POSTINGS)
eq("PWTV 不需要TV 升级", get_optimized_offset_source(POSTINGS_WITH_TERM_VECTORS, False, ["a"]), POSTINGS)
eq("命中集为空 → NONE_NEEDED", get_optimized_offset_source(POSTINGS, False, []), NONE_NEEDED)
eq("terms 未知(None) 不判空", get_optimized_offset_source(POSTINGS, False, None), POSTINGS)

# ============================================ 5. 切分 / 打分 / 择优
CONTENT = "alpha beta gamma. delta epsilon zeta. eta theta iota. kappa lambda mu."
BOUNDS = [18, 38, 54]


def loc(w):
    i = CONTENT.index(w)
    return (i, i + len(w))


def fresh(matches, max_passages=5, **kw):
    hb = BreakIterator()
    hb.set_boundaries(len(CONTENT), BOUNDS)
    hb.set_text(CONTENT)
    fh = FieldHighlighter("body", hb, PassageScorer(), max_passages, **kw)
    return fh.highlight_offsets_enums(OffsetsEnum(matches))


ALL = [loc("alpha") + ("alpha", 1), loc("epsilon") + ("epsilon", 1),
       loc("theta") + ("theta", 2), loc("iota") + ("iota", 1)]

eq("内容长度", len(CONTENT), 70)
eq("epsilon 偏移", loc("epsilon"), (24, 31))

r = fresh(ALL, 3)
eq("切出 3 段", len(r), 3)
eq("第 1 段区间", (r[0].start_offset, r[0].end_offset), (0, 18))
eq("第 2 段区间", (r[1].start_offset, r[1].end_offset), (18, 38))
eq("第 3 段区间", (r[2].start_offset, r[2].end_offset), (38, 54))
eq("第 3 段含两个命中", r[2].num_matches(), 2)
close("第 1 段得分", r[0].score, 1.6862685680389404)
close("第 3 段得分", r[2].score, 2.870396852493286)
# 输出按 passageSortComparator（默认按 offset 升序）
check("输出按 startOffset 升序", [x.start_offset for x in r] == sorted(x.start_offset for x in r))

# 择优：只留 1 段时必须留下分最高的 [38,54)
r1 = fresh(ALL, 1)
eq("maxPassages=1 只剩 1 段", len(r1), 1)
eq("留下的是最高分段", (r1[0].start_offset, r1[0].end_offset), (38, 54))

# 命中跨越内容末尾 → 整条丢弃
r2 = fresh([loc("alpha") + ("alpha", 1), (66, 74, "mu", 1)], 5)
eq("越界命中被丢弃", len(r2), 1)
eq("剩下的仍是首段", (r2[0].start_offset, r2[0].end_offset), (0, 18))

# 没按 offsets 索引 → 抛异常
try:
    fresh([(-1, 5, "x", 1)], 5)
    check("offset=-1 应抛异常", False)
except ValueError as e:
    check("offset=-1 抛 ValueError", "was indexed without offsets" in str(e))

# 空偏移流 → 空结果（随后由上层走 summary 兜底）
eq("空偏移流", len(fresh([], 5)), 0)

# 兜底摘要：无命中时取前 N 段，且这些 Passage 没有任何 match
hb = BreakIterator()
hb.set_boundaries(len(CONTENT), BOUNDS)
hb.set_text(CONTENT)
fh = FieldHighlighter("body", hb, PassageScorer(), 3)
summ = fh.get_summary_passages_no_highlight(2)
eq("兜底摘要段数", len(summ), 2)
eq("兜底第 1 段", (summ[0].start_offset, summ[0].end_offset), (0, 18))
eq("兜底第 2 段", (summ[1].start_offset, summ[1].end_offset), (18, 38))
eq("兜底段无命中", summ[0].num_matches(), 0)
eq("maxNoHighlightPassages=-1 时沿用 maxPassages",
   fh.max_passages if fh.max_no_highlight_passages == -1 else -1, 3)

# ================================================= 6. DefaultPassageFormatter
hb = BreakIterator()
hb.set_boundaries(len(CONTENT), BOUNDS)
hb.set_text(CONTENT)
fh = FieldHighlighter("body", hb, PassageScorer(), 3,
                      formatter=DefaultPassageFormatter())
txt = fh.formatter.format(fresh(ALL, 3), CONTENT)
eq("格式化结果", txt,
   "<b>alpha</b> beta gamma. delta <b>epsilon</b> zeta. eta <b>theta</b> <b>iota</b>. ")
check("命中被 pre/postTag 包裹", txt.count("<b>") == 4 and txt.count("</b>") == 4)
check("段间相连不加省略号", "..." not in txt)

# 段与段不相连时才插省略号
pf = DefaultPassageFormatter()
pa = Passage(); pa.set_start_offset(0); pa.set_end_offset(4); pa.add_match(0, 4, "abcd", 1)
pb = Passage(); pb.set_start_offset(10); pb.set_end_offset(14); pb.add_match(10, 14, "klmn", 1)
eq("不相连插省略号", pf.format([pa, pb], "abcdefghijklmn"), "<b>abcd</b>... <b>klmn</b>")

# 重叠命中会被合并成一个更大的区间
ov = Passage(); ov.set_start_offset(0); ov.set_end_offset(8)
ov.add_match(0, 4, "abcd", 1)
ov.add_match(2, 6, "cdef", 1)
eq("重叠命中合并", pf.format([ov], "abcdefgh"), "<b>abcdef</b>gh")

# 命中越出 passage 末尾会被截断到 endOffset
ov2 = Passage(); ov2.set_start_offset(0); ov2.set_end_offset(4)
ov2.add_match(0, 9, "abcdefghi", 1)
eq("越出 passage 的命中被截断", pf.format([ov2], "abcdefghi"), "<b>abcd</b>")

# escape
# pa 是 [0,4)，所以只有前 4 个字符 "a&b<" 进入转义（owasp 规则：& < >）
eq("escape 会转义", DefaultPassageFormatter(escape=True).format([pa], "a&b<zzz"),
   "<b>a&amp;b&lt;</b>")

# null tag
try:
    DefaultPassageFormatter(pre_tag=None)
    check("preTag=None 应抛异常", False)
except ValueError:
    check("preTag=None 抛 ValueError", True)

# ==================================================== 7. 端到端
hb = BreakIterator()
hb.set_boundaries(len(CONTENT), BOUNDS)
fh = FieldHighlighter("body", hb, PassageScorer(), 2)
out = fh.highlight_field_for_doc(OffsetsEnum(ALL), CONTENT)
check("端到端产出非空", out is not None and "<b>" in out)
eq("空内容返回 None", fh.highlight_field_for_doc(OffsetsEnum([]), ""), None)

print("PASS=%d FAIL=%d" % (PASS, len(FAIL)))
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
