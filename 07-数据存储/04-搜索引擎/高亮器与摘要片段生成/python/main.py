"""高亮器与摘要片段生成 —— 演示入口。

跑一遍完整的 UnifiedHighlighter 链路：
  偏移源判定 → 命中偏移流 → 按 BreakIterator 切 passage → 打分择优 → 格式化
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from breakiter import BreakIterator, LengthGoalBreakIterator
from passage import PassageScorer
from highlighter import (FieldHighlighter, OffsetsEnum, DefaultPassageFormatter,
                         get_offset_source, get_optimized_offset_source,
                         DOCS_AND_FREQS_AND_POSITIONS_AND_OFFSETS)

CONTENT = ("Lucene is a search library. Lucene builds an inverted index. "
           "Highlighters cut passages from matched offsets. The best passages win.")

def sent_bounds(text):
    """句子边界 = 每个 '. ' 之后的下标（0 与 len 由 BreakIterator 自动补上）。"""
    out = []
    i = 0
    while True:
        j = text.find(". ", i)
        if j < 0:
            break
        out.append(j + 2)
        i = j + 2
    return out


BOUNDS = sent_bounds(CONTENT)


def loc(word):
    i = CONTENT.index(word)
    return i, i + len(word)


def main():
    print("=" * 72)
    print("Lucene UnifiedHighlighter 链路演示")
    print("=" * 72)
    print("正文(%d 字符): %s" % (len(CONTENT), CONTENT))
    print("句子边界: %s\n" % ([0] + BOUNDS + [len(CONTENT)]))

    # 1. 偏移源判定
    fi = {"index_options": DOCS_AND_FREQS_AND_POSITIONS_AND_OFFSETS,
          "has_term_vectors": False}
    src = get_offset_source(fi)
    print("1) 偏移源: %s  (有 offsets + 无词向量)" % src)
    print("   同一个字段来了多词查询 → %s  (POSTINGS 会被降级)"
          % get_optimized_offset_source(src, True, ["luc*"]))
    print("   命中集合为空且无需改写 → %s\n"
          % get_optimized_offset_source(src, False, []))

    # 2. 打分器
    s = PassageScorer()
    print("2) PassageScorer 默认 k1=%.1f b=%.2f pivot=%.0f" % (s.k1, s.b, s.pivot))
    print("   weight(全文%d, 词频2) = %.6f" % (len(CONTENT), s.weight(len(CONTENT), 2)))
    print("   tf(词频2, 段长20)     = %.6f" % s.tf(2, 20))
    print("   norm(段起点0)         = %.6f" % s.norm(0))
    print("   norm(段起点100)       = %.6f  ← 越靠前越值钱\n" % s.norm(100))

    # 3. 切分 + 择优
    matches = [loc("Lucene") + ("lucene", 2),
               loc("inverted") + ("invert", 1),
               loc("index") + ("index", 1),
               loc("Highlighters") + ("highlight", 1),
               loc("passages") + ("passag", 2)]
    bi = BreakIterator()
    bi.set_boundaries(len(CONTENT), BOUNDS)
    bi.set_text(CONTENT)
    fh = FieldHighlighter("body", bi, s, 3, formatter=DefaultPassageFormatter())
    passages = fh.highlight_offsets_enums(OffsetsEnum(matches))
    print("3) 切成 %d 段并打分（maxPassages=3）:" % len(passages))
    for p in passages:
        print("   %s" % p)

    # 4. 择优
    bi2 = BreakIterator()
    bi2.set_boundaries(len(CONTENT), BOUNDS)
    bi2.set_text(CONTENT)
    fh1 = FieldHighlighter("body", bi2, s, 1)
    best = fh1.highlight_offsets_enums(OffsetsEnum(matches))
    print("\n4) maxPassages=1 只留最高分: [%d,%d) score=%.6f"
          % (best[0].start_offset, best[0].end_offset, best[0].score))

    # 5. 格式化
    print("\n5) 格式化输出:")
    print("   %s" % fh.formatter.format(passages, CONTENT))

    # 6. 无命中时的兜底摘要
    bi3 = BreakIterator()
    bi3.set_boundaries(len(CONTENT), BOUNDS)
    bi3.set_text(CONTENT)
    fh2 = FieldHighlighter("body", bi3, s, 2)
    summ = fh2.get_summary_passages_no_highlight(2)
    print("\n6) 无命中兜底: 取前 %d 段 %s"
          % (len(summ), [(p.start_offset, p.end_offset) for p in summ]))

    # 7. 长度目标：min vs closest
    base = BreakIterator()
    base.set_boundaries(60, [10, 20, 30, 40, 50])
    base.set_text("z" * 60)
    mn = LengthGoalBreakIterator.create_min_length(base, 10, 0.5)
    base2 = BreakIterator()
    base2.set_boundaries(60, [10, 20, 30, 40, 50])
    base2.set_text("z" * 60)
    cl = LengthGoalBreakIterator.create_closest_to_length(base2, 10, 0.5)
    print("\n7) 边界集 {0,10,20,30,40,50,60}，lengthGoal=10，alignment=0.5")
    print("   minLength     .preceding(23) = %d   (永不欠冲)" % mn.preceding(23))
    print("   closestToLength.preceding(23) = %d   (取最近)" % cl.preceding(23))

    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()
