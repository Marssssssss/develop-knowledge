# -*- coding: utf-8 -*-
"""Cypher 执行计划: 火山模型(Volcano/迭代器)算子树最小实现.

依据 Neo4j Cypher Manual "Execution plans":
1. 计划 = 算子二叉树, 根是 ProduceResults, 数据自底向上流; 展示时
   二元算子的 RHS(右输入)先显示且缩进更深.
2. 二元算子两类: Apply 风格(LHS 每行驱动 RHS 重新执行, 带变量绑定)
   vs Join/CartesianProduct(两侧完整输入组合).
3. 默认惰性(lazy/streaming): 算子一产出就推给父算子; 聚合/排序类是
   Eager 算子, 必须收完全部输入才能产出, 存在内存代价.
4. Estimated Rows 来自统计与选择性模型, 用于计划选择, 可能是错的;
   PROFILE 才给出真实 Rows / DB Hits.

算子实现见 operators.py; 本文件: 图 + 查询计划 + demo.
"""
from operators import (
    Row, AllNodesScan, NodeByLabelScan, Filter, Expand, Projection, Sort,
    CartesianProduct, Apply,
)


# ---------- 图与查询计划 ----------

def build_graph():
    """官方 Movies 简化图."""
    nodes = {
        "charlie": {"labels": ["Person"], "props": {"name": "Charlie Sheen"}},
        "martin":  {"labels": ["Person"], "props": {"name": "Martin Sheen"}},
        "michael": {"labels": ["Person"], "props": {"name": "Michael Douglas"}},
        "oliver":  {"labels": ["Director"], "props": {"name": "Oliver Stone"}},
        "wallst":  {"labels": ["Movie"], "props": {"title": "Wall Street"}},
    }
    out = {
        "charlie": [("r1", "wallst")], "martin": [("r2", "wallst")],
        "michael": [("r3", "wallst")], "oliver": [("r4", "wallst")],
    }
    return {"nodes": nodes, "out": out}


def plan_actor_movie(g):
    """MATCH (p:Person)-[r:ACTED_IN]->(m) RETURN p.name, m.title
    计划: Projection <- Expand(All) <- NodeByLabelScan(Person)
    """
    scan = NodeByLabelScan("p", "Person")
    expand = Expand(scan, "p", "r", "ACTED_IN", "m")
    return Projection(expand, lambda r: Row({
        "name": g["nodes"][r["p"]]["props"]["name"],
        "title": g["nodes"][r["m"]]["props"].get("title"),
    }))


def plan_apply(g):
    """MATCH (m:Movie) MATCH (p:Person)-[:ACTED_IN]->(m)  (连续两个 MATCH)
    计划: Apply(LHS=NodeByLabelScan(Movie), RHS=Expand+Filter 逐电影重启)
    """
    left = NodeByLabelScan("m", "Movie")

    def right_factory(bindings):
        scan = AllNodesScan("p")
        expand = Expand(scan, "p", "r", "ACTED_IN", "m2")
        return Filter(expand, lambda row: row["m2"] == bindings["m"],
                      detail=f"m2 == {bindings['m']}")

    return Apply(left, right_factory, right_label="Expand+Filter")


def plan_cartesian(g):
    """MATCH (m:Movie), (d:Director) RETURN m.title, d.name  (笛卡尔积)"""
    cp = CartesianProduct(NodeByLabelScan("m", "Movie"),
                          NodeByLabelScan("d", "Director"))
    return Projection(cp, lambda r: Row({
        "title": g["nodes"][r["m"]]["props"]["title"],
        "dname": g["nodes"][r["d"]]["props"]["name"],
    }))


def plan_sort(g):
    """MATCH (p:Person) RETURN p.name ORDER BY p.name  (Eager: Sort)"""
    scan = NodeByLabelScan("p", "Person")
    proj = Projection(scan, lambda r: Row(
        {"name": g["nodes"][r["p"]]["props"]["name"]}))
    return Sort(proj, key=lambda r: r["name"])


def demo():
    g = build_graph()
    ctx = {"graph": g}

    print("== 1. 惰性流水线: Projection <- Expand <- NodeByLabelScan ==")
    rows = plan_actor_movie(g).exec(ctx)
    for r in rows:
        print(f"  {r['name']:16s} -ACTED_IN-> {r['title']}")
    print("  计划树(官方: 自底向上阅读, 数据从叶算子流向根):")
    print(plan_actor_movie(g).describe(depth=1))

    print("\n== 2. Apply 风格: LHS 每行驱动 RHS 重启(连续 MATCH) ==")
    rows = plan_apply(g).exec(ctx)
    for r in rows:
        print(f"  {g['nodes'][r['m']]['props']['title']}: "
              f"{g['nodes'][r['p']]['props']['name']}")
    print("  -- 1 部电影 × RHS 每电影重启一次 = 3 行(嵌套循环语义)")

    print("\n== 3. Join 风格: CartesianProduct(两侧完整输入) ==")
    rows = plan_cartesian(g).exec(ctx)
    for r in rows:
        print(f"  {r['title']} x {r['dname']}")
    print(f"  -- 1 Movie x 1 Director = {len(rows)} 行(笛卡尔积语义)")

    print("\n== 4. Eager 算子: Sort 必须收完全部输入才能产出 ==")
    rows = plan_sort(g).exec(ctx)
    print(f"  {sorted(r['name'] for r in rows)}")
    print("  -- Sort/聚合类: 上游行全部物化进缓冲, 内存代价 O(行数)")

    print("\n== 5. Estimated Rows vs 实际 Rows(官方: 估算可能错) ==")
    print("  Estimated Rows: 标签统计+选择性模型, 用于计划选择, 无运行时反馈")
    print("  Rows/DB Hits:   仅 PROFILE 实测; DB Hits = 存储层访问抽象单位")
    print("  -- 估算与实测偏差大 -> 统计过期, 需更新统计或加索引")


if __name__ == "__main__":
    demo()
