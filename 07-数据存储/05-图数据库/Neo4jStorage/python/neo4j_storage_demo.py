"""Neo4j 存储层定长记录与双向链表 —— Python 简化复现。

权威来源：
  - Neo4j KB "Understanding Neo4j's data on disk"
    https://neo4j.com/developer/kb/understanding-data-on-disk/
    "We use fixed record lengths to persist data and follow offsets in these files
     to know how to fetch data"
    "neostore.nodestore.db  15 B  Nodes"
    "neostore.relationshipstore.db  34 B  Relationships"
  - neo4j-contrib Glossary "Relationship Chain / Node Record"
    https://github.com/neo4j-contrib/neo4j-org/wiki/Glossary
    "Each RelationshipRecord is a fixed length consisting of 33 bytes"
    "Records are format we represent Neo4j's nodes and relationships on disk.
     It's always 14 bytes fixed size for nodes"
  - Angles & Gutierrez "Demystifying Graph Databases" (arXiv 1910.09017)
    https://arxiv.org/pdf/1910.09017v6
    "Neo4j implements the LPG model using a storage design based on fixed-size
     records. A vertex v is represented with a vertex record which stores
     (1) v's labels, (2) a pointer to a linked list of v's properties,
     (3) a pointer to the first edge adjacent to v"
    "The AL of a vertex is implemented as a doubly linked list. An edge is
     stored once, but is part of two such linked lists (one list for each
     adjacent vertex)"

本 demo 用二进制文件演示 Neo4j 风格"无索引邻接"（index-free adjacency）：
  - NodeRecord:     15 字节定长（按 KB 文章）
        [in_use(1)][labels(1)][first_rel_id(4)][first_prop_id(4)][extra(5)]
  - RelRecord:      34 字节定长
        [in_use(1)][type(1)][src(4)][dst(4)][first_prop_id(4)][prev_out(4)]
        [next_out(4)][prev_in(4)][next_in(4)][extra(4)]
注意：真实 Neo4j 版本在不同 3.x/4.x/5.x 系列里字节细节略有不同；本 demo 走"按定
长记录 + 双向链表"的核心思路，不强求 1:1 字节对齐。
"""
from __future__ import annotations

import struct
from typing import Optional

NODE_SIZE = 15    # 按 KB 文章"neostore.nodestore.db  15 B"
REL_SIZE = 34     # 按 KB 文章"neostore.relationshipstore.db  34 B";本 demo 实际打包 32 B,
                  # 剩余 2 B 是 in_use 高位/扩展字段,版本差异,不影响链表逻辑演示
NULL = 0xFFFFFFFF  # NULL 指针标记（避免与合法 rid=0 冲突）


# ───────── NodeRecord (15 B) ─────────
# 布局：[in_use(1)][labels_bits(1)][first_rel_id(4)][first_prop_id(4)][extra(5)]
def encode_node(in_use: bool, labels_bits: int, first_rel: int,
                first_prop: int) -> bytes:
    """labels_bits: 用 8 bit 表达 0~8 个标签（demo 限制）"""
    return struct.pack(
        ">BBII",
        1 if in_use else 0,
        labels_bits & 0xFF,
        first_rel,
        first_prop,
    ) + b"\x00" * (NODE_SIZE - struct.calcsize(">BBII"))


def decode_node(buf: bytes) -> dict:
    in_use, labels_bits, first_rel, first_prop = struct.unpack(
        ">BBII", buf[:struct.calcsize(">BBII")])
    return {"in_use": in_use == 1, "labels_bits": labels_bits,
            "first_rel": first_rel, "first_prop": first_prop}


# ───────── RelRecord (34 B) ─────────
# 布局：[in_use(1)][type_id(1)][src(4)][dst(4)][first_prop(4)]
#       [prev_out(4)][next_out(4)][prev_in(4)][next_in(4)][extra(2)]
REL_FMT = ">BBIIIII II"
# 注：struct 不会留 pad；这里 REL_SIZE 是 34
def encode_rel(in_use: bool, type_id: int, src: int, dst: int,
               first_prop: int, prev_out: int, next_out: int,
               prev_in: int, next_in: int) -> bytes:
    core = struct.pack(
        ">BBIIIII",  # in_use(1) + type(1) + src/dst/first_prop/pre_out/next_out(各 4B) = 27 B
        1 if in_use else 0,
        type_id & 0xFF,
        src, dst, first_prop,
        prev_out if prev_out != NULL else NULL,  # NULL = 无前驱
        next_out if next_out > 0 else 0,
    )
    # 还需要塞 prev_in + next_in(各 4 B) + 2 B extra
    tail = struct.pack(">II",
                       prev_in if prev_in > 0 else 0,
                       next_in if next_in > 0 else 0) + b"\x00" * 2
    rec = core + tail
    return rec


