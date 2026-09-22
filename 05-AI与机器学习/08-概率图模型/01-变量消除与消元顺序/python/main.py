"""变量消除与消元顺序启发式 —— 依 pgmpy 官方实现转写。

来源（本轮实读）：
  * pgmpy/pgmpy@dev : pgmpy/inference/EliminationOrder.py
    - BaseEliminationOrder.get_elimination_order：贪心循环 = 取当前 cost 最小的
      结点（min() 稳定 ⇒ 平局按结点在图里的顺序），加 fill-in 边，删点，重算
    - fill_in_edges：邻居两两组合中尚无边的一对
    - MinFill / MinNeighbors / MinWeight / WeightedMinFill 四个代价函数
    - Kjaerulff(1990) H1..H6：S(邻居基数乘积) / E(自身基数) / M(含该点的极大团
      最大 Size) / C(含该点的极大团 Size 之和)
  * pgmpy/pgmpy@dev : pgmpy/inference/ExactInference.py
    - induced_graph / induced_width：width = 最大团大小 − 1
    - 官方口径：CPD 作用域 + 每步消元产生的中间作用域都进团集合；已被消掉的
      变量所在的因子**不再参与后续消元**（"all the factors should be considered
      only once"）
"""

from itertools import combinations


class UndirectedGraph:
    """保序的无向图：结点顺序即平局裁决顺序（pgmpy 用 networkx 的插入序）。"""

    def __init__(self, nodes, edges):
        self._nodes = list(nodes)
        self._adj = {n: set() for n in self._nodes}
        for u, v in edges:
            self.add_edge(u, v)

    def nodes(self):
        return list(self._nodes)

    def has_edge(self, u, v):
        return v in self._adj[u]

    def add_edge(self, u, v):
        if u == v:
            return
        self._adj[u].add(v)
        self._adj[v].add(u)

    def add_edges_from(self, ebunch):
        for u, v in ebunch:
            self.add_edge(u, v)

    def neighbors(self, node):
        """按结点插入序返回邻居 —— fill_in_edges 的配对顺序由此决定。"""
        return [n for n in self._nodes if n in self._adj[node]]

    def degree(self, node):
        return len(self._adj[node])

    def remove_node(self, node):
        for n in self._adj[node]:
            self._adj[n].discard(node)
        del self._adj[node]
        self._nodes.remove(node)

    def copy(self):
        g = UndirectedGraph(self._nodes, [])
        g._adj = {k: set(v) for k, v in self._adj.items()}
        return g


def moralize(nodes, directed_edges):
    """贝叶斯网 → 道德图：保留骨架 + 给每个结点的全体父结点两两连边（嫁娶）。"""
    g = UndirectedGraph(nodes, [(u, v) for u, v in directed_edges])
    parents = {n: [] for n in nodes}
    for u, v in directed_edges:
        parents[v].append(u)
    for child, ps in parents.items():
        for a, b in combinations(ps, 2):
            g.add_edge(a, b)
    return g


def maximal_cliques(g, containing=None):
    """Bron–Kerbosch（带 pivot）枚举极大团；containing 非空时只返回含该点的。"""
    result = []
    nodes = g.nodes()

    def expand(r, p, x):
        if not p and not x:
            if containing is None or containing in r:
                result.append(sorted(r))
            return
        pivot = max(p | x, key=lambda n: len(g._adj[n] & p), default=None)
        for v in list(p - (g._adj[pivot] if pivot else set())):
            expand(r | {v}, p & g._adj[v], x & g._adj[v])
            p = p - {v}
            x = x | {v}

    expand(set(), set(nodes), set())
    return result


class BaseEliminationOrder:
    """pgmpy BaseEliminationOrder 的转写；子类只实现 cost()。"""

    def __init__(self, graph, cardinality=None):
        self.g = graph.copy()
        self.cardinality = dict(cardinality or {})

    def cost(self, node):
        return 0

    def fill_in_edges(self, node):
        return [
            (u, v)
            for u, v in combinations(self.g.neighbors(node), 2)
            if not self.g.has_edge(u, v)
        ]

    def _weight(self, nodes):
        w = 1
        for n in nodes:
            w *= self.cardinality.get(n, 1)
        return w

    def get_elimination_order(self, nodes=None):
        if nodes is None:
            remaining = self.g.nodes()
        else:
            keep = set(nodes)
            remaining = [n for n in self.g.nodes() if n in keep]
        ordering = []
        while remaining:
            node = min(remaining, key=self.cost)
            ordering.append(node)
            remaining.remove(node)
            self.g.add_edges_from(self.fill_in_edges(node))
            self.g.remove_node(node)
        return ordering


