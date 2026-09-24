"""FST 的 Util.TopNSearcher：如何在不遍历全部候选的前提下取 top-N。

忠实转写自 apache/lucene@main：
  lucene/core/src/java/org/apache/lucene/util/fst/Util.java（TopNSearcher 全篇）

这是 699（NRTSuggester）里那句 `// search admissibility is not guaranteed` 的**出处**：
TopNSearcher 用一个深度为 maxQueueDepth 的有界队列做 A*-like 搜索，队列满了就把最差的挤掉，
因此结果可能不是全局 top-N。结尾用 `rejectCount + topN <= maxQueueDepth` 判断"是否完整"。

**建模口径**：FST 的**字节编码**与**后缀共享**不还原，这里用显式的节点/弧结构代替，
但要求满足 FST 的关键不变式 —— **每个节点至少有一条 NO_OUTPUT 弧**（源码 `assert foundZero`）。
"""

NO_OUTPUT = 0        # 本 demo 的 output 就是整数，加法即 add
END_LABEL = -1       # FST.END_LABEL


class Outputs(object):
    """Outputs 代数的最小实现：add 是加法，noOutput 是 0。"""

    @staticmethod
    def add(a, b):
        return a + b

    @staticmethod
    def no_output():
        return NO_OUTPUT

    @staticmethod
    def compare(a, b):
        return (a > b) - (a < b)


class Node(object):
    def __init__(self):
        self.arcs = []      # [(label, output, target)]，按 label 升序

    def add_arc(self, label, output, target):
        self.arcs.append((label, output, target))
        self.arcs.sort(key=lambda a: a[0])

    @property
    def is_leaf(self):
        return not self.arcs

    def min_output(self):
        return min(a[1] for a in self.arcs) if self.arcs else NO_OUTPUT

    def has_zero_arc(self):
        return any(Outputs.compare(a[1], NO_OUTPUT) == 0 for a in self.arcs)


class FST(object):
    def __init__(self, root=None):
        self.root = root or Node()
        self.outputs = Outputs()

    # ---- 不变式：每个节点至少一条 NO_OUTPUT 弧（源码 assert foundZero） ----
    def check_zero_arc_invariant(self):
        """不变式只约束**非根节点**。

        根的全部出弧由 addStartPaths 无条件枚举（是否需要 END_LABEL 那条由
        allowEmptyString 决定），"零输出补全"是从根的**目标节点**才开始的，
        所以根自己有没有零弧无所谓；真正会被 assert foundZero 拦住的是非根节点。
        """
        seen = set()
        stack = [self.root]
        while stack:
            n = stack.pop()
            if id(n) in seen:
                continue
            seen.add(id(n))
            if n is not self.root and n.arcs and not n.has_zero_arc():
                return False
            for _, _, t in n.arcs:
                stack.append(t)
        return True

    def nodes(self):
        seen = set()
        out = []
        stack = [self.root]
        while stack:
            n = stack.pop()
            if id(n) in seen:
                continue
            seen.add(id(n))
            out.append(n)
            for _, _, t in n.arcs:
                stack.append(t)
        return out

    # ---- 把输出往根推：保证每个节点至少一条零输出弧 ----
    def push_outputs_to_root(self):
        """自底向上：每个节点取 min(output)，从所有出弧里减掉、加到所有入弧上。

        这是 FST 能支持"零输出补全"的前提 —— 推完之后**每个节点必有一条零弧**，
        于是从任意节点往下先走零弧得到的就是该子树的最小输出路径。
        """
        order = self.nodes()
        # 入弧表
        incoming = {}
        for n in order:
            for _, _, t in n.arcs:
                incoming.setdefault(id(t), []).append(n)
        # 后序：先处理离根远的（简单按深度降序即可）
        depth = {}
        stack = [(self.root, 0)]
        while stack:
            n, d = stack.pop()
            if id(n) in depth:
                continue
            depth[id(n)] = d
            for _, _, t in n.arcs:
                stack.append((t, d + 1))
        for n in sorted(order, key=lambda x: -depth[id(x)]):
            if not n.arcs or n is self.root:
                # 根**不参与前推**：它没有入弧可吸收 min，减了就会让所有路径整体偏移。
                # 而且根也不需要零弧 —— addStartPaths 会把根的全部出弧都入队，
                # "零输出补全" 是从根的**目标节点**才开始走零弧的。
                continue
            m = n.min_output()
            if Outputs.compare(m, NO_OUTPUT) == 0:
                continue
            n.arcs = [(lb, out - m if isinstance(out, int) else out, t)
                      for (lb, out, t) in n.arcs]
            for src in incoming.get(id(n), []):
                src.arcs = [(lb, out + m if (t is n and isinstance(out, int)) else out, t)
                            for (lb, out, t) in src.arcs]
        return self


class Arc(object):
    """弧游标：(node, idx)。"""

    __slots__ = ("node", "idx")

    def __init__(self, node, idx=0):
        self.node = node
        self.idx = idx

    @property
    def label(self):
        return self.node.arcs[self.idx][0]

    @property
    def output(self):
        return self.node.arcs[self.idx][1]

    @property
    def target(self):
        return self.node.arcs[self.idx][2]

    @property
    def is_last(self):
        return self.idx == len(self.node.arcs) - 1

    def copy_from(self, other):
        self.node = other.node
        self.idx = other.idx


class FSTPath(object):
    __slots__ = ("output", "arc", "input")

    def __init__(self, output, arc, inp):
        self.output = output
        self.arc = arc
        self.input = list(inp)

    def new_path(self, output, inp):
        return FSTPath(output, Arc(self.arc.node, self.arc.idx), inp)


