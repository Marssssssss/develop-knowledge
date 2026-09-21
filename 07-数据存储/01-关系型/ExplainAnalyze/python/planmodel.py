"""EXPLAIN ANALYZE 的读数模型。

口径全部来自 PostgreSQL 18 官方文档《Using EXPLAIN》(14.1):

- `cost=A..B rows=N width=W` 是**估算**: startup cost / total cost / 输出行数 / 行宽。
- `actual time=a..b rows=R loops=L` 是**实测**: a/b 是首次/最后一次产出行的时刻(毫秒),
  R 与 time 都是**每次执行的平均值**, 不是总和 —— 文档原话:
  "the loops value reports the total number of executions of the node, and the actual
  time and rows values shown are averages per-execution ... Multiply by the loops value
  to get the total time actually spent in the node."
- 上层节点的 cost **包含**全部子节点; 而 `rows` 是**本节点输出**的行数,不是扫描的行数。
- `Rows Removed by Filter: N` **只在至少拒绝了 1 行时出现**。
- `BitmapAnd` / `BitmapOr` 的 actual rows **恒为 0**(文档 Caveats)。
- merge join 的外层重复键会让内层被回溯重扫, 内层 actual rows 会被**虚增**。
- `LIMIT` 短路时子节点按"跑到底"显示估算, 实测只跑了部分 —— 这不是估算错误。

本模块只做"读数"这件事: 把 instrumentation 的计数换算成文档口径的输出,
并给出估算误差诊断。
"""

EPS = 1e-9


class Node(object):
    """一个计划节点: 既可以放估算, 也可以挂载实测计数。"""

    def __init__(self, nodetype, est_startup=0.0, est_total=0.0, est_rows=0.0,
                 est_width=0, children=None, extra=None):
        self.nodetype = nodetype
        self.est_startup = est_startup
        self.est_total = est_total
        self.est_rows = est_rows
        self.est_width = est_width
        self.children = children or []
        self.extra = extra or {}
        # instrumentation: 由执行器累计
        self.first_tuple = None       # 首次产出行时刻(ms)
        self.last_tuple = None        # 最后一次产出行时刻(ms)
        self.ntuples = 0.0            # 累计产出行数(所有 loop)
        self.nloops = 0               # 本节点被执行的次数
        self.rows_removed = 0         # 被 filter 拒绝的行数
        self.scanned = 0              # 扫描(而非输出)的行数

    # ---- 执行侧: 累计 ------------------------------------------------------
    def run(self, tuples_out, scanned=None, first_ms=0.0, last_ms=0.0, removed=0):
        """记录一次执行(一次 loop)。"""
        self.nloops += 1
        self.ntuples += tuples_out
        self.rows_removed += removed
        self.scanned += tuples_out if scanned is None else scanned
        # 执行器按 loop 累加耗时, 展示时再除以 loops —— 所以记录的是**和**。
        self.first_tuple = (self.first_tuple or 0.0) + first_ms
        self.last_tuple = (self.last_tuple or 0.0) + last_ms

    # ---- 读数侧: 换算成文档口径 --------------------------------------------
    def actual_rows(self):
        """文档口径的 rows: 每次执行的平均值。BitmapAnd/Or 恒为 0。"""
        if self.nodetype in ("BitmapAnd", "BitmapOr"):
            return 0.0
        if self.nloops == 0:
            return 0.0
        return self.ntuples / self.nloops

    def total_rows(self):
        """`actual rows × loops` 才是真实总行数。"""
        return self.actual_rows() * self.nloops

    def actual_time(self):
        """(first, last) 每次执行的平均值(毫秒)。"""
        if self.nloops == 0:
            return (0.0, 0.0)
        return (self.first_tuple / self.nloops, self.last_tuple / self.nloops)

    def total_time(self):
        """`actual time` 是均值, 总耗时要乘 loops。"""
        first, last = self.actual_time()
        return (first * self.nloops, last * self.nloops)

    def est_error(self):
        """估算行数 / 实测总行数; 返回 None 表示不可比(如 BitmapAnd)。"""
        real = self.total_rows()
        if real <= EPS or self.est_rows <= EPS:
            return None
        return self.est_rows / real

    # ---- 渲染 ---------------------------------------------------------------
    def render(self, indent=0, analyze=True):
        pad = " " * (indent * 2) + ("-> " if indent else "")
        est = "cost=%.2f..%.2f rows=%.0f width=%d" % (
            self.est_startup, self.est_total, self.est_rows, self.est_width)
        line = pad + self.nodetype + " (" + est + ")"
        if analyze:
            f, l = self.actual_time()
            line += " (actual time=%.3f..%.3f rows=%.2f loops=%d)" % (
                f, l, self.actual_rows(), self.nloops)
        for k, v in self.extra.items():
            line += "\n" + " " * (indent * 2 + 6) + "%s: %s" % (k, v)
        if self.nloops == 0 and analyze:
            line += " (never executed)"
        if self.rows_removed > 0:
            line += "\n" + " " * (indent * 2 + 6) + "Rows Removed by Filter: %d" % self.rows_removed
        out = [line]
        for c in self.children:
            out.append(c.render(indent + 1, analyze))
        return "\n".join(out)


def seq_scan_cost(relpages, reltuples, seq_page_cost=1.0, cpu_tuple_cost=0.01):
    """文档 14.1.1 的算式: pages * seq_page_cost + rows * cpu_tuple_cost。

    tenk1 的例子: (345 * 1.0) + (10000 * 0.01) = 445。
    """
    return relpages * seq_page_cost + reltuples * cpu_tuple_cost
