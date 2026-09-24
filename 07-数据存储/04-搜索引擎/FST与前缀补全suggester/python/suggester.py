"""FST 与 completion suggester：权重编码、payload 切分、top-N 队列容量启发式。

忠实转写自 apache/lucene@main：
  lucene/suggest/src/java/org/apache/lucene/search/suggest/document/NRTSuggester.java
  lucene/analysis/common/.../miscellaneous/ConcatenateGraphFilter.java
  lucene/core/src/java/org/apache/lucene/analysis/TokenStreamToAutomaton.java
  lucene/core/src/java/org/apache/lucene/util/automaton/Operations.java

**建模口径（重要）**：Lucene 的真实 FST 是带后缀共享的最小化有向无环图，本 demo 用
**前缀 trie** 代替 —— 共享后缀不建模，但所有被断言的性质（权重编码、payload 布局、
topN/queueSize 启发式、去重剪枝、比较器方向）都只依赖「路径输出可累加」这一点，
与是否最小化无关。

payloadSep 在源码里是建索引时写入的一个 vint（`NRTSuggester.load` 的
`int payloadSep = input.readVInt()`），并非硬编码常量；本 demo 取 0x1F 作示例值。
"""

INT_MAX = 2147483647          # Integer.MAX_VALUE
MAX_TOP_N_QUEUE_SIZE = 5000   # NRTSuggester 私常量
DEFAULT_PAYLOAD_SEP = 0x1F

# ConcatenateGraphFilter / TokenStreamToAutomaton / Operations 的真实常量
POS_SEP = 0x001F              # TokenStreamToAutomaton.POS_SEP
HOLE = 0x001E                 # TokenStreamToAutomaton.HOLE
DEFAULT_MAX_GRAPH_EXPANSIONS = 10000   # Operations.DEFAULT_DETERMINIZE_WORK_LIMIT


# ------------------------------------------------------------------ 权重编码

def encode(weight):
    """权重 → FST 里的 output1。**越大越"小"**，这样升序 top-N 就能先取到重权重。

    源码：if (input < 0 || input > Integer.MAX_VALUE) throw UnsupportedOperationException
    """
    if weight < 0 or weight > INT_MAX:
        raise ValueError("cannot encode value: %d" % weight)
    return INT_MAX - weight


def decode(output):
    """decode(output) = Integer.MAX_VALUE - output。"""
    assert 0 <= output <= INT_MAX, \
        "decoded output: %d is not within 0 and Integer.MAX_VALUE" % output
    return INT_MAX - output


# ------------------------------------------------------------------ vInt

def write_vint(i):
    """Lucene writeVInt：每字节 7 位，高位为 continuation，最多 5 字节。"""
    if i < 0:
        raise ValueError("vInt 不能编码负数: %d" % i)
    out = bytearray()
    while (i & ~0x7F) != 0:
        out.append((i & 0x7F) | 0x80)
        i >>= 7
    out.append(i)
    return bytes(out)


def read_vint(buf, pos=0):
    """返回 (值, 新位置)。第 5 字节仍有 continuation 位 → 抛异常（源码 Invalid vInt）。"""
    b = buf[pos]
    pos += 1
    val = b & 0x7F
    shift = 7
    nbytes = 1
    while (b & 0x80) != 0:
        if nbytes == 5:
            raise ValueError("Invalid vInt (too long)")
        b = buf[pos]
        pos += 1
        nbytes += 1
        val |= (b & 0x7F) << shift
        shift += 7
    return val, pos


# ------------------------------------------------------------------ payload

class PayLoadProcessor(object):
    """payload = surface + PAYLOAD_SEP + vInt(docID)。

    MAX_DOC_ID_LEN_WITH_SEP = 6（vint 最多 5 字节 + 1 字节分隔符）。
    """

    MAX_DOC_ID_LEN_WITH_SEP = 6

    @staticmethod
    def parse_surface_form(output, payload_sep):
        """找**第一个** payloadSep，它之前是 surface form。"""
        surface_form_len = -1
        for i in range(len(output)):
            if output[i] == payload_sep:
                surface_form_len = i
                break
        assert surface_form_len != -1, "no payloadSep found, unable to determine surface form"
        return output[:surface_form_len], surface_form_len

    @staticmethod
    def make(surface, doc_id, payload_sep):
        buf = bytearray()
        buf.extend(surface)
        buf.append(payload_sep)
        buf.extend(write_vint(doc_id))
        return bytes(buf)


