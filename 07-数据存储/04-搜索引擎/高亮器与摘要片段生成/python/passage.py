"""Lucene UnifiedHighlighter 的 Passage 与 PassageScorer。

忠实转写自 apache/lucene@main：
  lucene/highlighter/src/java/org/apache/lucene/search/uhighlight/Passage.java
  lucene/highlighter/src/java/org/apache/lucene/search/uhighlight/PassageScorer.java

口径：PassageScorer 里所有运算在 Java 侧都是 **float**（binary32）。源码注释自己都写了
"this formula is completely made up"，数值没有「正确答案」，但每一步的**舍入**是确定的，
所以本 demo 在每个 float 边界上都显式 round 一次（f32），便于和 JVM 对拍。
"""

import math
import struct


def f32(x):
    """round 到 IEEE-754 binary32（对应 Java 的 float 赋值 / 返回）。"""
    return struct.unpack("<f", struct.pack("<f", x))[0]


class Passage(object):
    """一段候选摘要：起止偏移 + 里面的命中（term、位置、该词在全文中的频次）。"""

    def __init__(self):
        self.start_offset = -1
        self.end_offset = -1
        self.score = 0.0
        self.match_starts = []
        self.match_ends = []
        self.match_terms = []
        self.match_freqs = []

    def add_match(self, start_offset, end_offset, term, term_freq_in_doc):
        # 源码 assert：命中必须落在本 passage 的区间内
        assert self.start_offset <= start_offset <= self.end_offset, \
            "match [%d,%d) 不在 passage [%d,%d) 内" % (
                start_offset, end_offset, self.start_offset, self.end_offset)
        self.match_starts.append(start_offset)
        self.match_ends.append(end_offset)
        self.match_terms.append(term)
        self.match_freqs.append(term_freq_in_doc)

    def reset(self):
        self.start_offset = self.end_offset = -1
        self.score = 0.0
        self.match_starts = []
        self.match_ends = []
        self.match_terms = []
        self.match_freqs = []

    def num_matches(self):
        return len(self.match_starts)

    def length(self):
        """getLength() = endOffset - startOffset。"""
        return self.end_offset - self.start_offset

    def __str__(self):
        """照抄 Passage.toString：Passage[0-22]{yin[0-3],yang[4-8]}score=2.4964213"""
        parts = []
        for i in range(self.num_matches()):
            parts.append("%s[%d-%d]" % (self.match_terms[i],
                                        self.match_starts[i] - self.start_offset,
                                        self.match_ends[i] - self.start_offset))
        return "Passage[%d-%d]{%s}score=%s" % (
            self.start_offset, self.end_offset, ",".join(parts), self.score)


class PassageScorer(object):
    """把每个 passage 当成一篇小文档打分：norm * Σ(weight * tf)。

    默认 k1=1.2、b=0.75、pivot=87（源码注释：87 是英语句子的典型长度）。
    """

    def __init__(self, k1=1.2, b=0.75, pivot=87.0):
        self.k1 = f32(k1)
        self.b = f32(b)
        self.pivot = f32(pivot)

    def weight(self, content_length, total_term_freq):
        """词的重要度：用「内容长度 / pivot」近似 numDocs，再套一个 DFR 味的 log。

        Java 侧 `1 + contentLength / pivot` 是 **int / float** 的 float 除法。
        """
        num_docs = f32(1.0 + f32(f32(content_length) / self.pivot))
        w = f32(math.log(1.0 + (num_docs + 0.5) / (total_term_freq + 0.5)))
        return f32(f32(self.k1 + 1.0) * w)

    def tf(self, freq, passage_len):
        """passage 内的词频饱和函数，带长度归一化。"""
        n1 = f32(1.0 - self.b)
        n2 = f32(self.b * f32(f32(passage_len) / self.pivot))
        norm = f32(self.k1 * f32(n1 + n2))
        return f32(f32(freq) / f32(f32(freq) + norm))

    def norm(self, passage_start):
        """位置加成：越靠前的 passage 越像是摘要。1 + 1/log(pivot + start)。"""
        return f32(1.0 + f32(1.0 / f32(math.log(self.pivot + passage_start))))

    def score(self, passage, content_length):
        """Σ tf(词在 passage 内频次, passage 长度) * weight(全文长度, 词在全文频次)，再乘 norm。

        同一个词在 passage 里出现多次只算一次「词」，但 tf 用 passage 内的**出现次数**；
        termFreqsInDoc 取**第一次遇到**该词时记录的全文频次。
        """
        total = 0.0                 # Java 侧是 double
        index_of = {}
        freqs_in_passage = []
        freqs_in_doc = []
        for i in range(passage.num_matches()):
            t = passage.match_terms[i]
            if t in index_of:
                ti = index_of[t]
            else:
                ti = len(freqs_in_passage)
                index_of[t] = ti
                freqs_in_passage.append(0)
                freqs_in_doc.append(passage.match_freqs[i])
            freqs_in_passage[ti] += 1
        plen = passage.length()
        for i in range(len(freqs_in_passage)):
            total += f32(self.tf(freqs_in_passage[i], plen) *
                         self.weight(content_length, freqs_in_doc[i]))
        total *= self.norm(passage.start_offset)
        return f32(total)
