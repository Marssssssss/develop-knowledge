"""变量消除与消元顺序 —— 自检（全部实跑）。

黄金值来自 pgmpy 官方 doctest（本轮实读原文）：
  EliminationOrder.py 的 WeightedMinFill 例子：Asia 网（c,d,g,i,s,j,l,h）
  上消 {c,d,g,l,s} 得 ['c','d','l','s','g']
  ExactInference.py 的 induced_width 例子：A→B,C→B,C→D,B→E 上
  顺序 ["C","D","A","B","E"] 的 induced_width 为 3
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    HEURISTICS,
    UndirectedGraph,
    elimination_fill_count,
    induced_width,
    is_chordal,
    maximal_cliques,
    moralize,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAIL: " + label
    PASS += 1


# ---------------------------------------------------------------- 1. 道德图
ASIA_NODES = ["c", "d", "g", "i", "s", "j", "l", "h"]
ASIA_EDGES = [
    ("c", "d"),
    ("d", "g"),
    ("i", "g"),
    ("i", "s"),
    ("s", "j"),
    ("g", "l"),
    ("l", "j"),
    ("j", "h"),
    ("g", "h"),
]
ASIA_CARD = {"c": 2, "d": 2, "g": 3, "i": 2, "s": 2, "j": 2, "l": 2, "h": 2}

moral = moralize(ASIA_NODES, ASIA_EDGES)


def eset(g):
    return {(min(u, v), max(u, v)) for u in g.nodes() for v in g._adj[u]}


expected_moral = {
    ("c", "d"),  # 骨架
    ("d", "g"),
    ("g", "i"),
    ("i", "s"),
    ("j", "s"),
    ("g", "l"),
    ("j", "l"),
    ("h", "j"),
    ("g", "h"),
    ("d", "i"),  # 嫁娶：g 的父 {d,i}
    ("l", "s"),  # 嫁娶：j 的父 {s,l}
    ("g", "j"),  # 嫁娶：h 的父 {g,j}
}
ok(eset(moral) == expected_moral, "道德图 = 骨架 + 三对父结点嫁娶")
ok(not is_chordal(moral), "Asia 道德图非弦图（i-s-j-g-i 是无弦 4-环）")

# ------------------------------------------- 2. 官方 doctest：WeightedMinFill
order = HEURISTICS["weightedminfill"](moral, ASIA_CARD).get_elimination_order(
    ["c", "d", "g", "l", "s"]
)
ok(order == ["c", "d", "l", "s", "g"], "官方 doctest：WeightedMinFill = [c,d,l,s,g]")

# 手算校验每一步的代价（基数 c=d=i=s=j=l=h=2，g=3）
probe = HEURISTICS["weightedminfill"](moral, ASIA_CARD)
ok(probe.cost("c") == 0, "c 只有一个邻居 d ⇒ 无 fill-in 边，代价 0")
ok(
    probe.cost("s") == 8,
    "s 的邻居因嫁娶变成 {i,j,l}，(i,j)(i,l) 各缺一条 ⇒ 2×4 = 8",
)
ok(
    probe.cost("g") == 28,
    "g 的邻居 {d,i,l,h,j} 里只有 (d,i)(l,j)(h,j) 已有边 ⇒ 7×4 = 28",
)
ok(
    probe.fill_in_edges("d") == [("c", "g"), ("c", "i")],
    "d 的 fill-in 边按邻居插入序配对，(g,i) 已有边故不产生",
)

# ---------------------------------------------- 3. 官方 doctest：induced_width
# A→B, C→B, C→D, B→E；CPD 作用域 = (A) (B,A,C) (C) (D,C) (E,B)
scopes = [("A",), ("B", "A", "C"), ("C",), ("D", "C"), ("E", "B")]
w = induced_width(scopes, ["C", "D", "A", "B", "E"])
ok(w == 3, "官方 doctest：induced_width([C,D,A,B,E]) == 3")
ok(
    induced_width(scopes, ["C", "D", "A", "B", "E"], consider_once=False) >= w,
    "关掉「因子只计一次」只会让团更大 ⇒ 宽度不降",
)
ok(
    induced_width(scopes, ["A", "B", "C", "D", "E"]) >= 1,
    "换成另一种顺序宽度也不小于 1（任意顺序都含二元团）",
)

# 链 A-B-C-D：宽度**依赖**顺序 —— 从端点消是 1，从中间消会把两端粘起来变 2
chain = [("A",), ("A", "B"), ("B", "C"), ("C", "D")]
ok(induced_width(chain, ["A", "B", "C", "D"]) == 1, "链上从头消 ⇒ 宽度 1")
ok(induced_width(chain, ["D", "C", "B", "A"]) == 1, "链上从尾消 ⇒ 宽度 1")
ok(
    induced_width(chain, ["B", "C", "D", "A"]) == 2,
    "先消中间点 B ⇒ 产生团 {A,C}，最大团 {A,B,C} ⇒ 宽度 2",
)

# 星形：先消叶子 ⇒ 无 fill-in；先消中心 ⇒ 3 条 fill-in
star = UndirectedGraph(["c", "a", "b", "d"], [("c", "a"), ("c", "b"), ("c", "d")])
ok(elimination_fill_count(star, ["a", "b", "d", "c"]) == 0, "星形先消叶子 ⇒ 0 fill-in")
ok(elimination_fill_count(star, ["c", "a", "b", "d"]) == 3, "星形先消中心 ⇒ 3 fill-in")

# ------------------------------------- 4. 数边 vs 加权：代价函数真的会分道扬镳
# x 的三个邻居 p,q,y 都是高基数(10)，y 的三个邻居 m,n,o 都是低基数(2)
# 只消 {x, y}：MinFill 数边(3 < 6) 选 x；WeightedMinFill 加权(300 > 72) 选 y
gnodes = ["x", "y", "p", "q", "m", "n", "o"]
gedges = [("x", "p"), ("x", "q"), ("x", "y"), ("y", "m"), ("y", "n"), ("y", "o")]
gcard = {"x": 10, "y": 10, "p": 10, "q": 10, "m": 2, "n": 2, "o": 2}
gsel = UndirectedGraph(gnodes, gedges)

o_minfill = HEURISTICS["minfill"](gsel, gcard).get_elimination_order(["x", "y"])
o_wminfill = HEURISTICS["weightedminfill"](gsel, gcard).get_elimination_order(["x", "y"])
o_minnb = HEURISTICS["minneighbors"](gsel, gcard).get_elimination_order(["x", "y"])
o_minw = HEURISTICS["minweight"](gsel, gcard).get_elimination_order(["x", "y"])
ok(o_minfill == ["x", "y"], "MinFill 数边：x 只需 3 条 < y 的 6 条 ⇒ 先消 x")
ok(o_wminfill == ["y", "x"], "WeightedMinFill 加权：x 要 300 > y 的 72 ⇒ 先消 y")
ok(o_minnb == ["x", "y"], "MinNeighbors 看度数：x 度 3 < y 度 4 ⇒ 先消 x")
ok(o_minw == ["y", "x"], "MinWeight 看邻居基数乘积：x 1000 > y 80 ⇒ 先消 y")
ok(o_minfill != o_wminfill, "数边与加权给出**不同**的消元顺序")

# 手算两种顺序实际新增的 fill-in 边数（不删点的三角化）
ok(
    elimination_fill_count(gsel, ["x", "y"]) == 12,
    "顺序 x→y：x 贡献 3 条，y 之后邻居变 5 个贡献 9 条 ⇒ 12",
)
ok(
    elimination_fill_count(gsel, ["y", "x"]) == 13,
    "顺序 y→x：y 贡献 6 条，x 之后邻居变 5 个贡献 7 条 ⇒ 13",
)

# 平局按结点顺序：全等代价时取列表中最靠前的
flat = UndirectedGraph(["x", "y", "z"], [])
ok(HEURISTICS["minfill"](flat, {}).get_elimination_order() == ["x", "y", "z"],
   "全等代价 ⇒ min() 稳定，按结点插入序消元")
ok(
    HEURISTICS["minneighbors"](flat, {}).get_elimination_order(["z", "y", "x"]) == ["x", "y", "z"],
    "nodes 参数只做集合筛选，顺序仍由图的结点插入序决定（传入 z,y,x 也得到 x,y,z）",
)
ok(
    HEURISTICS["minneighbors"](flat, {}).get_elimination_order(["z", "x"]) == ["x", "z"],
    "子集 {z,x} 的输出同样是图序而非传入序",
)

# ------------------------------------------------------ 5. Kjaerulff H1..H6
tri = UndirectedGraph(["a", "b", "c"], [("a", "b"), ("b", "c"), ("a", "c")])
tcard = {"a": 2, "b": 3, "c": 5}
h1 = HEURISTICS["h1"](tri, tcard)
s, e, m, c = h1._terms("a")
ok(s == 15, "S(i) = 邻居基数乘积 = 3×5 = 15")
ok(e == 2, "E(i) = 自身基数")
ok(m == 30 and c == 30, "三角形里含 a 的极大团只有一个 ⇒ M == C == 2×3×5 = 30")
ok(abs(h1.cost("a") - 15) < 1e-12, "H1 = S")
ok(abs(HEURISTICS["h2"](tri, tcard).cost("a") - 7.5) < 1e-12, "H2 = S/E")
ok(abs(HEURISTICS["h3"](tri, tcard).cost("a") + 15) < 1e-12, "H3 = S−M = −15")
ok(abs(HEURISTICS["h4"](tri, tcard).cost("a") + 15) < 1e-12, "H4 = S−C = −15")
ok(abs(HEURISTICS["h5"](tri, tcard).cost("a") - 0.5) < 1e-12, "H5 = S/M")
ok(abs(HEURISTICS["h6"](tri, tcard).cost("a") - 0.5) < 1e-12, "H6 = S/C")

# ------------------------------------------------------------ 6. 弦图与团
c4 = UndirectedGraph(["1", "2", "3", "4"], [("1", "2"), ("2", "3"), ("3", "4"), ("4", "1")])
ok(not is_chordal(c4), "4-环无弦 ⇒ 非弦图")
c4.add_edge("1", "3")
ok(is_chordal(c4), "加一条弦后变弦图")
ok(maximal_cliques(c4) == [["1", "2", "3"], ["1", "3", "4"]], "弦图极大团枚举正确")
ok(is_chordal(UndirectedGraph(["1", "2", "3"], [("1", "2"), ("2", "3")])), "路是弦图")
ok(all(len(cq) == 2 for cq in maximal_cliques(moral) if "c" in cq),
   "c 只连 d ⇒ 含 c 的极大团大小必为 2")

# 沿消元顺序补齐 fill-in 边（**不删点**）⇒ 整图被三角化成弦图
tri2 = moral.copy()
for node in HEURISTICS["minfill"](moral, ASIA_CARD).get_elimination_order():
    nb = tri2.neighbors(node)
    tri2.add_edges_from(
        [(u, v) for i, u in enumerate(nb) for v in nb[i + 1 :] if not tri2.has_edge(u, v)]
    )
ok(is_chordal(tri2), "沿消元顺序补 fill-in 边后整图成为弦图（三角化）")
ok(len(tri2._adj) == len(ASIA_NODES), "三角化只加边，不删点")

print("PASS =", PASS)