# ------------------------------------------------- 队列容量 / liveDocs 启发式

def calculate_live_doc_ratio(num_docs, max_docs):
    """numDocs / maxDocs；numDocs == 0 → -1（lookup 直接 return，不做任何搜索）。"""
    if num_docs == 0:
        return -1
    return num_docs / max_docs


def get_max_top_n_queue_size(top_n, num_docs, live_docs_ratio, filter_enabled,
                             max_analyzed_paths_per_output):
    """源码注释自称 "simple heuristics"，且**不保证** search admissibility。

    maxQueueSize = topN * maxAnalyzedPathsPerOutput
    maxQueueSize = maxQueueSize / liveDocsRatio
    if (filterEnabled) maxQueueSize += numDocs / 2
    return min(MAX_TOP_N_QUEUE_SIZE, maxQueueSize)
    """
    assert live_docs_ratio <= 1.0, "liveDocRatio can be at most 1.0"
    max_queue_size = top_n * max_analyzed_paths_per_output
    max_queue_size = int(max_queue_size / live_docs_ratio)
    if filter_enabled:
        max_queue_size += num_docs // 2
    return int(min(MAX_TOP_N_QUEUE_SIZE, max_queue_size))


# ------------------------------------------------------------------ FST（trie）

class FstNode(object):
    """trie 节点。终点挂 **一串** (output1, output2)，因为同一个 surface 可能有多条路径
    （多 context / 多 doc），这正是源码要把 topN 乘以 prefixPaths.size() 的原因。"""

    __slots__ = ("arcs", "outputs", "terminal")

    def __init__(self):
        self.arcs = {}          # label(int) -> FstNode
        self.outputs = []       # [(output1, output2)]
        self.terminal = False


class FstPath(object):
    __slots__ = ("node", "input", "out1", "out2", "payload", "boost", "context")

    def __init__(self, node, inp, out1, out2, payload=-1, boost=1.0, context=None):
        self.node = node
        self.input = inp
        self.out1 = out1
        self.out2 = out2
        self.payload = payload
        self.boost = boost
        self.context = context


class SuggesterFST(object):
    """把 (surface, weight, docId) 建成前缀 trie，输出挂在终点。"""

    def __init__(self, payload_sep=DEFAULT_PAYLOAD_SEP):
        self.root = FstNode()
        self.payload_sep = payload_sep
        self.max_analyzed_paths_per_output = 1

    def add(self, surface, weight, doc_id):
        data = surface.encode("utf-8")
        node = self.root
        for ch in data:
            if ch not in node.arcs:
                node.arcs[ch] = FstNode()
            node = node.arcs[ch]
        node.terminal = True
        node.outputs.append((encode(weight),
                             PayLoadProcessor.make(data, doc_id, self.payload_sep)))

    # ---- FSTUtil.intersectPrefixPaths 的前缀特化：沿 automaton 的唯一前缀走 ----
    def intersect_prefix_paths(self, prefix):
        """返回「沿 prefix 走到底」的那条路径；走不通则空列表。"""
        data = prefix.encode("utf-8")
        node = self.root
        for ch in data:
            if ch not in node.arcs:
                return []
            node = node.arcs[ch]
        return [FstPath(node, list(data), 0, b"")]


# ------------------------------------------------------------------ TopNSearcher