class MinFill(BaseEliminationOrder):
    def cost(self, node):
        return len(self.fill_in_edges(node))


class MinNeighbors(BaseEliminationOrder):
    def cost(self, node):
        return self.g.degree(node)


class MinWeight(BaseEliminationOrder):
    def cost(self, node):
        return self._weight(self.g.neighbors(node))


class WeightedMinFill(BaseEliminationOrder):
    def cost(self, node):
        return sum(self._weight(e) for e in self.fill_in_edges(node))


class _Kjaerulff(BaseEliminationOrder):
    def _terms(self, node):
        s = self._weight(self.g.neighbors(node))
        e = self.cardinality.get(node, 1)
        sizes = [self._weight(c) for c in maximal_cliques(self.g, containing=node)]
        m = max(sizes) if sizes else 0
        c = sum(sizes)
        return s, e, m, c


class H1(_Kjaerulff):
    def cost(self, node):
        return self._terms(node)[0]


class H2(_Kjaerulff):
    def cost(self, node):
        s, e, _, _ = self._terms(node)
        return s / e


class H3(_Kjaerulff):
    def cost(self, node):
        s, _, m, _ = self._terms(node)
        return s - m


class H4(_Kjaerulff):
    def cost(self, node):
        s, _, _, c = self._terms(node)
        return s - c


class H5(_Kjaerulff):
    def cost(self, node):
        s, _, m, _ = self._terms(node)
        return s / m if m else float("inf")


class H6(_Kjaerulff):
    def cost(self, node):
        s, _, _, c = self._terms(node)
        return s / c if c else float("inf")


HEURISTICS = {
    "minfill": MinFill,
    "minneighbors": MinNeighbors,
    "minweight": MinWeight,
    "weightedminfill": WeightedMinFill,
    "h1": H1,
    "h2": H2,
    "h3": H3,
    "h4": H4,
    "h5": H5,
    "h6": H6,
}


def induced_width(factor_scopes, elimination_order, consider_once=True):
    """pgmpy induced_width：团集合 = CPD 作用域 ∪ 每步消元产生的中间作用域。

    consider_once=True 是官方口径：已经含被消掉变量的因子不再参与后续消元；
    关掉它就变成"因子可重复计入"，只会让团更大（宽度只会更差）。
    """
    cliques = {tuple(phi) for phi in factor_scopes}
    working = {}
    for phi in factor_scopes:
        for v in phi:
            working.setdefault(v, []).append(list(phi))
    eliminated = set()
    for var in elimination_order:
        if consider_once:
            factors = [f for f in working.get(var, []) if not set(f) & eliminated]
        else:
            factors = list(working.get(var, []))
        phi = sorted(set().union(*factors) - {var}) if factors else []
        cliques.add(tuple(phi))
        if var in working:
            del working[var]
        for v in phi:
            working.setdefault(v, []).append(list(phi))
        eliminated.add(var)
    # 团集合 → 图 → 最大团
    nodes = sorted({v for c in cliques for v in c})
    g = UndirectedGraph(nodes, [])
    for c in cliques:
        for u, v in combinations(c, 2):
            g.add_edge(u, v)
    best = 0
    for c in maximal_cliques(g):
        best = max(best, len(c))
    return best - 1


def elimination_fill_count(graph, order):
    """给定完整消元顺序，统计新增的 fill-in 边总数（= 三角化后的额外边）。"""
    g = graph.copy()
    total = 0
    for node in order:
        add = [
            (u, v)
            for u, v in combinations(g.neighbors(node), 2)
            if not g.has_edge(u, v)
        ]
        total += len(add)
        g.add_edges_from(add)
        g.remove_node(node)
    return total


def is_chordal(g):
    """networkx is_chordal 的等价判据：最大势搜索(MCS)下每个前缀邻居集都是团。"""
    if not g.nodes():
        return True
    numbered = set()
    unnumbered = set(g.nodes())
    while unnumbered:
        v = max(unnumbered, key=lambda n: len(g._adj[n] & numbered))
        unnumbered.discard(v)
        cand = set(g._adj[v]) & numbered
        for a, b in combinations(cand, 2):
            if not g.has_edge(a, b):
                return False
        numbered.add(v)
    return True