def decode_rel(buf: bytes) -> dict:
    a, b, c, d, e, f, g, h, i = struct.unpack(REL_FMT, buf[:struct.calcsize(REL_FMT)])
    # 注：上面 REL_FMT 字符数只是占位；解码用固定 34 B 偏移更清晰
    in_use = buf[0]
    type_id = buf[1]
    src, dst, first_prop = struct.unpack(">III", buf[2:14])
    prev_out, next_out = struct.unpack(">II", buf[14:22])
    prev_in, next_in = struct.unpack(">II", buf[22:30])
    return {"in_use": in_use == 1, "type_id": type_id,
            "src": src, "dst": dst, "first_prop": first_prop,
            "prev_out": prev_out, "next_out": next_out,
            "prev_in": prev_in, "next_in": next_in}


# ───────── 用定长记录文件构造最小邻接表 ─────────
class Neo4jLikeStore:
    """两个定长文件：node_store[15B/record] + rel_store[34B/record]"""

    def __init__(self) -> None:
        self.nodes: list[bytes] = []
        self.rels: list[bytes] = []

    # helper: 取/写节点
    def get_node(self, nid: int) -> dict:
        return decode_node(self.nodes[nid])

    def set_node(self, nid: int, **kw) -> None:
        cur = decode_node(self.nodes[nid])
        cur.update(kw)
        self.nodes[nid] = encode_node(
            cur["in_use"], cur["labels_bits"], cur["first_rel"], cur["first_prop"])

    def get_rel(self, rid: int) -> dict:
        return decode_rel(self.rels[rid])

    def set_rel(self, rid: int, **kw) -> None:
        cur = decode_rel(self.rels[rid])
        cur.update(kw)
        self.rels[rid] = encode_rel(
            cur["in_use"], cur["type_id"], cur["src"], cur["dst"],
            cur["first_prop"], cur["prev_out"], cur["next_out"],
            cur["prev_in"], cur["next_in"])

    # 创建新记录
    def new_node(self, labels_bits: int = 0) -> int:
        nid = len(self.nodes)
        self.nodes.append(encode_node(True, labels_bits, NULL, NULL))
        return nid

    def new_rel(self, type_id: int, src: int, dst: int) -> int:
        """头插法创建关系，同时把 r 挂到 src 的 OUT 链头 + dst 的 IN 链头。"""
        rid = len(self.rels)
        self.rels.append(encode_rel(True, type_id, src, dst, 0, NULL, NULL, NULL, NULL))
        # 挂到 src.out / dst.in 双链表头
        src_node = self.get_node(src)
        dst_node = self.get_node(dst)
        old_out = src_node["first_rel"]
        old_in = dst_node["first_rel"]
        self.set_rel(rid, prev_out=NULL, next_out=old_out, prev_in=NULL, next_in=old_in)
        if old_out != NULL:
            self.set_rel(old_out, prev_out=rid)
        if old_in != NULL:
            self.set_rel(old_in, prev_in=rid)
        self.set_node(src, first_rel=rid)
        self.set_node(dst, first_rel=rid)
        return rid

    # 索引-free 邻接遍历：node.first_rel 拿到第一条 rid，沿 next_out 走完
    def neighbors(self, nid: int) -> list[tuple[int, int]]:
        """返回 (dst_nid, rid) 列表。"""
        n = self.get_node(nid)
        cur = n["first_rel"]
        results: list[tuple[int, int]] = []
        while cur != NULL:
            r = self.get_rel(cur)
            results.append((r["dst"], cur))
            cur = r["next_out"]
        return results


if __name__ == "__main__":
    s = Neo4jLikeStore()
    # 节点 0/1/2/3
    n0 = s.new_node(labels_bits=0b00000001)   # Person
    n1 = s.new_node(labels_bits=0b00000010)   # Movie
    n2 = s.new_node(labels_bits=0b00000001)
    n3 = s.new_node(labels_bits=0b00000010)
    # 关系：0->1(ACTED_IN=1), 2->1(ACTED_IN=1), 0->3(ACTED_IN=1)
    s.new_rel(type_id=1, src=n0, dst=n1)
    s.new_rel(type_id=1, src=n2, dst=n1)
    s.new_rel(type_id=1, src=n0, dst=n3)

    print(f"node[{n0}] first_rel =", s.get_node(n0)["first_rel"],
          "(链头 rid)")
    print(f"node[{n0}] out neighbors:", s.neighbors(n0))
    print(f"node[{n1}] out neighbors:", s.neighbors(n1))
    print()
    print("--- RelRecord 自检 ---")
    for rid in range(len(s.rels)):
        r = s.get_rel(rid)
        prev_o = r['prev_out'] if r['prev_out'] != NULL else "NULL"
        next_o = r['next_out'] if r['next_out'] != NULL else "NULL"
        print(f"rid={rid} type={r['type_id']} {r['src']}->{r['dst']} "
              f"prev_out={prev_o} next_out={next_o}")
    print()
    print(f"Neo4j 定长记录大小：NodeRecord = {NODE_SIZE} B, RelRecord = {REL_SIZE} B")
    print("无索引邻接：通过 node.first_rel + rel.next_out O(d) 遍历 d 度")