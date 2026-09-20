"""LouvainModularity 自检：模块度、ΔQ 公式、聚合不变性、两阶段迭代。

官方/原始来源（实读）：
  * Blondel, Guillaume, Lambiotte, Lefebvre — *Fast unfolding of communities
    in large networks*, arXiv:0803.0476v2（12 页 PDF 全文抽取后逐段回读）：
    式(1) 模块度定义、式(2) ΔQ 增量公式、两阶段迭代、聚合时「社区内部边变成
    自环」、ring of 30 cliques 的两次 pass 结果、Q 取值在 −1 与 1 之间。
  * Neo4j GDS → Louvain 文档：maxLevels / maxIterations（每个 level 的最大
    迭代次数）/ tolerance（迭代间模块度变化小于该值即视为稳定并返回）/
    includeIntermediateCommunities / consecutiveIds；结果含最终 modularity
    与每个 level 的 modularity 列表。
"""

from louvain import (Graph, modularity, delta_q_add, one_level, aggregate,
                     louvain, ring_of_cliques, TOL)

OK = 0
FAIL = []


def ck(name, cond, detail=""):
    global OK
    if cond:
        OK += 1
    else:
        FAIL.append("%s  %s" % (name, detail))


def eq(name, got, want):
    ck(name, got == want, "got=%r want=%r" % (got, want))


def close(name, got, want, tol=1e-9):
    ck(name, abs(got - want) < tol, "got=%.12g want=%.12g" % (got, want))


# ------------------------------------------------- 基础量：m 与 k
g = Graph()                       # 两个三角形 + 一条连接边
for a, b in [(0, 1), (0, 2), (1, 2), (3, 4), (3, 5), (4, 5), (2, 3)]:
    g.add_edge(a, b)
eq("m = 边数（无权图）", g.m(), 7.0)
eq("k(0) = 2", g.k(0), 2.0)
eq("k(2) = 3（含跨三角形的边）", g.k(2), 3.0)
eq("Σk = 2m", sum(g.k(i) for i in g.nodes), 2 * g.m())

# ------------------------------------------------- 模块度
singleton = {n: n for n in g.nodes}
close("Q(singleton) 为负", modularity(g, singleton), -0.17346938775510204)
two = {0: 0, 1: 0, 2: 0, 3: 1, 4: 1, 5: 1}
close("Q(两个三角形)", modularity(g, two), 0.35714285714285715)
ck("两个三角形的划分优于单点划分", modularity(g, two) > modularity(g, singleton))
allone = {n: 0 for n in g.nodes}
close("全图一个社区时 Q = 0", modularity(g, allone), 0.0)
# 官方：Q 是 −1 与 1 之间的标量
for p in (singleton, two, allone):
    q = modularity(g, p)
    ck("Q 落在 [-1,1]", -1.0 <= q <= 1.0, "q=%r" % q)

# ------------------------------------------------- ΔQ 公式 vs 重算（式 2）
part = dict(two)
i = 2
before = modularity(g, part)
close("ΔQ_add({3,4,5}) 公式值", delta_q_add(g, part, i, [3, 4, 5]),
      -0.07142857142857142)
close("ΔQ_add({0,1}) 公式值", delta_q_add(g, part, i, [0, 1]),
      0.16326530612244897)
moved = dict(part)
moved[i] = 1
net = modularity(g, moved) - before
close("净增益 = ΔQ_add(C) − ΔQ_add(D\\{i})",
      delta_q_add(g, part, i, [3, 4, 5]) - delta_q_add(g, part, i, [0, 1]),
      net)
# 孤立节点移入：ΔQ_add 直接等于「加入后 Q − 加入前 Q − 单点 Q」
iso = {0: 0, 1: 0, 2: 2, 3: 1, 4: 1, 5: 1}
pre = modularity(g, iso)
post_p = dict(iso)
post_p[2] = 0
close("孤立节点移入：公式 = 重算差",
      delta_q_add(g, iso, 2, [0, 1]), modularity(g, post_p) - pre)
# 空目标社区的增益恒为 0（即「留在孤立状态」的基准）
close("ΔQ_add(空社区) = 0", delta_q_add(g, part, 2, []), 0.0)

# ------------------------------------------------- 自环约定（正向 + 反向）
sg = Graph()
sg.add_edge("a", "b", 3.0)
sg.add_edge("b", "c", 4.0)
sg.add_edge("a", "a", 5.0)            # 自环权重 5
eq("A(a,a) = 2·s（本模型约定）", sg.A("a", "a"), 10.0)
eq("m = 边权之和（自环按一次计）", sg.m(), 12.0)
close("自环只记一次时 m 变小（反例）", sg.total_weight_once(), 9.5)

# ------------------------------------------------- 聚合不变性
p_agg = {"a": 0, "b": 0, "c": 1}
q_before = modularity(sg, p_agg)
sup = aggregate(sg, p_agg)
close("聚合后 m 不变", sup.m(), sg.m())
q_after = modularity(sup, {c: c for c in sup.nodes})
close("聚合前后 Q 不变", q_after, q_before, 1e-9)
# 社区 0 = {a, b}：内部边 a-b(3) + a 的自环(5) = 8，自环约定下 A_ii = 2×8
eq("聚合产生自环（内部边 + 原有自环）", sup.A(0, 0), 16.0)
eq("聚合后跨社区边权", sup.A(0, 1), 4.0)
# 反向断言：若自环只记一次，不变性会被破坏
ck("自环只记一次会破坏聚合不变性（反例）",
   abs(sup.total_weight_once() - sg.total_weight_once()) > TOL
   or abs(sup.total_weight_once() - sg.m()) > TOL,
   "once=%r m=%r" % (sup.total_weight_once(), sg.m()))
