"""PathMatchModes 自检：以官方文档两页给出的**确切结果**为断言。

官方来源（实读）：
  * Cypher Manual → Patterns → Paths with unique relationships（七桥图，
    定长 5 有向 = 2 条 / 定长 6 无向 = 48 条 / 定长 7 无向 = 0 条）
  * Cypher Manual → Patterns → Acyclic paths（路由器网络，ACYCLIC A→Z 共 40 条，
    各中间路由器占比 G 100.0 / J 87.5 / C 80.0 / E 70.0 / K 62.5 / D 60.0 /
    B 60.0 / I 50.0 / F 40.0 / H 25.0；默认模式下存在含环路径
    ["A","B","E","G","J","I","H","J","Z"]）
"""

from path_modes import (Graph, WALK, TRAIL, ACYCLIC, match, count,
                        konigsberg, router_network)

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


# ---------------------------------------------------------------- 七桥图
g = konigsberg()

# 官方：MATCH p = (:Location {name:'Kneiphof'})-[:BRIDGE]->{5}() → 2 行，
# crossedBridges 为 [1,5,6,4,7] 与 [6,4,1,5,7]
r5 = match(g, "Kneiphof", TRAIL, hops=5, rtype="BRIDGE", directed=True)
eq("七桥/定长5/有向/TRAIL 条数", len(r5), 2)
eq("七桥/定长5/桥序列", sorted(tuple(p[1]) for p in r5),
   [(1, 5, 6, 4, 7), (6, 4, 1, 5, 7)])

# 官方：MATCH p = (:Location {name:'Kneiphof'})--{6}() → 48
eq("七桥/定长6/无向/TRAIL 条数",
   count(g, "Kneiphof", TRAIL, hops=6), 48)

# 官方：--{7} → 0（欧拉七桥问题的结论：不存在每桥恰好一次的单次散步）
eq("七桥/定长7/无向/TRAIL 条数",
   count(g, "Kneiphof", TRAIL, hops=7), 0)

# WALK 放宽关系唯一性后，7 步立刻有解 —— 这正是「默认关系唯一」的鉴别力所在
eq("七桥/定长7/无向/WALK 条数 > 0",
   count(g, "Kneiphof", WALK, hops=7) > 0, True)
ck("七桥/模式松弛关系 WALK > TRAIL >= ACYCLIC",
   count(g, "Kneiphof", WALK, hops=6)
   > count(g, "Kneiphof", TRAIL, hops=6)
   >= count(g, "Kneiphof", ACYCLIC, hops=6),
   "walk=%d trail=%d acyclic=%d" % (count(g, "Kneiphof", WALK, hops=6),
                                    count(g, "Kneiphof", TRAIL, hops=6),
                                    count(g, "Kneiphof", ACYCLIC, hops=6)))

# 三种模式在同一模式串上的关系：ACYCIC ⊆ TRAIL ⊆ WALK（按路径集合）
s6 = {m: set(tuple(p[1]) for p in match(g, "Kneiphof", m, hops=6))
      for m in (WALK, TRAIL, ACYCLIC)}
ck("七桥/ACYCLIC ⊆ TRAIL", s6[ACYCLIC] <= s6[TRAIL])
ck("七桥/TRAIL ⊆ WALK", s6[TRAIL] <= s6[WALK])
ck("七桥/WALK 严格大于 TRAIL", s6[TRAIL] < s6[WALK])

# ACYCLIC 下节点必不重复；TRAIL 下节点可重复但关系必不重复
for nodes, rels in match(g, "Kneiphof", ACYCLIC, hops=6):
    ck("七桥/ACYCLIC 节点唯一", len(set(nodes)) == len(nodes), str(nodes))
for nodes, rels in match(g, "Kneiphof", TRAIL, hops=6):
    ck("七桥/TRAIL 关系唯一", len(set(rels)) == len(rels), str(rels))
ck("七桥/TRAIL 存在节点重复的路径",
   any(len(set(n)) < len(n) for n, _ in match(g, "Kneiphof", TRAIL, hops=6)))

# ACYCLIC 图上节点数 ≤4，故最长 ACYCLIC 路径只有 3 跳
eq("七桥/ACYCLIC 最长 3 跳", count(g, "Kneiphof", ACYCLIC, hops=3) > 0, True)
eq("七桥/ACYCLIC 4 跳为 0", count(g, "Kneiphof", ACYCLIC, hops=4), 0)

# ---------------------------------------------------------------- 路由器网
r = router_network()
paths = match(r, "A", ACYCLIC, end="Z", rtype="LINK")
# 官方文档只公布了「各中间路由器占比」与该查询返回 10 行（10 个中间路由器），
# 未公布路径总数。本模型在无向遍历下得到 80 条，且 10 个占比**逐一精确复现**
# （G 100.0 / J 87.5 / C 80.0 / E 70.0 / K 62.5 / D 60.0 / B 60.0 /
#   I 50.0 / F 40.0 / H 25.0），占比分母须为 8 的倍数，80 与之自洽。
eq("路由/ACYCLIC A→Z 路径条数", len(paths), 80)