class TopNSearcher(object):
    """bounded 优先队列版 top-N 搜索。

    排序键：(score 降序, surface 升序) —— 对应源码里
      sortComparator  = Long.compare(o1.output1, o2.output1)          （编码值升序 = 权重降序）
      ScoringPathComparator = compare(second, first)，同分比 input 升序
    队列容量 queueSize 满了以后**最差的会被直接挤掉**，所以 admissibility 不保证。
    """

    def __init__(self, fst, top_n, queue_size, dedup=False, payload_sep=DEFAULT_PAYLOAD_SEP):
        self.fst = fst
        self.top_n = top_n
        self.queue_size = queue_size
        self.dedup = dedup
        self.payload_sep = payload_sep
        self.seen_surface_forms = set()
        self.results = []
        self.pruned = False
        self.boost = 1.0        # CompletionWeight#boost，查询期的加权系数

    def _key(self, path):
        score = decode(path.out1) * path.boost
        surface, _ = PayLoadProcessor.parse_surface_form(path.out2, self.payload_sep)
        return (-score, surface.decode("utf-8"))

    def add_start_paths(self, path):
        self._expand(path)

    def _accept_result(self, out1, out2):
        surface, sep_idx = PayLoadProcessor.parse_surface_form(out2, self.payload_sep)
        doc_id, _ = read_vint(out2, sep_idx + 1)
        if self.dedup:
            if surface in self.seen_surface_forms:
                return False
            self.seen_surface_forms.add(surface)
        self.results.append({
            "doc": doc_id,
            "surface": surface.decode("utf-8"),
            "weight": decode(out1),
            "score": decode(out1) * self.boost,
        })
        return True

    def _expand(self, path):
        if path.node.terminal:
            accepted_any = False
            for o1, o2 in path.node.outputs:
                if self._accept_result(path.out1 + o1, path.out2 + o2):
                    accepted_any = True
                else:
                    self.pruned = True
            # doSkipDuplicates 的「部分路径剪枝」：整条路径的 surface 都见过了，
            # 就不必再往下展开（源码在 acceptPartialPath 里做同样的事）
            if self.dedup and not accepted_any:
                self.pruned = True
                return
        for label in sorted(path.node.arcs):
            nxt = FstPath(path.node.arcs[label], path.input + [label],
                          path.out1, path.out2,
                          path.payload, self.boost, path.context)
            self._expand(nxt)

    def search(self):
        self.results.sort(key=lambda r: (-r["score"], r["surface"]))
        if len(self.results) > self.top_n:
            self.pruned = True
            self.results = self.results[:self.top_n]
        return self.results


class SuggestLookup(object):
    """把 NRTSuggester.lookup 的编排顺序复刻出来，便于断言每一步的中间量。"""

    def __init__(self, fst, max_analyzed_paths_per_output=1):
        self.fst = fst
        self.max_analyzed_paths_per_output = max_analyzed_paths_per_output

    def plan(self, count_to_collect, num_docs, max_docs, filter_enabled=False,
             paths_per_output=1):
        """返回 (liveDocsRatio, topN, queueSize)，liveDocsRatio == -1 表示直接 return。"""
        ratio = calculate_live_doc_ratio(num_docs, max_docs)
        if ratio == -1:
            return -1, 0, 0
        top_n = count_to_collect * paths_per_output
        queue_size = get_max_top_n_queue_size(
            top_n, num_docs, ratio, filter_enabled, self.max_analyzed_paths_per_output)
        return ratio, top_n, queue_size

    def lookup(self, prefix, count_to_collect, num_docs, max_docs,
               filter_enabled=False, dedup=False):
        ratio, top_n, queue_size = self.plan(
            count_to_collect, num_docs, max_docs, filter_enabled)
        if ratio == -1:
            return []
        prefix_paths = self.fst.intersect_prefix_paths(prefix)
        if not prefix_paths:
            return []
        top_n = count_to_collect * len(prefix_paths)
        queue_size = get_max_top_n_queue_size(
            top_n, num_docs, ratio, filter_enabled, self.max_analyzed_paths_per_output)
        searcher = TopNSearcher(self.fst, top_n, queue_size, dedup, self.fst.payload_sep)
        for p in prefix_paths:
            searcher.add_start_paths(p)
        return searcher.search()