ck("自环只记一次时 m 与总边权不等（反例）",
   abs(sg.total_weight_once() - sg.m()) > TOL)

# ------------------------------------------------- 第一阶段（局部极大）
g2 = Graph()
for a, b in [(0, 1), (0, 2), (1, 2), (3, 4), (3, 5), (4, 5), (2, 3)]:
    g2.add_edge(a, b)
p1, q1, iters = one_level(g2, {n: n for n in g2.nodes})
eq("第一阶段找到 2 个社区", len(set(p1.values())), 2)
close("第一阶段 Q", q1, 0.35714285714285715, 1e-9)
# 再跑一次不应再变（已达局部极大）
p1b, q1b, _ = one_level(g2, p1)
close("再跑一次 Q 不变（局部极大）", q1b, q1, 1e-12)
# 再跑一次真正的划分也不变（按社区成员集合比较，避免标签重命名）
def _blocks(p):
    d = {}
    for k, v in p.items():
        d.setdefault(v, []).append(k)
    return sorted(tuple(sorted(v)) for v in d.values())


eq("再跑一次划分不变（按成员集合比较）", _blocks(p1b), _blocks(p1))

# ------------------------------------------------- 完整 Louvain
fin, qf, levels, nlev = louvain(g2)
eq("Louvain 最终社区数", len(set(fin.values())), 2)
close("Louvain 最终 Q", qf, 0.35714285714285715, 1e-9)
ck("每个 level 的 Q 单调不减",
   all(levels[k + 1] >= levels[k] - 1e-12 for k in range(len(levels) - 1)),
   str(levels))
ck("最终 Q 不低于任一 level", qf >= max(levels) - 1e-12)

# tolerance 语义：把 tolerance 调到很大时，第一轮后就返回
p_t, q_t, it_t = one_level(g2, {n: n for n in g2.nodes}, max_iterations=20,
                           tolerance=10.0)
ck("tolerance 极大时立即视为稳定（迭代数 ≤2）", it_t <= 2, "iters=%d" % it_t)

# ------------------------------------------------- ring of cliques（论文示例）
rg = ring_of_cliques(30, 5)
eq("ring 30×5 节点数", len(rg.nodes), 150)
eq("ring 30×5 边数 = 30×C(5,2)+30", rg.m(), 30 * 10 + 30)
rfin, rq, rlevels, rnlev = louvain(rg)
eq("最终社区数 = 15（团两两合并，与论文一致）", len(set(rfin.values())), 15)
ck("最终 Q 与论文描述的全局最优一致（≈0.888）", abs(rq - 0.8878) < 0.002,
   "q=%.6f" % rq)
ck("level 序列单调不减",
   all(rlevels[k + 1] >= rlevels[k] - 1e-12 for k in range(len(rlevels) - 1)),
   str([round(x, 4) for x in rlevels]))

# 第一阶段的社区数应为 30（论文：「第一次 pass 找到自然划分」）
rp1, rq1, _ = one_level(rg, {n: n for n in rg.nodes})
eq("第一阶段社区数 = 30", len(set(rp1.values())), 30)
close("第一阶段 Q", rq1, rlevels[0], 1e-12)

# 每个社区恰好 5 个节点（自然划分 = 30 个团）
sizes = {}
for n, c in rp1.items():
    sizes[c] = sizes.get(c, 0) + 1
eq("每个社区 5 个节点", sorted(set(sizes.values())), [5])
eq("社区数 × 规模 = 节点数", sum(sizes.values()), 150)

# ------------------------------------------------- 分辨率极限（论文提及）
# 论文：模块度优化识别不出小于某个尺度的社区（resolution limit），Louvain 的
# 多层次特性部分缓解了它。这里用 Q 本身量化这个征兆。
natural30 = {n: n // 5 for n in rg.nodes}          # 30 个团 = 自然划分
q30 = modularity(rg, natural30)
merged15 = {n: (n // 5) // 2 for n in rg.nodes}    # 相邻团两两合并
q15 = modularity(rg, merged15)
ck("30 团图：合并成 15 个社区的 Q **高于**自然划分（分辨率极限征兆）",
   q15 > q30, "q30=%.6f q15=%.6f" % (q30, q15))
close("自然划分的 Q 等于第一阶段 Q", q30, rlevels[0], 1e-12)

# 只有 2 个团时不会合并：合并反而降低 Q
tiny = Graph()
for _c in range(2):
    _b = _c * 5
    for _i in range(5):
        for _j in range(_i + 1, 5):
            tiny.add_edge(_b + _i, _b + _j, 1.0)
tiny.add_edge(0, 5, 1.0)
eq("两团图边数 = 2×C(5,2)+1", tiny.m(), 21.0)
tfin, tq, _, _ = louvain(tiny)
eq("两个 5 阶团保持 2 个社区", len(set(tfin.values())), 2)
ck("两团图：自然划分的 Q 高于合成一个",
   modularity(tiny, {n: n // 5 for n in tiny.nodes})
   > modularity(tiny, {n: 0 for n in tiny.nodes}))

print("断言通过: %d" % OK)
if FAIL:
    print("失败 %d 条:" % len(FAIL))
    for f in FAIL:
        print("  - " + f)
    raise SystemExit(1)
print("ALL OK")
