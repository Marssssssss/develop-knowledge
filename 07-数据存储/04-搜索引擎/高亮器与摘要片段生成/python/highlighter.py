"""FieldHighlighter：把命中偏移流切成 passage、打分、择优，再格式化成摘要片段。

忠实转写自 apache/lucene@main：
  .../uhighlight/FieldHighlighter.java          （highlightOffsetsEnums / maybeAddPassage /
                                                  getSummaryPassagesNoHighlight）
  .../uhighlight/UnifiedHighlighter.java        （OffsetSource 判定、getOptimizedOffsetSource）
  .../uhighlight/DefaultPassageFormatter.java   （format：重叠命中合并 + 省略号）

关键口径（全部来自源码，非推测）：
  * 打分是「把每个 passage 当一篇小文档」，score = norm(startOffset) * Σ(weight * tf)。
  * 择优用一个容量为 maxPassages 的**小顶堆**，堆顶是当前最差的那个。
  * 新 passage 打不过堆顶就直接 reset 复用（对象池），所以 Passage 实例数 ≤ maxPassages + 1。
  * 一个匹配若横跨内容末尾（start < contentLength && end > contentLength）会被**整条丢弃**。
"""

import heapq

from breakiter import DONE
from passage import Passage


# ---------------------------------------------------------------- OffsetSource

POSTINGS = "POSTINGS"
TERM_VECTORS = "TERM_VECTORS"
ANALYSIS = "ANALYSIS"
POSTINGS_WITH_TERM_VECTORS = "POSTINGS_WITH_TERM_VECTORS"
NONE_NEEDED = "NONE_NEEDED"

DOCS_AND_FREQS_AND_POSITIONS_AND_OFFSETS = "DOCS_AND_FREQS_AND_POSITIONS_AND_OFFSETS"


def get_offset_source(field_info):
    """UnifiedHighlighter#getOffsetSource：先查 FieldInfo，查不到就回落到 ANALYSIS。

    field_info = None 或 {"index_options": ..., "has_term_vectors": bool}
    """
    if field_info is not None:
        if field_info.get("index_options") == DOCS_AND_FREQS_AND_POSITIONS_AND_OFFSETS:
            return POSTINGS_WITH_TERM_VECTORS if field_info.get("has_term_vectors") else POSTINGS
        if field_info.get("has_term_vectors"):
            return TERM_VECTORS
    return ANALYSIS


def get_optimized_offset_source(offset_source, mtq_or_rewrite, terms):
    """UnifiedHighlighter#getOptimizedOffsetSource。

    两处「降级 / 升级」是反直觉的：
      * POSTINGS 遇到多词查询（需要扫全部 term）会**降级**成 ANALYSIS；
      * POSTINGS_WITH_TERM_VECTORS 若确实不需要词向量会**升级**成 POSTINGS。
      * 命中集合为空且无需改写 → NONE_NEEDED，连偏移都不用取。
    """
    if mtq_or_rewrite is False and terms is not None and len(terms) == 0:
        return NONE_NEEDED
    if offset_source == POSTINGS:
        if mtq_or_rewrite:
            return ANALYSIS
    elif offset_source == POSTINGS_WITH_TERM_VECTORS:
        if not mtq_or_rewrite:
            return POSTINGS
    return offset_source


# ------------------------------------------------------------ OffsetsEnum 抽象

class OffsetsEnum(object):
    """命中偏移流。每个元素 = (startOffset, endOffset, term, termFreqInDoc)。

    startOffset == -1 表示该字段没按 offsets 索引，Lucene 会直接抛异常。
    """

    def __init__(self, positions):
        self._pos = list(positions)
        self._i = -1

    def next_position(self):
        self._i += 1
        return self._i < len(self._pos)

    def start_offset(self):
        return self._pos[self._i][0]

    def end_offset(self):
        return self._pos[self._i][1]

    def term(self):
        return self._pos[self._i][2]

    def freq(self):
        return self._pos[self._i][3]


# -------------------------------------------------------------- passage 择优堆

class _PassageQueue(object):
    """小顶堆：score 升序，同分按 startOffset 升序（照抄 FieldHighlighter 的 comparator）。"""

    def __init__(self):
        self._seq = 0
        self._heap = []

    def __len__(self):
        return len(self._heap)

    def peek(self):
        return self._heap[0][2]

    def offer(self, p):
        self._seq += 1
        heapq.heappush(self._heap, (p.score, p.start_offset, p, self._seq))

    def poll(self):
        return heapq.heappop(self._heap)[2]

    def to_list(self):
        return [item[2] for item in self._heap]


# ---------------------------------------------------------------- FieldHighlighter

