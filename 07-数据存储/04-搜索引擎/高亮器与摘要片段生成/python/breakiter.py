"""BreakIterator 与 LengthGoalBreakIterator。

忠实转写自 apache/lucene@main：
  lucene/highlighter/src/java/org/apache/lucene/search/uhighlight/LengthGoalBreakIterator.java

Java 的 java.text.BreakIterator 在本 demo 里用「一组有序边界点」建模：
0 与 endIndex 恒为边界。语义对齐 JDK 文档：
  first()/last()  首个/末个边界
  next()          current 之后的下一个边界，没有则 DONE
  preceding(off)  **严格小于** off 的最后一个边界，没有则 DONE
  following(off)  **严格大于** off 的第一个边界，没有则 DONE
"""

import struct


DONE = -1


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


class BreakIterator(object):
    """句子/整段边界迭代器（stateful，set_text 会重置游标）。"""

    def __init__(self):
        self._bounds = ()
        self._end = 0
        self._cur = 0

    # ---- 构造：由「边界点」直接给出，避免依赖 ICU ----
    def set_boundaries(self, text_len, inner):
        """inner: 位于 (0, text_len) 开区间内的内部边界点。"""
        b = sorted(set([0] + [i for i in inner if 0 < i < text_len] + [text_len]))
        self._bounds = tuple(b)
        self._end = text_len
        self._cur = 0
        return self

    # ---- CharacterIterator 侧（Lucene 只用到 getEndIndex） ----
    def set_text(self, text):
        self._end = len(text)
        self._cur = 0
        return self

    def text_length(self):
        return self._end

    # ---- 游标 ----
    def current(self):
        return self._cur

    def first(self):
        self._cur = self._bounds[0] if self._bounds else 0
        return self._cur

    def last(self):
        self._cur = self._bounds[-1] if self._bounds else self._end
        return self._cur

    def next(self):
        for b in self._bounds:
            if b > self._cur:
                self._cur = b
                return b
        return DONE

    def preceding(self, offset):
        best = DONE
        for b in self._bounds:
            if b < offset:
                best = b
            else:
                break
        if best != DONE:
            self._cur = best
        return best

    def following(self, offset):
        for b in self._bounds:
            if b > offset:
                self._cur = b
                return b
        return DONE


class WholeBreakIterator(BreakIterator):
    """WholeBreakIterator：只有一个段落，边界就是 {0, end}。"""

    def set_boundaries(self, text_len, inner=()):
        return super(WholeBreakIterator, self).set_boundaries(text_len, [])


class LengthGoalBreakIterator(BreakIterator):
    """包装另一个 BreakIterator，跳过会让 passage 过短的断点。

    is_minimum_length=True  → 只取「不低于目标」的一侧（永不欠冲）
    is_minimum_length=False → 取离目标最近的那一侧（closest-to-length）
    """

    def __init__(self, base_iter, length_goal, fragment_alignment,
                 is_minimum_length, current_cache=0):
        if fragment_alignment < 0.0 or fragment_alignment > 1.0 or fragment_alignment != fragment_alignment:
            raise ValueError("fragmentAlignment must be >= zero and <= one")
        self.base = base_iter
        self.length_goal = length_goal
        self.fragment_alignment = f32(fragment_alignment)
        self.is_minimum_length = is_minimum_length
        self._cur = current_cache

    @staticmethod
    def create_min_length(base_iter, min_length, fragment_alignment):
        return LengthGoalBreakIterator(base_iter, min_length, fragment_alignment,
                                       True, base_iter.current())

    @staticmethod
    def create_closest_to_length(base_iter, target_length, fragment_alignment):
        return LengthGoalBreakIterator(base_iter, target_length, fragment_alignment,
                                       False, base_iter.current())

    # ---- 委托 ----
    def text_length(self):
        return self.base.text_length()

    def set_text(self, text):
        self.base.set_text(text)
        self._cur = self.base.current()
        return self

    def current(self):
        return self._cur

    def first(self):
        self._cur = self.base.first()
        return self._cur

    def last(self):
        self._cur = self.base.last()
        return self._cur

    def next(self):
        # return following(currentCache, currentCache + lengthGoal);
        return self._following2(self._cur, self._cur + self.length_goal)

    # ---- 两个核心方法 ----
    def following(self, match_end_index):
        # targetIdx = (matchEndIndex + 1) + (int)(lengthGoal * (1.f - fragmentAlignment))
        delta = int(f32(self.length_goal * f32(1.0 - self.fragment_alignment)))
        return self._following2(match_end_index, (match_end_index + 1) + delta)

    def _following2(self, match_end_index, target_idx):
        if target_idx >= self.text_length():
            if self._cur == self.base.last():
                return DONE
            self._cur = self.base.last()
            return self._cur
        after_idx = self.base.following(target_idx - 1)
        if after_idx == DONE:
            self._cur = self.base.last()
            return DONE
        if after_idx == target_idx:          # right on the money
            self._cur = after_idx
            return self._cur
        if self.is_minimum_length:           # thus never undershoot
            self._cur = after_idx
            return self._cur
        before_idx = self.base.preceding(target_idx)
        if (target_idx - before_idx < after_idx - target_idx
                and before_idx > match_end_index):
            self._cur = before_idx
            return self._cur
        self._cur = after_idx
        return self._cur

    def preceding(self, match_start_index):
        # targetIdx = (matchStartIndex - 1) - (int)(lengthGoal * fragmentAlignment)
        delta = int(f32(self.length_goal * self.fragment_alignment))
        target_idx = (match_start_index - 1) - delta
        if target_idx <= 0:
            if self._cur == self.base.first():
                return DONE
            self._cur = self.base.first()
            return self._cur
        before_idx = self.base.preceding(target_idx + 1)
        if before_idx == DONE:
            self._cur = self.base.first()
            return DONE
        if before_idx == target_idx:         # right on the money
            self._cur = before_idx
            return self._cur
        if self.is_minimum_length:           # thus never undershoot
            self._cur = before_idx
            return self._cur
        after_idx = self.base.following(target_idx - 1)
        if (after_idx - target_idx < target_idx - before_idx
                and after_idx < match_start_index):
            self._cur = after_idx
            return self._cur
        self._cur = before_idx
        return self._cur

    def __str__(self):
        goal = "minLen" if self.is_minimum_length else "targetLen"
        return "LengthGoalBreakIterator{%s=%d, fragAlign=%s}" % (
            goal, self.length_goal, self.fragment_alignment)
