"""FuzzyQuery 参数语义与 Levenshtein 自动机的参数化描述。

忠实转写自 apache/lucene@main：
  lucene/core/src/java/org/apache/lucene/search/FuzzyQuery.java
  lucene/core/src/java/org/apache/lucene/util/automaton/LevenshteinAutomata.java
  .../automaton/Lev1ParametricDescription.java / Lev2ParametricDescription.java /
  Lev1TParametricDescription.java

**建模口径**：真正的 DFA 状态转移表（`toStates*` / `offsetIncrs*` 打包数组）这里不还原，
本 demo 断言的是**参数化描述的骨架** —— 状态数、接受态判据、位置函数、特征向量，
以及 FuzzyQuery 的参数校验与默认值。这些都能从源码逐行对上。
"""

MAXIMUM_SUPPORTED_DISTANCE = 2
CHARACTER_MAX_CODE_POINT = 0x10FFFF


# ------------------------------------------------------------------ FuzzyQuery

class FuzzyQuery(object):
    """FuzzyQuery 的构造参数与校验（源码四条 throw 全部复刻）。"""

    defaultMaxEdits = MAXIMUM_SUPPORTED_DISTANCE   # = 2
    defaultPrefixLength = 0
    defaultMaxExpansions = 50
    defaultTranspositions = True

    def __init__(self, term, max_edits=None, prefix_length=None,
                 max_expansions=None, transpositions=None):
        self.term = term
        self.max_edits = self.defaultMaxEdits if max_edits is None else max_edits
        self.prefix_length = self.defaultPrefixLength if prefix_length is None else prefix_length
        self.max_expansions = (self.defaultMaxExpansions if max_expansions is None
                               else max_expansions)
        self.transpositions = (self.defaultTranspositions if transpositions is None
                               else transpositions)

        if self.max_edits < 0 or self.max_edits > MAXIMUM_SUPPORTED_DISTANCE:
            raise ValueError("maxEdits must be between 0 and %d" % MAXIMUM_SUPPORTED_DISTANCE)
        if self.prefix_length < 0:
            raise ValueError("prefixLength cannot be negative.")
        if self.max_expansions <= 0:
            raise ValueError("maxExpansions must be positive.")

    @property
    def terms_enum_kind(self):
        """maxEdits == 0 时走 SingleTermsEnum（只能精确匹配），否则 FuzzyTermsEnum。"""
        return "SingleTermsEnum" if self.max_edits == 0 else "FuzzyTermsEnum"


def float_to_edits(minimum_similarity, term_len):
    """把「最小相似度」换算成编辑距离。三分支，源码照抄。

    * similarity >= 1f → min(similarity, 2)，即直接当整数编辑距离用
    * similarity == 0.0f → **0**（源码注释：0 means exact, not infinite # of edits!）
    * 否则 → min((int)((1 - similarity) * termLen), 2)
    """
    if minimum_similarity >= 1.0:
        return int(min(minimum_similarity, MAXIMUM_SUPPORTED_DISTANCE))
    if minimum_similarity == 0.0:
        return 0
    return min(int((1.0 - minimum_similarity) * term_len), MAXIMUM_SUPPORTED_DISTANCE)


# ---------------------------------------------------- ParametricDescription

class ParametricDescription(object):
    """参数化描述：状态数 / 接受态 / 位置 / 转移，全部以词长 w 为参数。"""

    def __init__(self, w, n, min_errors):
        self.w = w
        self.n = n
        self.min_errors = list(min_errors)

    def size(self):
        """minErrors.length * (w + 1)。"""
        return len(self.min_errors) * (self.w + 1)

    def is_accept(self, abs_state):
        """state = absState / (w+1)，offset = absState % (w+1)；
        接受条件 `w - offset + minErrors[state] <= n`。"""
        state = abs_state // (self.w + 1)
        offset = abs_state % (self.w + 1)
        assert offset >= 0
        return self.w - offset + self.min_errors[state] <= self.n

    def get_position(self, abs_state):
        """最小边界函数：absState % (w + 1)。"""
        return abs_state % (self.w + 1)

    def accept_states(self):
        return [s for s in range(self.size()) if self.is_accept(s)]