class FieldHighlighter(object):

    def __init__(self, field, break_iterator, scorer, max_passages,
                 max_no_highlight_passages=-1,
                 formatter=None, sort_by_score=True):
        self.field = field
        self.bi = break_iterator
        self.scorer = scorer
        self.max_passages = max_passages
        self.max_no_highlight_passages = max_no_highlight_passages
        self.formatter = formatter or DefaultPassageFormatter()
        self.sort_by_score = sort_by_score

    def highlight_field_for_doc(self, off, content):
        if len(content) == 0:
            return None
        self.bi.set_text(content)
        passages = self.highlight_offsets_enums(off)
        if len(passages) == 0:
            n = self.max_passages if self.max_no_highlight_passages == -1 \
                else self.max_no_highlight_passages
            passages = self.get_summary_passages_no_highlight(n)
        if len(passages) > 0:
            return self.formatter.format(passages, content)
        return None

    def get_summary_passages_no_highlight(self, max_passages):
        """没有命中时的兜底摘要：从 breakIterator 当前位置（应为 0）起取前 N 段。"""
        self.bi.first()
        passages = []
        pos = self.bi.current()
        while len(passages) < max_passages:
            nxt = self.bi.next()
            if nxt == DONE:
                break
            p = Passage()
            p.set_start_offset(pos)
            p.set_end_offset(nxt)
            passages.append(p)
            pos = nxt
        return passages

    def highlight_offsets_enums(self, off):
        content_length = self.bi.text_length()
        if not off.next_position():
            return []

        queue = _PassageQueue()
        passage = Passage()
        last_passage_end = 0

        while True:
            start = off.start_offset()
            if start == -1:
                raise ValueError(
                    "field '%s' was indexed without offsets, cannot highlight" % self.field)
            end = off.end_offset()
            if start < content_length and end > content_length:
                if not off.next_position():
                    break
                continue
            if start >= passage.end_offset:
                passage = self._maybe_add_passage(queue, passage, content_length)
                if start >= content_length:
                    break
                # 从命中「中点」往两侧找断点，让片段长度更接近 fragsize
                center = start + (end - start) // 2
                passage.set_start_offset(min(
                    start,
                    max(self.bi.preceding(max(start + 1, center)), last_passage_end)))
                last_passage_end = max(
                    end,
                    min(self.bi.following(min(end - 1, center)), content_length))
                passage.set_end_offset(last_passage_end)
            passage.add_match(start, end, off.term(), off.freq())
            if not off.next_position():
                break

        self._maybe_add_passage(queue, passage, content_length)
        passages = queue.to_list()
        if self.sort_by_score:
            passages.sort(key=lambda p: p.start_offset)
        return passages

    def _maybe_add_passage(self, queue, passage, content_length):
        if passage.start_offset == -1:
            return passage                      # 空 passage，忽略
        passage.set_score(self.scorer.score(passage, content_length))
        if len(queue) == self.max_passages and passage.score < queue.peek().score:
            passage.reset()                     # 打不过最差的，复用这个对象
        else:
            queue.offer(passage)
            if len(queue) > self.max_passages:
                passage = queue.poll()
                passage.reset()                 # 淘汰堆顶并复用
            else:
                passage = Passage()
        return passage


# ------------------------------------------------------- DefaultPassageFormatter

class DefaultPassageFormatter(object):
    """默认格式器：<b> 加粗命中，段落之间用 "... " 连接。"""

    def __init__(self, pre_tag="<b>", post_tag="</b>", ellipsis="... ", escape=False):
        if pre_tag is None or post_tag is None or ellipsis is None:
            raise ValueError("tags must not be null")
        self.pre_tag = pre_tag
        self.post_tag = post_tag
        self.ellipsis = ellipsis
        self.escape = escape

    def format(self, passages, content):
        out = []
        pos = 0
        for passage in passages:
            # 第一段不加省略号；与上文相连时也不加
            if out and passage.start_offset != pos:
                out.append(self.ellipsis)
            pos = passage.start_offset
            i = 0
            n = passage.num_matches()
            while i < n:
                start = passage.match_starts[i]
                out.append(self._slice(content, pos, start))
                end = passage.match_ends[i]
                # 命中之间可能重叠：向前吞掉所有重叠区间，取最大的 end
                while i + 1 < n and passage.match_starts[i + 1] < end:
                    i += 1
                    end = max(end, passage.match_ends[i])
                end = min(end, passage.end_offset)   # 命中可能越出 passage
                out.append(self.pre_tag)
                out.append(self._slice(content, start, end))
                out.append(self.post_tag)
                pos = end
                i += 1
            out.append(self._slice(content, pos, max(pos, passage.end_offset)))
            pos = passage.end_offset
        return "".join(out)

    def _slice(self, content, start, end):
        s = content[start:end]
        if not self.escape:
            return s
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
