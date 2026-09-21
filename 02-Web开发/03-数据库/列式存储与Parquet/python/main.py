"""列式存储与 Parquet 嵌套编码 —— Dremel 记录切分 + def/rep levels + RLE 混合编码。

转写对象（官方规范，逐条对照）：
  * apache/parquet-format 《README.md》—— Nested Encoding / Nulls / Data Pages
    三节：「definition levels 说明路径上有多少个 optional 字段被定义」、
    「repetition levels 说明在路径的哪一级 repeated 字段上发生了重复」、
    「数据页里 rep → def → values 三段背靠背，无填充」
  * apache/parquet-format 《Encodings.md》—— RLE / Bit-Packing Hybrid (RLE = 3)
    的文法、LSB-first 的位打包顺序、ULEB-128 的 run header

口径说明：
  * definition level 计数的是路径上 **optional 与 repeated** 两类节点（repeated
    为空时也算「未定义」），required 节点不计 —— 这与 Dremel 论文 Figure 3 一致。
  * 本模型只做**单列**的切分与装配，不做多列拼装（那是 reader 的 record assembly）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from encoding import (data_page, pack_lsb_first, rle_decode, rle_encode,
                      read_uleb128, uleb128, unpack_lsb_first, bit_width)

REQUIRED, OPTIONAL, REPEATED = "required", "optional", "repeated"


class Node:
    """schema 里的一个节点。children 为空即叶子。"""

    def __init__(self, name: str, rep: str = REQUIRED,
                 children: Optional[Sequence["Node"]] = None) -> None:
        if rep not in (REQUIRED, OPTIONAL, REPEATED):
            raise ValueError("bad repetition: %s" % rep)
        self.name = name
        self.rep = rep
        self.children = list(children or [])

    @property
    def is_leaf(self) -> bool:
        return not self.children

    def find(self, path: Sequence[str]) -> List["Node"]:
        """按列名路径取出节点链。"""
        if not path or path[0] != self.name:
            raise KeyError(path)
        chain = [self]
        cur = self
        for name in path[1:]:
            nxt = None
            for c in cur.children:
                if c.name == name:
                    nxt = c
                    break
            if nxt is None:
                raise KeyError(name)
            chain.append(nxt)
            cur = nxt
        return chain


def document_schema() -> Node:
    """Dremel 论文里的 Document schema（Parquet 官方示例同构）。"""
    return Node("Document", REQUIRED, [
        Node("DocId", REQUIRED),
        Node("Links", OPTIONAL, [Node("Backward", REPEATED), Node("Forward", REPEATED)]),
        Node("Name", REPEATED, [
            Node("Language", REPEATED, [
                Node("Code", REQUIRED),
                Node("Country", OPTIONAL),
            ]),
            Node("Url", OPTIONAL),
        ]),
    ])


# ------------------------------------------------------------ 最大层级
def max_definition_level(path: Sequence[Node]) -> int:
    """路径上 optional 与 repeated 节点的个数（required 不计）。"""
    return sum(1 for n in path if n.rep in (OPTIONAL, REPEATED))


def max_repetition_level(path: Sequence[Node]) -> int:
    """路径上 repeated 节点的个数。"""
    return sum(1 for n in path if n.rep == REPEATED)


def defcounts(path: Sequence[Node]) -> List[int]:
    """defcounts[i] = 到 path[i] 为止累计的 optional/repeated 个数。

    path[i] 被定义  <=>  d >= defcounts[i]
    """
    out, c = [], 0
    for n in path:
        if n.rep in (OPTIONAL, REPEATED):
            c += 1
        out.append(c)
    return out


def repeated_index(path: Sequence[Node]) -> Dict[int, int]:
    """path 下标 -> 它是路径上第几个 repeated 节点（1 起）。"""
    out, k = {}, 0
    for i, n in enumerate(path):
        if n.rep == REPEATED:
            k += 1
            out[i] = k
    return out


# -------------------------------------------------------------- 切分
Entry = Tuple[Any, int, int]      # (value, repetition_level, definition_level)


def shred_column(records: Sequence[Any], path: Sequence[Node]) -> List[Entry]:
    """把若干文档在某一列路径上切成 (value, r, d) 序列。"""
    out: List[Entry] = []
    counts = defcounts(path)
    ridx = repeated_index(path)
    max_def = max_definition_level(path)

    def walk(container: Any, i: int, cur_def: int, cur_rep: int) -> None:
        node = path[i]
        if node.rep == REPEATED:
            items = _get(container, node.name) if isinstance(container, dict) else None
            if not items:
                out.append((None, cur_rep, cur_def))         # 空/缺失 → NULL
                return
            k = ridx[i]
            for j, item in enumerate(items):
                d = cur_def + 1
                r = k if j > 0 else cur_rep
                if node.is_leaf:
                    out.append((item, r, d))
                else:
                    walk(item, i + 1, d, r)
            return
        if node.rep == OPTIONAL:
            v = _get(container, node.name)
            if v is None:
                out.append((None, cur_rep, cur_def))
                return
            d = cur_def + 1
            if node.is_leaf:
                out.append((v, r0(cur_rep), d))
            else:
                walk(v, i + 1, d, cur_rep)
            return
        # required
        v = _get(container, node.name)
        if node.is_leaf:
            out.append((v, cur_rep, cur_def))
        else:
            walk(v, i + 1, cur_def, cur_rep)

    def r0(x: int) -> int:
        return x

    for rec in records:
        walk(rec, 0, 0, 0)
    # required 叶子在「整条路径都必填」时 d 恒为 max_def
    assert all(d <= max_def for _, _, d in out)
    return out


def _get(container: Any, key: str) -> Any:
    if isinstance(container, dict):
        return container.get(key)
    return None


# -------------------------------------------------------------- 装配
def assemble_column(entries: Sequence[Entry], path: Sequence[Node]) -> List[Dict[str, Any]]:
    """把 (value, r, d) 序列还原成每个文档在该路径上的嵌套结构。"""
    counts = defcounts(path)
    docs: List[Dict[str, Any]] = []
    rep_stack: List[Dict[str, Any]] = []

    for value, r, d in entries:
        if r == 0:
            docs.append({})
            rep_stack = []
        else:
            # r = k 表示在第 k 个 repeated 节点上开了新的一次出现，
            # 于是只保留前 k-1 层，更深的层要重新创建
            rep_stack = rep_stack[: r - 1]
        parent: Dict[str, Any] = docs[-1]
        k = 0
        for i, node in enumerate(path):
            if d < counts[i]:
                break                                  # 该层未定义
            if node.rep == REPEATED:
                k += 1
                lst = parent.setdefault(node.name, [])
                if i == len(path) - 1:
                    lst.append(value)
                    break
                if k < r:                              # 该层沿用当前出现
                    occ = lst[-1] if lst else {}
                else:                                  # 第 r 层及更深都开新的一次出现
                    occ = {}
                    lst.append(occ)
                if len(rep_stack) >= k:
                    rep_stack[k - 1] = occ
                else:
                    rep_stack.append(occ)
                parent = occ
            else:
                if i == len(path) - 1:
                    parent[node.name] = value
                    break
                parent = parent.setdefault(node.name, {})
    return docs


