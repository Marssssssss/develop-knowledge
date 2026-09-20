"""Cypher 路径匹配模式（match mode / path mode）最小模型。

依据 Neo4j Cypher Manual（Cypher 25 / current）：
  * ``Paths with unique relationships``——Cypher 默认**不允许同一条关系在同一条 MATCH
    结果里被重复遍历**（无论方向），但**节点可以**被重复遍历；默认行为可用
    ``MATCH DIFFERENT RELATIONSHIPS`` 显式写出（Neo4j 2025.06 起，仅 Cypher 25），
    且**不改变**匹配语义。
  * ``Acyclic paths``——``ACYCLIC`` 关键字进一步禁止**节点**在**同一条路径**内重复
    （跨路径重复仍允许，因此 equijoin 仍成立）；Cypher 同时接受 GQL 路径模式
    ``TRAIL`` 与 ``WALK``，它们分别与 DIFFERENT RELATIONSHIPS / REPEATABLE ELEMENTS
    已施加的约束相同，并不额外改变语义。
  * 由此得到三种可枚举的约束等级：
      WALK      = 无任何唯一性约束（REPEATABLE ELEMENTS）
      TRAIL     = 关系唯一（默认 / DIFFERENT RELATIONSHIPS）
      ACYCLIC   = 关系唯一 + 节点唯一（ACYCLIC）

本模型只做「按模式枚举路径」这一件事，用于量化三种模式在同一模式串上的结果差异。
"""

WALK = "WALK"
TRAIL = "TRAIL"
ACYCLIC = "ACYCLIC"

MODES = (WALK, TRAIL, ACYCLIC)


class Rel:
    __slots__ = ("rid", "rtype", "start", "end", "props")

    def __init__(self, rid, rtype, start, end, **props):
        self.rid = rid
        self.rtype = rtype
        self.start = start
        self.end = end
        self.props = props


class Graph:
    """有向边按创建方向存一份；无向遍历时两端各可进入。"""

    def __init__(self):
        self.nodes = {}
        self.rels = []
        self._out = {}
        self._und = {}

    def add_node(self, name, **props):
        self.nodes[name] = props
        self._out.setdefault(name, [])
        self._und.setdefault(name, [])

    def add_rel(self, rid, rtype, a, b, **props):
        r = Rel(rid, rtype, a, b, **props)
        self.rels.append(r)
        self._out[a].append((r, b))
        self._und[a].append((r, b))
        self._und[b].append((r, a))
        return r

    def edges(self, name, rtype=None, directed=False):
        table = self._out if directed else self._und
        return [(r, n) for (r, n) in table.get(name, []) if rtype is None or r.rtype == rtype]

    def rel_by_id(self, rid):
        for r in self.rels:
            if r.rid == rid:
                return r
        raise KeyError(rid)


def allows(mode, rel_seen, node_seen, rel, node):
    """判定在该模式下，能否把 (rel, node) 追加进当前路径。"""
    if mode == WALK:
        return True
    if mode == ACYCLIC:
        # 节点唯一 ⇒ 关系必然唯一（重复关系必然带回重复节点）
        return rel.rid not in rel_seen and node not in node_seen
    if mode == TRAIL:
        return rel.rid not in rel_seen
    raise ValueError("unknown mode: %r" % (mode,))


def match(graph, start, mode, hops=None, end=None, rtype=None, directed=False,
          max_hops=64):
    """枚举满足模式的路径，返回 [(nodes, rel_ids), ...]。

    - ``hops`` 给定：定长量化 ``{n}``，恰好 n 跳后停止（不再继续扩展）。
    - ``hops`` 为 None 且 ``end`` 给定：变长量化 ``+``，到达 end 即产出并**停止扩展**。
      口径说明：在 ACYCLIC 下这与官方语义**严格等价**——节点唯一使得中途经过 end
      之后不可能再次回到 end；在 TRAIL 下官方示例需要用内联谓词
      ``(l WHERE l.name <> 'Z')`` 排除中途经过终点，本模型直接停在终点，
      等价于把这些内联谓词写进模式。
    """
    if mode not in MODES:
        raise ValueError("unknown mode: %r" % (mode,))
    results = []

    def dfs(cur, rel_seen, node_seen, node_path, rel_path):
        if hops is not None:
            if len(rel_path) == hops:
                if end is None or cur == end:
                    results.append((list(node_path), list(rel_path)))
                return
        elif end is not None and len(rel_path) >= 1 and cur == end:
            results.append((list(node_path), list(rel_path)))
            return
        if len(rel_path) >= max_hops:
            return
        for rel, nxt in graph.edges(cur, rtype, directed):
            if not allows(mode, rel_seen, node_seen, rel, nxt):
                continue
            rel_seen.add(rel.rid)
            node_seen.add(nxt)
            node_path.append(nxt)
            rel_path.append(rel.rid)
            dfs(nxt, rel_seen, node_seen, node_path, rel_path)
            rel_path.pop()
            node_path.pop()
            node_seen.discard(nxt)
            rel_seen.discard(rel.rid)

    node_seen = {start}
    dfs(start, set(), node_seen, [start], [])
    return results


def count(graph, start, mode, **kw):
    return len(match(graph, start, mode, **kw))


def konigsberg():
    """官方 ``Paths with unique relationships`` 页的七桥图。

    定向：kneiphof->northBank(1) kneiphof->southBank(6) kneiphof->lomse(7)
          northBank->kneiphof(5) northBank->lomse(2)
          southBank->kneiphof(4) southBank->lomse(3)
    """
    g = Graph()
    for n in ("Kneiphof", "North Bank", "South Bank", "Lomse"):
        g.add_node(n, label="Location")
    g.add_rel(1, "BRIDGE", "Kneiphof", "North Bank")
    g.add_rel(6, "BRIDGE", "Kneiphof", "South Bank")
    g.add_rel(7, "BRIDGE", "Kneiphof", "Lomse")
    g.add_rel(5, "BRIDGE", "North Bank", "Kneiphof")
    g.add_rel(2, "BRIDGE", "North Bank", "Lomse")
    g.add_rel(4, "BRIDGE", "South Bank", "Kneiphof")
    g.add_rel(3, "BRIDGE", "South Bank", "Lomse")
    return g


def router_network():
    """官方 ``Acyclic paths`` 页的路由器网络（12 节点 / 19 条 LINK）。"""
    g = Graph()
    for n in ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "Z"):
        g.add_node(n, label="Router")
    for a, b in (("A", "B"), ("A", "C"), ("A", "D"), ("C", "B"), ("C", "D"),
                 ("B", "E"), ("C", "E"), ("D", "F"), ("E", "G"), ("F", "G"),
                 ("H", "I"), ("H", "J"), ("I", "J"), ("I", "Z"), ("G", "J"),
                 ("G", "K"), ("J", "K"), ("J", "Z"), ("K", "Z")):
        g.add_rel("%s->%s" % (a, b), "LINK", a, b)
    return g
