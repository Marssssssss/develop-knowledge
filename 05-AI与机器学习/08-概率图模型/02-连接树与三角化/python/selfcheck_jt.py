"""连接树与三角化 —— 自检（全部实跑）。

黄金值：
  networkx chordal.py 官方 doctest：nx.barbell_graph(4, 6) 的 treewidth 为 3
  pgmpy BeliefPropagation 的 LS 校准：collect + distribute 后 sepset 三方一致
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    Factor,
    Graph,
    JunctionTree,
    barbell_graph,
    brute_force_marginals,
    check_running_intersection,
    chordal_graph_cliques,
    chordality_breaker,
    complete_to_chordal_graph,
    is_chordal,
    treewidth,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAIL: " + label
    PASS += 1


# ------------------------------------------------------------- 1. 弦图判定
c4 = Graph(["1", "2", "3", "4"], [("1", "2"), ("2", "3"), ("3", "4"), ("4", "1")])
ok(not is_chordal(c4), "4-环无弦 ⇒ 非弦图")
ok(chordality_breaker(c4) != (), "chordality_breaker 能给出 (u,v,w) 三元组")
c4b = Graph(["1", "2", "3", "4"], [("1", "2"), ("2", "3"), ("3", "4"), ("4", "1"), ("1", "3")])
ok(is_chordal(c4b), "加弦后是弦图")
ok(chordality_breaker(c4b) == (), "弦图的 chordality_breaker 返回空元组")
path = Graph(["a", "b", "c", "d"], [("a", "b"), ("b", "c"), ("c", "d")])
ok(is_chordal(path), "路是弦图")
ok(treewidth(path) == 1, "路的树宽 = 2−1 = 1")

# ------------------------------------------- 2. 官方 doctest：barbell(4,6)
bb = barbell_graph(4, 6)
ok(len(bb.nodes) == 2 * 4 + 6, "barbell_graph(4,6) 共 14 个结点")
ok(is_chordal(bb), "barbell 是弦图（两个团 + 一条路）")
ok(treewidth(bb) == 3, "官方 doctest：barbell_graph(4,6) 的 treewidth == 3")
ok(
    max(len(c) for c in chordal_graph_cliques(bb)) == 4,
    "最大团就是 K4 本身（路径部分不构成更大的团）",
)

# --------------------------------------------------------- 3. MCS-M 三角化
h, alpha = complete_to_chordal_graph(c4)
ok(is_chordal(h), "MCS-M 三角化后必为弦图")
ok(len(h.edges()) >= len(c4.edges()), "三角化只加边，不删边")
ok(len(h.edges()) - len(c4.edges()) == 1, "4-环只需补 1 条弦（极小三角化）")
ok(max(alpha.values()) == 4 and min(alpha.values()) == 1, "alpha 是 1..n 的一个排列")
ok(len(set(alpha.values())) == 4, "alpha 的取值两两不同")
h_again, _ = complete_to_chordal_graph(c4)
ok(sorted(map(sorted, h.edges())) == sorted(map(sorted, h_again.edges())), "三角化是确定性的")
# 已经是弦图 ⇒ 原样返回且 alpha 全 0
h2, alpha2 = complete_to_chordal_graph(c4b)
ok(sorted(map(sorted, h2.edges())) == sorted(map(sorted, c4b.edges())), "弦图输入 ⇒ 不加任何边")
ok(set(alpha2.values()) == {0}, "弦图输入的 alpha 全为 0（networkx 的短路分支）")

# 6-环：三角化需要的弦数 ≥ 3
c6 = Graph([str(i) for i in range(6)], [(str(i), str((i + 1) % 6)) for i in range(6)])
ok(not is_chordal(c6), "6-环非弦图")
h6, _ = complete_to_chordal_graph(c6)
ok(is_chordal(h6), "6-环三角化后为弦图")
ok(len(h6.edges()) - len(c6.edges()) >= 3, "6-环至少要补 3 条弦（实测 %d）"
   % (len(h6.edges()) - len(c6.edges()),))

# ----------------------------------------------- 4. 极大团 / 团树 / RIP
# 三角化的三角链：{A,B,C} 与 {C,D,E}
tri_chain = Graph(
    ["A", "B", "C", "D", "E"],
    [("A", "B"), ("A", "C"), ("B", "C"), ("C", "D"), ("C", "E"), ("D", "E")],
)
ok(is_chordal(tri_chain), "两个三角形共享一个点 ⇒ 弦图")
cliques = chordal_graph_cliques(tri_chain)
ok(sorted(cliques) == [["A", "B", "C"], ["C", "D", "E"]], "极大团恰为两个三角形")
ok(treewidth(tri_chain) == 2, "树宽 = 3−1 = 2")
ok(check_running_intersection(cliques, [(0, 1, 1)]), "两团的团树天然满足 RIP")

# ---------------------------------------- 5. 团树校准 vs 暴力枚举（真值参照）
variables = ["A", "B", "C", "D", "E"]
card = {v: 2 for v in variables}


def clique_potential(vars, seed):
    table = {}
    n = len(vars)
    for i in range(2 ** n):
        assign = tuple((i >> (n - 1 - k)) & 1 for k in range(n))
        table[assign] = 0.2 + 0.1 * ((seed * (i + 1)) % 7)
    return Factor(vars, table)


pots = [clique_potential(["A", "B", "C"], 3), clique_potential(["C", "D", "E"], 5)]
jt = JunctionTree(cliques, pots)
ok(jt.edges == [(0, 1, 1)], "团树只有一条边，sepset 大小 1（共享变量 C）")
jt.calibrate()
ok(jt.is_converged(), "LS 校准后 sepset 三方一致 ⇒ 已收敛")
truth = brute_force_marginals(variables, card, pots)
for v in variables:
    ok(jt.marginal(v).isclose(truth[v]), "变量 %s 的边缘与暴力枚举一致" % v)

# 负向：不做「除掉旧消息」= 把同一条消息乘两遍 ⇒ 结果必然偏离真值
jt_bad = JunctionTree(cliques, pots)
jt_bad.calibrate(divide_out=False)
bad = [v for v in variables if not jt_bad.marginal(v).isclose(truth[v])]
ok(len(bad) > 0, "关掉 divide-out 后至少有一个变量的边缘与真值不符（实测 %d 个）" % len(bad))
ok(
    all(jt.marginal(v).isclose(truth[v]) for v in variables),
    "对照：保留 divide-out 时全部变量都对得上",
)

# ------------------------------------------- 6. 三团链上的两趟消息传递
# {A,B} -- {B,C} -- {C,D}
chain_cliques = [["A", "B"], ["B", "C"], ["C", "D"]]
chain_pots = [
    clique_potential(["A", "B"], 2),
    clique_potential(["B", "C"], 4),
    clique_potential(["C", "D"], 6),
]
jt2 = JunctionTree(chain_cliques, chain_pots)
ok(
    check_running_intersection(chain_cliques, jt2.edges),
    "三团链的连接树满足 RIP",
)
jt2.calibrate()
truth2 = brute_force_marginals(["A", "B", "C", "D"], {v: 2 for v in "ABCD"}, chain_pots)
for v in "ABCD":
    ok(jt2.marginal(v).isclose(truth2[v]), "三团链：变量 %s 边缘正确" % v)
ok(len(jt2.edges) == 2, "三团的连接树是两棵树边（链式）")

print("PASS =", PASS)