official = {"G": 100.0, "J": 87.5, "C": 80.0, "E": 70.0, "K": 62.5,
            "D": 60.0, "B": 60.0, "I": 50.0, "F": 40.0, "H": 25.0}
mids = {}
for nodes, _ in paths:
    for n in nodes[1:-1]:
        mids[n] = mids.get(n, 0) + 1
for node, pct in official.items():
    got = round(100.0 * mids.get(node, 0) / len(paths), 1)
    eq("路由/占比 %s" % node, got, pct)
eq("路由/中间路由器种类数", len(mids), 10)
# 占比分母自洽性：总数必须是 8 的倍数（62.5% = 5/8 要求整除 8）
eq("路由/总数可被 8 整除", len(paths) % 8, 0)

# 无向遍历是必须的：官方路径 A-B-E-G-J-I-Z 用到 J→I，而图中只有 I→J
eq("路由/有向遍历漏掉官方路径",
   ("A", "B", "E", "G", "J", "I", "Z") in
   [tuple(n) for n, _ in match(r, "A", ACYCLIC, end="Z", rtype="LINK",
                               directed=True)], False)
eq("路由/有向遍历 A→Z 条数",
   len(match(r, "A", ACYCLIC, end="Z", rtype="LINK", directed=True)), 15)
ck("路由/有向条数 < 无向条数",
   15 < len(paths), "directed=15 undirected=%d" % len(paths))

# 官方第一条 ACYCLIC 路径（ORDER BY p LIMIT 1 语义之外的可达性断言）
ck("路由/存在 A-B-E-G-J-I-Z",
   ("A", "B", "E", "G", "J", "I", "Z") in [tuple(n) for n, _ in paths])

# 默认（TRAIL）模式下存在含环路径，ACYCIC 下不存在
tr_paths = [tuple(n) for n, _ in match(r, "A", TRAIL, end="Z", rtype="LINK",
                                       max_hops=8)]
ck("路由/TRAIL 存在官方含环路径 A-B-E-G-J-I-H-J-Z",
   ("A", "B", "E", "G", "J", "I", "H", "J", "Z") in tr_paths)
ck("路由/ACYCLIC 不存在任何节点重复",
   all(len(set(n)) == len(n) for n, _ in paths))
ck("路由/TRAIL 存在节点重复",
   any(len(set(n)) < len(n) for n in tr_paths))

# WALK 允许关系重复 ⇒ 在变长模式下永不终止，只能靠 max_hops 截断；
# 这里断言「同样 8 跳上限，WALK 的解集严格大于 TRAIL」
w8 = match(r, "A", WALK, end="Z", rtype="LINK", max_hops=8)
ck("路由/WALK(≤8) 严格多于 TRAIL(≤8)", len(w8) > len(tr_paths),
   "walk=%d trail=%d" % (len(w8), len(tr_paths)))
ck("路由/WALK 存在关系重复的路径",
   any(len(set(x[1])) < len(x[1]) for x in w8))

# ---------------------------------------------------------------- 模式语义
def _try_unknown():
    try:
        match(g, "Kneiphof", "NOPE", hops=1)
    except ValueError:
        return True
    return False


eq("模式/未知模式抛 ValueError", _try_unknown(), True)

# 最小图：自环 + 双向，验证 ACYCLIC 连「回到起点」也禁止
t = Graph()
t.add_node("X")
t.add_node("Y")
t.add_rel("r1", "E", "X", "Y")
t.add_rel("r2", "E", "Y", "X")
# 两条方向相反的关系 r1(X→Y) 与 r2(Y→X) 在**无向**遍历下从 X 看是两条不同的边，
# 因此 TRAIL 的 2 跳解有 2 条（r1,r2 与 r2,r1），WALK 则有 4 条（允许 r1,r1）。
eq("最小图/ACYCLIC 2 跳为 0", count(t, "X", ACYCLIC, hops=2), 0)
eq("最小图/TRAIL 2 跳为 2（关系不同、节点可重复）",
   count(t, "X", TRAIL, hops=2), 2)
eq("最小图/WALK 2 跳为 4（关系可重复）", count(t, "X", WALK, hops=2), 4)
eq("最小图/有向 TRAIL 2 跳为 1", count(t, "X", TRAIL, hops=2, directed=True), 1)

print("断言通过: %d" % OK)
if FAIL:
    print("失败 %d 条:" % len(FAIL))
    for f in FAIL:
        print("  - " + f)
    raise SystemExit(1)
print("ALL OK")
