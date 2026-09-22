"""连接树(junction tree)与三角化 —— 依 networkx chordal.py 与 pgmpy ExactInference.py 转写。

来源（本轮实读）：
  * networkx/networkx@main : networkx/algorithms/chordal.py
    - is_chordal：最大势搜索(MCS)找「无弦环破坏者」
    - complete_to_chordal_graph：MCS-M（Berry et al. 2004）做**极小**三角化，
      返回 (H, alpha)，alpha 是 1..n 的编号（越大越先被消）
    - chordal_graph_cliques：MCS 序下「已编号邻居集」即极大团
    - chordal_graph_treewidth = 最大团大小 − 1（官方 doctest: barbell_graph(4,6) → 3）
  * pgmpy/pgmpy@dev : pgmpy/inference/ExactInference.py 的 BeliefPropagation
    - _calibrate_junction_tree：Lauritzen-Spiegelhalter，先 collect(逆 BFS) 再
      distribute(BFS)
    - _update_beliefs：σ = Σ_{Ci−S} βi；βj ← βj·(σ/μ旧)；μ ← σ
    - _is_converged：每条边上两侧对 sepset 的投影三者相等
  * pgmpy/pgmpy@dev : pgmpy/models/JunctionTree.py
    - 结点是团、边是 sepset；add_edge 会因「成环」拒绝；check_model 要求连通
"""

from itertools import combinations


# ------------------------------------------------------------------ 图与弦性
class Graph:
    def __init__(self, nodes, edges):
        self.nodes = list(nodes)
        self.adj = {n: set() for n in self.nodes}
        for u, v in edges:
            self.add_edge(u, v)

    def add_edge(self, u, v):
        if u == v:
            return
        self.adj[u].add(v)
        self.adj[v].add(u)

    def has_edge(self, u, v):
        return v in self.adj[u]

    def degree(self, u):
        return len(self.adj[u])

    def copy(self):
        g = Graph(self.nodes, [])
        g.adj = {k: set(v) for k, v in self.adj.items()}
        return g

    def subgraph(self, nodes):
        keep = set(nodes)
        g = Graph([n for n in self.nodes if n in keep], [])
        g.adj = {k: set(v) & keep for k, v in self.adj.items() if k in keep}
        return g

    def edges(self):
        return [(u, v) for u in self.nodes for v in self.adj[u] if u < v]


def _is_complete(g):
    n = len(g.nodes)
    if n < 2:
        return True
    return sum(g.degree(u) for u in g.nodes) // 2 == n * (n - 1) // 2


def _find_missing_edge(g):
    nodes = set(g.nodes)
    for u in g.nodes:
        missing = nodes - set(g.adj[u]) - {u}
        if missing:
            return (u, missing.pop())
    return None


def max_cardinality_search(g, start=None):
    """MCS：每步选与已编号集合邻接最多的结点；返回编号序。"""
    unnumbered = set(g.nodes)
    s = start if start is not None else g.nodes[0]
    unnumbered.discard(s)
    numbered = [s]
    while unnumbered:
        v = max(unnumbered, key=lambda n: len(g.adj[n] & set(numbered)))
        unnumbered.discard(v)
        numbered.append(v)
    return numbered


def is_chordal(g):
    """networkx is_chordal 等价判据：MCS 序下每个已编号邻居集都必须是团。"""
    numbered = set()
    order = max_cardinality_search(g)
    for v in order:
        cand = set(g.adj[v]) & numbered
        sub = g.subgraph(list(cand))
        if not _is_complete(sub):
            return False
        numbered.add(v)
    return True


def chordality_breaker(g, start=None):
    """返回 (u,v,w) 三元组表示找到了无弦环；空元组表示弦图。"""
    order = max_cardinality_search(g, start)
    numbered = set()
    for v in order:
        cand = set(g.adj[v]) & numbered
        sub = g.subgraph(list(cand))
        if not _is_complete(sub):
            u, w = _find_missing_edge(sub)
            return (u, v, w)
        numbered.add(v)
    return ()


def complete_to_chordal_graph(g):
    """MCS-M 极小三角化（networkx 同名函数逐行转写）。返回 (H, alpha)。"""
    h = g.copy()
    alpha = {node: 0 for node in h.nodes}
    if is_chordal(h):
        return h, alpha
    chords = set()
    weight = {node: 0 for node in h.nodes}
    unnumbered = list(h.nodes)
    for i in range(len(h.nodes), 0, -1):
        z = max(unnumbered, key=lambda node: weight[node])
        unnumbered.remove(z)
        alpha[z] = i
        update_nodes = []
        for y in unnumbered:
            if g.has_edge(y, z):
                update_nodes.append(y)
            else:
                y_weight = weight[y]
                lower_nodes = [n for n in unnumbered if weight[n] < y_weight]
                sub = h.subgraph(lower_nodes + [z, y])
                if _has_path(sub, y, z):
                    update_nodes.append(y)
                    chords.add((z, y))
        for node in update_nodes:
            weight[node] += 1
    for u, v in chords:
        h.add_edge(u, v)
    return h, alpha


def _has_path(g, s, t):
    seen = {s}
    stack = [s]
    while stack:
        v = stack.pop()
        if v == t:
            return True
        for w in g.adj[v]:
            if w not in seen:
                seen.add(w)
                stack.append(w)
    return False


def chordal_graph_cliques(g):
    """MCS 序下「已编号邻居集」即极大团（networkx chordal_graph_cliques）。"""
    cliques = []
    numbered = set()
    for v in max_cardinality_search(g):
        cand = set(g.adj[v]) & numbered
        sub = g.subgraph(list(cand))
        if not _is_complete(sub):
            raise ValueError("Input graph is not chordal.")
        if cand:
            cliques.append(sorted(cand | {v}))
        else:
            cliques.append([v])
        numbered.add(v)
    # 只保留极大团（按集合包含关系去重）
    out = []
    for c in cliques:
        cs = set(c)
        if not any(cs < set(o) for o in cliques):
            out.append(c)
    return out


def treewidth(g):
    """chordal_graph_treewidth = 最大团大小 − 1。"""
    if not is_chordal(g):
        raise ValueError("Input graph is not chordal.")
    return max(len(c) for c in chordal_graph_cliques(g)) - 1


# --------------------------------------------------------------- 因子与团树

from pgmodel import (  # noqa: E402  F401  同包重导出，便于 selfcheck 单一入口
    Factor,
    JunctionTree,
    brute_force_marginals,
    build_junction_tree,
    check_running_intersection,
)

def barbell_graph(m1, m2):
    """networkx barbell_graph：两个 K_m1 用一条 m2 个结点的路连起来（共 2·m1+m2 点）。"""
    nodes = ["L%d" % i for i in range(m1)]
    nodes += ["P%d" % i for i in range(m2)]
    nodes += ["R%d" % i for i in range(m1)]
    edges = []
    for pre, a, b in (("L", 0, m1), ("R", 0, m1)):
        for i, j in combinations(range(a, b), 2):
            edges.append(("%s%d" % (pre, i - a), "%s%d" % (pre, j - a)))
    prev = "L0"
    for i in range(m2):
        edges.append((prev, "P%d" % i))
        prev = "P%d" % i
    edges.append((prev, "R0"))
    return Graph(nodes, edges)