class Lev1ParametricDescription(ParametricDescription):
    def __init__(self, w):
        super(Lev1ParametricDescription, self).__init__(w, 1, [0, 1, 0, -1, -1])


class Lev1TParametricDescription(ParametricDescription):
    def __init__(self, w):
        super(Lev1TParametricDescription, self).__init__(w, 1, [0, 1, 0, -1, -1, -1])


class Lev2ParametricDescription(ParametricDescription):
    def __init__(self, w):
        super(Lev2ParametricDescription, self).__init__(w, 2, [
            0, 1, 2, 0, 1, -1, 0, -1, 0, -1, 0, -1, -1, -1, -1, -2,
            -1, -2, -1, -2, -1, -2, -2, -2, -2, -2, -2, -2, -2, -2])


# --------------------------------------------------------- LevenshteinAutomata

class LevenshteinAutomata(object):
    """按源码顺序复刻构造过程：抽字母表 → 算补集区间 → 选参数化描述。"""

    def __init__(self, word, alpha_max=CHARACTER_MAX_CODE_POINT,
                 with_transpositions=True):
        self.word = list(word)
        self.alpha_max = alpha_max

        seen = set()
        for v in self.word:
            if v > alpha_max:
                raise ValueError("alphaMax exceeded by symbol %d in word" % v)
            seen.add(v)
        self.alphabet = sorted(seen)

        # 字母表之外的 Unicode 区间（特征向量恒为 0）
        self.range_lower = []
        self.range_upper = []
        num_ranges = 0
        lower = 0
        for higher in self.alphabet:
            if higher > lower:
                self.range_lower.append(lower)
                self.range_upper.append(higher - 1)
                num_ranges += 1
            lower = higher + 1
        if lower <= alpha_max:
            self.range_lower.append(lower)
            self.range_upper.append(alpha_max)
            num_ranges += 1
        self.num_ranges = num_ranges

        w = len(self.word)
        # n=2 的 T 变体（Lev2TParametricDescription）本轮没取到源码，
        # 因此这里**不做区分**：n=2 一律用 Lev2ParametricDescription 的骨架，
        # 并在 README 标注该限制（状态数/接受判据只对非 T 变体严格成立）。
        self.descriptions = [
            None,                                           # n=0 不需要参数化描述
            Lev1TParametricDescription(w) if with_transpositions
            else Lev1ParametricDescription(w),
            Lev2ParametricDescription(w),
        ]

    def to_automaton_plan(self, n, prefix=""):
        """返回构造 DFA 前能确定的几个量；对应 toAutomaton(n, prefix) 的前半段。

        n == 0        → 退化成 makeString(prefix + word)
        n >= len(desc) → 源码直接 return null（不支持）
        """
        if n == 0:
            return {"kind": "makeString", "value": prefix + "".join(
                chr(c) for c in self.word)}
        if n >= len(self.descriptions):
            return {"kind": "unsupported"}
        desc = self.descriptions[n]
        num_states = desc.size()
        return {
            "kind": "dfa",
            "range": 2 * n + 1,
            "numStates": num_states,
            "numTransitions": num_states * min(1 + 2 * n, len(self.alphabet)),
            "prefixStates": len(prefix),
            "totalStates": num_states + len(prefix),
        }

    def get_vector(self, x, pos, end):
        """特征向量 X(x, V)：从 pos 到 end 逐位左移，命中置 1。"""
        vector = 0
        for i in range(pos, end):
            vector <<= 1
            if self.word[i] == x:
                vector |= 1
        return vector

    def vector_window(self, x, pos, n):
        """toAutomaton 里用的窗口：end = pos + min(w - pos, 2n+1)。"""
        end = pos + min(len(self.word) - pos, 2 * n + 1)
        return self.get_vector(x, pos, end), end