class Result(object):
    __slots__ = ("input", "output")

    def __init__(self, inp, output):
        self.input = list(inp)
        self.output = output

    def __repr__(self):
        return "Result(%r, %r)" % (self.input, self.output)


class TopResults(object):
    def __init__(self, is_complete, results):
        self.isComplete = is_complete
        self.results = results

    def __repr__(self):
        return "TopResults(complete=%s, %r)" % (self.isComplete, self.results)


def tie_break_by_input(comparator):
    """TieBreakByInputComparator：先比 output，同分比 input 字典序。"""
    def cmp(p1, p2):
        c = comparator(p1.output, p2.output)
        if c != 0:
            return c
        a = tuple(p1.input)
        b = tuple(p2.input)
        return (a > b) - (a < b)
    return cmp


class TopNSearcher(object):
    """Util.TopNSearcher 的 Python 转写。

    关键点（源码逐条对应）：
      * queue 是有界的（maxQueueDepth），用 pathComparator 排序，pollFirst 取最优；
      * addIfCompetitive 在**队列满**时才比较，不与最差的比赢就丢弃；
      * 同分时按 input 字典序再比一次；
      * `results.size() == topN-1 && maxQueueDepth == topN` 时把 queue 置 null，
        最后一条直接走"零输出补全"，不再入队；
      * 零输出补全：从当前节点往下只走 **output == NO_OUTPUT** 的弧（用 comparator 比，
        不是 ==），因此每个节点必须至少有一条零弧（`assert foundZero`）。
    """

    def __init__(self, fst, top_n, max_queue_depth, comparator=None):
        self.fst = fst
        self.top_n = top_n
        self.max_queue_depth = max_queue_depth
        self.comparator = comparator or Outputs.compare
        self.path_comparator = tie_break_by_input(self.comparator)
        self.queue = []
        self.scratch_arc = None
        self.reject_count = 0
        self.partial_rejects = 0

    # ---- 两个可覆写的钩子 ----
    def accept_partial_path(self, path):
        return True

    def accept_result(self, path):
        return True

    # ---- addIfCompetitive ----
    def add_if_competitive(self, path):
        assert self.queue is not None
        output = self.fst.outputs.add(path.output, path.arc.output)
        if len(self.queue) == self.max_queue_depth:
            bottom = self.queue[-1]
            comp = self.path_comparator(path, bottom)
            if comp > 0:
                return False                     # Doesn't compete
            if comp == 0:
                # Tie break by alpha sort on the input
                a = tuple(path.input + [path.arc.label])
                b = tuple(bottom.input)
                if b < a:
                    return False
        new_path = path.new_path(output, path.input + [path.arc.label])
        if not self.accept_partial_path(new_path):
            self.partial_rejects += 1
            return False
        self._insert(new_path)
        if len(self.queue) == self.max_queue_depth + 1:
            self.queue.pop()
        return True

    def _insert(self, p):
        import bisect
        # 用 pathComparator 做有序插入；这里 comparator 只依赖 (output, input)
        lo, hi = 0, len(self.queue)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.path_comparator(self.queue[mid], p) < 0:
                lo = mid + 1
            else:
                hi = mid
        self.queue.insert(lo, p)

    # ---- addStartPaths ----
    def add_start_paths(self, node, start_output, input_, allow_empty_string=False):
        # De-dup NO_OUTPUT since it must be a singleton
        if Outputs.compare(start_output, NO_OUTPUT) == 0:
            start_output = NO_OUTPUT
        path = FSTPath(start_output, Arc(node, 0), input_)
        while True:
            if allow_empty_string or path.arc.label != END_LABEL:
                self.add_if_competitive(path)
            if path.arc.is_last:
                break
            path.arc.idx += 1

    # ---- search ----
    def search(self):
        results = []
        NO = self.fst.outputs.no_output()
        while len(results) < self.top_n:
            if self.queue is None:
                break
            if not self.queue:
                break
            path = self.queue.pop(0)
            if not self.accept_partial_path(path):
                self.partial_rejects += 1
                continue
            if path.arc.label == END_LABEL:
                # Empty string!
                path.input = path.input[:-1]
                results.append(Result(path.input, path.output))
                continue
            if len(results) == self.top_n - 1 and self.max_queue_depth == self.top_n:
                # Last path -- don't bother w/ queue anymore
                self.queue = None

            # 零输出补全
            while True:
                path.arc.node = path.arc.target
                path.arc.idx = 0
                found_zero = False
                arc_copy_pending = False
                while True:
                    if self.comparator(NO, path.arc.output) == 0:
                        if self.queue is None:
                            found_zero = True
                            break
                        if not found_zero:
                            arc_copy_pending = True
                            found_zero = True
                        else:
                            self.add_if_competitive(path)
                    elif self.queue is not None:
                        self.add_if_competitive(path)
                    if path.arc.is_last:
                        break
                    if arc_copy_pending:
                        self.scratch_arc = Arc(path.arc.node, path.arc.idx)
                        arc_copy_pending = False
                    path.arc.idx += 1
                assert found_zero, "每个节点必须至少有一条 NO_OUTPUT 弧（源码 assert foundZero）"
                if self.queue is not None and not arc_copy_pending and self.scratch_arc is not None:
                    path.arc.copy_from(self.scratch_arc)
                if path.arc.label == END_LABEL:
                    path.output = self.fst.outputs.add(path.output, path.arc.output)
                    if self.accept_result(path):
                        results.append(Result(path.input, path.output))
                    else:
                        self.reject_count += 1
                    break
                path.input = path.input + [path.arc.label]
                path.output = self.fst.outputs.add(path.output, path.arc.output)
                if not self.accept_partial_path(path):
                    self.partial_rejects += 1
                    break
        return TopResults(self.reject_count + self.top_n <= self.max_queue_depth, results)
