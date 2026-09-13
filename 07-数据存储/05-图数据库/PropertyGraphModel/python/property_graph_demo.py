"""属性图模型(Neo4j property graph)最小可读实现 —— 纯标准库。

权威来源：
  - Neo4j Getting Started "Graph database concepts"
    https://neo4j.com/docs/getting-started/current/graphdb-concepts/
  - Neo4j Developer Guides "What is a Graph Database"
    https://neo4j.com/developer/graph-database/

属性图模型 = 节点(Node) + 关系(Relationship) + 标签(Label) + 属性(Property)。
节点描述实体，可以有零或多个 label 来分类，零或多个属性(key-value)。
关系连接两个节点，必须有方向、必须恰好一个 type、可有零或多个属性；
方向在查询时可被忽略（"Relationships always have a direction.
However, the direction can be disregarded where it is not useful"）。
本 demo 用邻接表(adjacency list)+ 散列表(dict)实现：
  - 节点：id + labels:set[str] + props:dict
  - 关系：id + type + src_id + dst_id + props + 双向链表 next_out/prev_out/next_in/prev_in
展示「关系类型按 src 双向链表」遍历（对应 Neo4j 的"按类型获取节点的出边"）。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


# 关系记录：id + 双向链表指针
@dataclass
class Relationship:
    rid: int
    type: str
    src_id: int
    dst_id: int
    props: dict = field(default_factory=dict)
    # 起始节点上的双向链表（outgoing）
    next_out: Optional[int] = None   # 同 src 下同 type 的下一条 rid
    prev_out: Optional[int] = None
    # 终止节点上的双向链表（incoming）
    next_in: Optional[int] = None
    prev_in: Optional[int] = None


@dataclass
class Node:
    nid: int
    labels: set[str] = field(default_factory=set)
    props: dict = field(default_factory=dict)
    # 每个 label + direction 上的第一条关系 rid（head of chain）
    first_rel_out: dict[str, int] = field(default_factory=dict)
    first_rel_in: dict[str, int] = field(default_factory=dict)


class PropertyGraph:
    """属性图：节点 + 关系 + 按 (type, direction) 分桶的双向链表。"""

    def __init__(self) -> None:
        self.nodes: dict[int, Node] = {}
        self.rels: dict[int, Relationship] = {}
        self._next_nid = 1
        self._next_rid = 1

    # ──────────── CRUD ────────────
    def create_node(self, labels=(), **props) -> int:
        nid = self._next_nid
        self._next_nid += 1
        n = Node(nid=nid, labels=set(labels), props=dict(props))
        self.nodes[nid] = n
        return nid

    def create_rel(self, src_id: int, dst_id: int, type_: str, **props) -> int:
        """创建关系，同时把 r 挂到 src 的 (type, OUT) 与 dst 的 (type, IN) 双向链表头。"""
        rid = self._next_rid
        self._next_rid += 1
        src = self.nodes[src_id]
        dst = self.nodes[dst_id]
        # 取出 src 当前该 type 的首条
        old_head_out = src.first_rel_out.get(type_)
        old_head_in = dst.first_rel_in.get(type_)
        r = Relationship(
            rid=rid, type=type_, src_id=src_id, dst_id=dst_id, props=dict(props),
            next_out=old_head_out, prev_out=None,
            next_in=old_head_in, prev_in=None,
        )
        self.rels[rid] = r
        # 更新旧头节点的 prev 指针
        if old_head_out is not None:
            self.rels[old_head_out].prev_out = rid
        if old_head_in is not None:
            self.rels[old_head_in].prev_in = rid
        src.first_rel_out[type_] = rid
        dst.first_rel_in[type_] = rid
        return rid

    # ──────────── 查询 ────────────
    def neighbors_out(self, nid: int, type_: Optional[str] = None) -> list[tuple[int, int]]:
        """出邻居列表 [(dst_id, rid), ...]，遍历 src 的双向链表。type_=None 则遍历所有类型。"""
        n = self.nodes[nid]
        results = []
        if type_ is not None:
            types = [type_]
        else:
            types = list(n.first_rel_out.keys())
        for t in types:
            cur = n.first_rel_out.get(t)
            while cur is not None:
                r = self.rels[cur]
                results.append((r.dst_id, r.rid))
                cur = r.next_out
        return results

    def find_by_label(self, label: str) -> list[int]:
        return [n.nid for n in self.nodes.values() if label in n.labels]

    # ──────────── 演示：电影数据模型 ────────────
    def demo_movie_graph(self) -> None:
        # Tom Hanks -> Forrest Gump -> ...
        tom = self.create_node(labels={"Person"}, name="Tom Hanks", born=1956)
        forest = self.create_node(labels={"Movie"}, title="Forrest Gump", released=1994)
        forrest_role = ["Forrest"]
        self.create_rel(tom, forest, "ACTED_IN", roles=forrest_role)

        sally = self.create_node(labels={"Person"}, name="Sally Field", born=1946)
        self.create_rel(sally, forest, "ACTED_IN", roles=["Mrs. Gump"])

        apollo = self.create_node(labels={"Movie"}, title="Apollo 13", released=1995)
        self.create_rel(tom, apollo, "ACTED_IN", roles=["Jim Lovell"])

        # 查询 1：Tom Hanks 演过的所有电影（按类型 ACTED_IN 遍历出向链表）
        print("Tom Hanks 的电影:", [
            self.nodes[d].props["title"]
            for d, _ in self.neighbors_out(tom, "ACTED_IN")
        ])
        # 查询 2：所有 Person 节点
        print("所有 Person:", [
            self.nodes[nid].props["name"] for nid in self.find_by_label("Person")
        ])
        # 查询 3：Forrest Gump 的演员
        print("Forrest Gump 演员:", [
            self.nodes[s].props["name"] for s, _ in self.neighbors_in_iter(forest, "ACTED_IN")
        ])

    def neighbors_in_iter(self, nid: int, type_: str) -> list[tuple[int, int]]:
        """入邻居（同上，但走 IN 链表）"""
        n = self.nodes[nid]
        results = []
        cur = n.first_rel_in.get(type_)
        while cur is not None:
            r = self.rels[cur]
            results.append((r.src_id, r.rid))
            cur = r.next_in
        return results


if __name__ == "__main__":
    g = PropertyGraph()
    g.demo_movie_graph()
    # 验证双向链表完整性
    tom_node = g.nodes[1]
    print("Tom 节点 ACTED_IN 出链头 rid =", tom_node.first_rel_out.get("ACTED_IN"))
    # 打印所有关系记录的 prev/next 字段，确认是双向链表
    for rid, r in g.rels.items():
        print(f"rid={rid} type={r.type} {r.src_id}->{r.dst_id} "
              f"next_out={r.next_out} prev_out={r.prev_out}")