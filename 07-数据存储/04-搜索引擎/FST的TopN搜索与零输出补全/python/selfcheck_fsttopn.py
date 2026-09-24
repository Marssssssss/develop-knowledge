"""FST 的 TopN 搜索与零输出补全 —— 自检。

期望值来自 Util.java 的 TopNSearcher 源码实读 + 手算，已实跑校准。

运行：python selfcheck_fsttopn.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fstsearch import (FST, Node, Arc, FSTPath, TopNSearcher, Outputs,
                       NO_OUTPUT, END_LABEL)

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


def leaf():
    return Node()


def build(entries, push=True):
    """entries = [(input 码点列表, output)]；终端用 END_LABEL 弧收尾。"""
    fst = FST()
    for inp, out in entries:
        n = fst.root
        for ch in inp:
            nxt = None
            for lb, o, t in n.arcs:
                if lb == ch:
                    nxt = t
                    break
            if nxt is None:
                nxt = Node()
                n.add_arc(ch, NO_OUTPUT, nxt)
            n = nxt
        n.add_arc(END_LABEL, out, leaf())
    if push:
        fst.push_outputs_to_root()
    return fst


def paths(fst):
    """暴力枚举所有 (input, output)，用来和搜索结果对拍。"""
    out = []

    def walk(n, acc_out, acc_in):
        for lb, o, t in n.arcs:
            if lb == END_LABEL:
                out.append((tuple(acc_in), acc_out + o))
            else:
                walk(t, acc_out + o, acc_in + [lb])
    walk(fst.root, 0, [])
    return out


def run(fst, top_n, max_queue_depth, allow_empty=False, **kw):
    s = TopNSearcher(fst, top_n, max_queue_depth, **kw)
    s.add_start_paths(fst.root, NO_OUTPUT, [], allow_empty_string=allow_empty)
    return s.search(), s


# ============================================= 1. 输出前推与零弧不变式
fst = build([([1, 2], 5), ([1, 3], 7), ([9], 2)], push=False)
eq("未前推时不变式不成立", fst.check_zero_arc_invariant(), False)
fst.push_outputs_to_root()
eq("前推后不变式成立", fst.check_zero_arc_invariant(), True)
# 前推不改变任何路径的总输出
raw = build([([1, 2], 5), ([1, 3], 7), ([9], 2)], push=False)
eq("前推保持路径输出不变",
   sorted(paths(fst)), sorted(paths(raw)))

# 每个**非根**节点都有一条零输出弧（根不要求，见 check_zero_arc_invariant 的说明）
for n in fst.nodes():
    if n.arcs and n is not fst.root:
        check("非根节点有零弧", n.has_zero_arc())

# ============================================= 2. 基本 top-N
res, s = run(fst, 10, 50)
eq("三条全收", len(res.results), 3)
eq("按 output 升序（升序即 top）",
   [r.output for r in res.results], sorted(o for _, o in paths(fst)))
eq("队列够深 → isComplete", res.isComplete, True)

res1, _ = run(fst, 1, 50)
eq("topN=1 只收一条", len(res1.results), 1)
eq("收的是最小 output", res1.results[0].output, min(o for _, o in paths(fst)))

res2, _ = run(fst, 2, 50)
eq("topN=2 收两条", len(res2.results), 2)

# ============================================= 3. addIfCompetitive 的容量语义
# maxQueueDepth=1：队列只有一个位置，被挤掉的候选再也回不来 —— 这正是
# NRTSuggester 里那句 "search admissibility is not guaranteed" 的成因
res3, s3 = run(fst, 3, 1)
eq("队列深度 1 会丢候选（over-pruning）", len(res3.results) < 3, True)
eq("队列深度 1 至少还能出 1 条", len(res3.results) >= 1, True)
# isComplete = rejectCount + topN <= maxQueueDepth → 0 + 1 <= 1
# isComplete = (rejectCount + topN <= maxQueueDepth)；3 <= 1 显然不成立
eq("isComplete 用的是 (rejectCount + topN <= depth)", res3.isComplete, False)
res_deep, _ = run(fst, 3, 50)
eq("队列够深时收满", len(res_deep.results), 3)

res4, s4 = run(fst, 1, 1)
eq("rejectCount+topN<=depth 时 isComplete 为真", res4.isComplete, True)

# ============================================= 4. 同分时的 input 字典序
tie = build([([2], 0), ([1], 0), ([3], 0)])
rest, _ = run(tie, 3, 50)
eq("同分按 input 升序", [tuple(r.input) for r in rest.results], [(1,), (2,), (3,)])

# ============================================= 5. END_LABEL：空串结果
# 空串：根上的 END_LABEL 弧**只有 allowEmptyString=true 才会入队**
empty = build([([], 4), ([7], 9)])
eq("空串 FST 的零弧不变式", empty.check_zero_arc_invariant(), True)
rn, _ = run(empty, 5, 50, allow_empty=False)
eq("allowEmptyString=false 时排除空串", len(rn.results), 1)
eq("剩下的是 [7]", tuple(rn.results[0].input), (7,))
rem, _ = run(empty, 5, 50, allow_empty=True)
got = sorted([(tuple(r.input), r.output) for r in rem.results])
eq("allowEmptyString=true 时收满两条", len(rem.results), 2)
eq("含空串结果", (tuple(),) in [g[:1] for g in got], True)
eq("空串与 [7] 的输出顺序", [g[1] for g in got], sorted(o for _, o in paths(empty)))

# ============================================= 6. acceptResult 拒绝 → rejectCount
class RejectBig(TopNSearcher):
    def accept_result(self, path):
        return path.output < 6


r5, s5 = None, None
sr = RejectBig(fst, 3, 50)
sr.add_start_paths(fst.root, NO_OUTPUT, [])
r5 = sr.search()
eq("被拒的不进结果", len(r5.results), sum(1 for _, o in paths(fst) if o < 6))
eq("rejectCount 累加", sr.reject_count, sum(1 for _, o in paths(fst) if o >= 6))
eq("isComplete 把 rejectCount 算进去",
   r5.isComplete, sr.reject_count + 3 <= 50)

# ============================================= 7. acceptPartialPath 拒绝
class RejectOne(TopNSearcher):
    """拒绝所有经过 label 9 的部分路径。"""

    def accept_partial_path(self, path):
        return 9 not in path.input


sp = RejectOne(fst, 3, 50)
sp.add_start_paths(fst.root, NO_OUTPUT, [])
rp = sp.search()
check("被剪枝的路径不出现在结果里",
      all(9 not in r.input for r in rp.results))
eq("剪枝后收不满 topN", len(rp.results) < 3, True)

# ============================================= 8. queue = null 的触发条件
# results.size() == topN-1 && maxQueueDepth == topN 时才把队列置空
s6 = TopNSearcher(fst, 2, 2)          # topN == maxQueueDepth
s6.add_start_paths(fst.root, NO_OUTPUT, [])
r6 = s6.search()
eq("topN==depth 时能收满", len(r6.results), 2)
check("最后一条把队列置空了", s6.queue is None)

s7 = TopNSearcher(fst, 2, 5)          # topN != maxQueueDepth → 队列不置空
s7.add_start_paths(fst.root, NO_OUTPUT, [])
r7 = s7.search()
eq("depth != topN 时也能收满", len(r7.results), 2)
check("队列未被置空", s7.queue is not None)

# ============================================= 9. 零弧不变式被破坏 → assert
# 零弧不变式只约束**非根节点**（根的全部出弧由 addStartPaths 入队，不走零输出补全）
bad = FST()
mid = Node()
bad.root.add_arc(1, NO_OUTPUT, mid)     # 根弧可以非零
mid.add_arc(END_LABEL, 5, leaf())       # mid 只有一条非零弧 → 破坏不变式
eq("坏 FST 的根有零弧", bad.root.has_zero_arc(), True)
eq("坏 FST 的非根节点无零弧", mid.has_zero_arc(), False)
sb = TopNSearcher(bad, 1, 5)
sb.add_start_paths(bad.root, NO_OUTPUT, [])
try:
    sb.search()
    check("无零弧应触发断言", False)
except AssertionError as e:
    check("无零弧触发 assert foundZero", "NO_OUTPUT 弧" in str(e))

# ============================================= 10. 大一点的例子：排序正确性
import random
rnd = random.Random(11)
entries = [([rnd.randint(1, 5), rnd.randint(1, 5)], rnd.randint(0, 50))
           for _ in range(12)]
big = build(entries)
allp = sorted(set(paths(big)))
rb, _ = run(big, len(allp), 200)
eq("全量搜索条数一致", len(rb.results), len(allp))
eq("全量搜索输出一致", sorted(r.output for r in rb.results),
   sorted(o for _, o in allp))
rt, _ = run(big, 3, 200)
eq("topN=3 取到最小的 3 个",
   [r.output for r in rt.results], sorted(o for _, o in allp)[:3])

print("PASS=%d FAIL=%d" % (PASS, len(FAIL)))
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
