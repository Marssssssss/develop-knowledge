# -*- coding: utf-8 -*-
"""量化路径模式 (Quantified Path Patterns) 最小实现.

语义依据 Neo4j Cypher Manual "Patterns / Variable-length paths":
- ((a)-[r:NEXT]->(b)){min,max} 在概念上展开为固定长度模式的"并集":
  重复 min..max 次, 前一段终点必须与后一段起点为同一节点.
- 组变量(group variables): 量词内部声明的变量, 在外部引用时绑定为"列表".
- 关系唯一性: 默认同一 MATCH 模式中, 一条关系在一次匹配(路径)内不得重复遍历.
- 内联谓词(inline WHERE): 遍历时逐步剪枝, 避免路径爆炸.

图例来自官方文档英国铁路简化版: Denmark Hill <-> Clapham Junction,
两条服务一条 1 跳直连, 一条 3 跳绕行.
"""
from collections import defaultdict


class Graph:
    def __init__(self):
        self.next_rid = 0
        self.out = defaultdict(list)   # node -> [(rid, type, dst, props)]
        self.inn = defaultdict(list)   # node -> [(rid, type, src, props)]
        self.rel = {}                  # rid -> (type, src, dst, props)

    def add_edge(self, src, dst, etype, props=None):
        rid = self.next_rid
        self.next_rid += 1
        props = dict(props or {})
        self.rel[rid] = (etype, src, dst, props)
        self.out[src].append((rid, etype, dst, props))
        self.inn[dst].append((rid, etype, src, props))
        return rid

    def neighbors(self, node, etype, direction):
        """direction: 'out' / 'in' / 'both', 返回 [(rid, other_node, props)]."""
        res = []
        if direction in ("out", "both"):
            for rid, t, dst, p in self.out[node]:
                if t == etype:
                    res.append((rid, dst, p))
        if direction in ("in", "both"):
            for rid, t, src, p in self.inn[node]:
                if t == etype:
                    res.append((rid, src, p))
        return res


def match_quantified(g, start, etype, lo, hi, direction="out",
                     inline_pred=None, max_results=None):
    """匹配 (( )-[etype]->( )){lo,hi}, 返回绑定列表.

    每个结果 = {
      'nodes':   组变量 m 的绑定列表(不含起点), 官方语义: l 从 origin 侧收集,
                 m 从第二段开始收集,
      'rels':    组变量 r 的绑定列表,
      'end':     终点单例,
    }
    inline_pred(rid, props, node) 为 True 才继续深入 —— 对应内联 WHERE 剪枝.
    """
    results = []
    path_rels, path_nodes = [], [start]
    used = set()  # 关系唯一性: 一条路径内关系不得重复遍历

    def dfs(cur, depth):
        if lo <= depth:
            results.append({
                "rels": list(path_rels),
                "nodes": list(path_nodes[1:]),  # 组变量不含起点单例
                "end": cur,
            })
            if max_results and len(results) >= max_results:
                return True
        if depth == hi:
            return False
        stop = False
        for rid, nxt, props in g.neighbors(cur, etype, direction):
            if rid in used:          # 关系唯一性检查
                continue
            if inline_pred and not inline_pred(rid, props, nxt):
                continue             # 内联谓词剪枝: 该分支整棵子树被砍掉
            used.add(rid)
            path_rels.append(rid)
            path_nodes.append(nxt)
            if dfs(nxt, depth + 1):
                stop = True
            path_rels.pop()
            path_nodes.pop()
            used.discard(rid)
            if stop:
                break
        return stop

    dfs(start, 0)
    return results


def build_railway():
    """官方文档铁路图简化: 两站四停靠点, 1 跳直连 + 3 跳绕行."""
    g = Graph()
    # 服务 A(直连): dk -> cj            1 跳
    g.add_edge("dk", "cj", "LINK", {"distance": 2.0})
    # 服务 B(绕行): dk -> x -> y -> cj  3 跳
    g.add_edge("dk", "x", "LINK", {"distance": 0.5})
    g.add_edge("x", "y", "LINK", {"distance": 0.5})
    g.add_edge("y", "cj", "LINK", {"distance": 0.5})
    # 环形支线(演示关系唯一性如何防止无限路径): x -> dk
    g.add_edge("x", "dk", "LINK", {"distance": 0.9})
    return g


def demo():
    g = build_railway()

    print("== 1. {1,3} 展开: 1 跳 + 3 跳两条路径都命中 ==")
    for r in match_quantified(g, "dk", "LINK", 1, 3):
        hops = len(r["rels"])
        total = sum(g.rel[rid][3]["distance"] for rid in r["rels"])
        print(f"  dk -{hops} 跳-> {r['end']}  组变量 rels={r['rels']} 距离={total}")

    print("\n== 2. 无上界 + 环: 关系唯一性保证有限结果 ==")
    unbounded = match_quantified(g, "dk", "LINK", 1, 10**9)
    print(f"  路径数 = {len(unbounded)} (若无关系唯一性, 环 x->dk 会导致无限展开)")

    print("\n== 3. 组变量绑定: nodes 不含起点单例 ==")
    r = match_quantified(g, "dk", "LINK", 3, 3)[0]
    print(f"  nodes(组变量 m)={r['nodes']}, rels(组变量 r)={r['rels']}, end(单例)={r['end']}")

    print("\n== 4. 路径爆炸与内联谓词剪枝 ==")
    # 网格图: 每层 3 个节点, 层层全连接, 4 层 -> 3^3=27 条 3 跳路径
    g2 = Graph()
    layers = [[f"L{i}n{j}" for j in range(3)] for i in range(4)]
    for i in range(3):
        for a in layers[i]:
            for b in layers[i + 1]:
                g2.add_edge(a, b, "E", {})
    all_paths = match_quantified(g2, "L0n0", "E", 3, 3)
    print(f"  无谓词: 3 层全连接网格 3 跳路径数 = {len(all_paths)} (指数爆炸)")
    pruned = match_quantified(
        g2, "L0n0", "E", 3, 3,
        inline_pred=lambda rid, p, node: node != "L3n2")  # 剪掉通往 L3n2 的分支
    print(f"  内联谓词剪掉 L3n2 分支后 = {len(pruned)} 条"
          f" (剪枝发生在遍历时, 不是先枚举再过滤)")

    print("\n== 5. 上界收紧: {1,1} 等价于固定长度 1 跳 ==")
    for r in match_quantified(g, "dk", "LINK", 1, 1):
        print(f"  dk -> {r['end']}")


if __name__ == "__main__":
    demo()
