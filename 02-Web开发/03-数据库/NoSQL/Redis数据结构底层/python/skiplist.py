#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Redis 有序集合的跳表（zskiplist）复刻：层数几何分布 + span/rank 记账。

口径来源（先联网实读再写）：Redis 源码 unstable 分支 src/t_zset.c 与 src/server.h
（raw.githubusercontent.com/redis/redis/unstable/src/{t_zset.c,server.h}，经 WebFetch 实读）：
  - 头注释：zset 同时用两张结构 —— "The elements are added to a hash table mapping Redis objects
    to scores. At the same time the elements are added to a skip list mapping scores to Redis
    objects"；SDS 字符串在两者间共享，只在 zslFreeNode() 里释放；
  - 头注释说明它是对 William Pugh "Skip Lists: A Probabilistic Alternative to Balanced Trees"
    的三处修改：a) 允许重复 score；b) 比较键是 (score, satellite data)；c) level 0 带 back 指针，
    因此"it's a doubly linked list with the back pointers being only at level 1"（利于 ZREVRANGE）；
  - src/server.h：`#define ZSKIPLIST_MAXLEVEL 32 /* Should be enough for 2^64 elements */`、
    `#define ZSKIPLIST_P 0.25 /* Skiplist P = 1/4 */`；
  - `zslRandomLevel()`：`level = 1; while (random() < threshold) level += 1;`
    返回 min(level, ZSKIPLIST_MAXLEVEL)，"with a powerlaw-alike distribution where higher levels
    are less likely to be returned"；
  - `zslInsertNode()` 用两个定长数组：`update[ZSKIPLIST_MAXLEVEL]`（每层前驱）与
    `rank[ZSKIPLIST_MAXLEVEL]`（0-based 排名），span 记账公式见下；
  - `zslGetRank()`：1-based rank，"due to the span of zsl->header to the first element"；
  - `zslUnlinkNode()`：逐层 `incr/decr span`，并在顶层为空时把 `zsl->level` 降下来。
  - 备注：unstable 分支把节点信息（levels / sdsoffset）压在 level[0].span 里、ele 内嵌在 level[]
    之后（`zslGetNodeInfo()`）；本 demo 用经典布局（node.ele + node.level[i].span）以便阅读，
    rank 语义与源码一致。

运行：python3 skiplist.py —— 打印演示输出；断言见 checks.py
"""
from __future__ import annotations

import random

ZSKIPLIST_MAXLEVEL = 32   # src/server.h
ZSKIPLIST_P = 0.25        # src/server.h


def zsl_random_level(rnd: random.Random) -> int:
    """对应 zslRandomLevel()：几何分布，期望 1/(1-P) = 1.333 层。"""
    level = 1
    while rnd.random() < ZSKIPLIST_P:
        level += 1
    return min(level, ZSKIPLIST_MAXLEVEL)


class Node:
    __slots__ = ("score", "ele", "backward", "forward", "span")

    def __init__(self, score: float, ele: str, level: int):
        self.score = score
        self.ele = ele
        self.backward: Node | None = None
        self.forward: list[Node | None] = [None] * level   # forward[i]：第 i 层后继
        self.span: list[int] = [0] * level                 # span[i]：到 forward[i] 跨过的元素数

    @property
    def level(self) -> int:
        return len(self.forward)


def zsl_compare(score: float, ele: str, node: Node) -> int:
    """对应 zslCompareWithNode()：先比 score，再比 ele（允许重复 score）。"""
    if score != node.score:
        return -1 if score < node.score else 1
    if ele == node.ele:
        return 0
    return -1 if ele < node.ele else 1


class SkipList:
    """最小跳表：表头是 level 层的哨兵，span 用于按排名定位（ZRANGE / ZRANK）。"""

    def __init__(self, rnd: random.Random | None = None):
        self.rnd = rnd or random.Random(20260918)
        self.header = Node(float("-inf"), "", ZSKIPLIST_MAXLEVEL)
        self.tail: Node | None = None
        self.length = 0
        self.level = 1

    # -- 插入：与 zslInsertNode() 的同构实现 ------------------------------
    def insert(self, score: float, ele: str) -> Node:
        update = [self.header] * ZSKIPLIST_MAXLEVEL
        rank = [0] * ZSKIPLIST_MAXLEVEL
        x = self.header
        for i in range(self.level - 1, -1, -1):
            rank[i] = 0 if i == self.level - 1 else rank[i + 1]
            while x.forward[i] is not None and zsl_compare(score, ele, x.forward[i]) > 0:
                rank[i] += x.span[i]
                x = x.forward[i]
            update[i] = x
        level = zsl_random_level(self.rnd)
        if level > self.level:
            for i in range(self.level, level):
                rank[i] = 0
                update[i] = self.header
                update[i].span[i] = self.length
            self.level = level
        node = Node(score, ele, level)
        for i in range(level):
            node.forward[i] = update[i].forward[i]
            update[i].forward[i] = node
            node.span[i] = update[i].span[i] - (rank[0] - rank[i])
            update[i].span[i] = (rank[0] - rank[i]) + 1
        for i in range(level, self.level):
            update[i].span[i] += 1
        node.backward = None if update[0] is self.header else update[0]
        if node.forward[0] is not None:
            node.forward[0].backward = node
        else:
            self.tail = node
        self.length += 1
        return node

    # -- 按 (score, ele) 定位：zslGetRank() -------------------------------
    def get_rank(self, score: float, ele: str) -> int:
        x, rank = self.header, 0
        for i in range(self.level - 1, -1, -1):
            while x.forward[i] is not None and zsl_compare(score, ele, x.forward[i]) >= 0:
                rank += x.span[i]
                x = x.forward[i]
            if x is not self.header and zsl_compare(score, ele, x) == 0:
                return rank
        return 0

    # -- 按 1-based 排名取元素：zslGetElementByRank() ---------------------
    def get_element_by_rank(self, rank: int) -> Node | None:
        x, traversed = self.header, 0
        for i in range(self.level - 1, -1, -1):
            while x.forward[i] is not None and traversed + x.span[i] <= rank:
                traversed += x.span[i]
                x = x.forward[i]
            if traversed == rank:
                return x
        return None

    # -- 删除：zslUnlinkNode() -------------------------------------------
    def delete(self, score: float, ele: str) -> bool:
        update = [self.header] * ZSKIPLIST_MAXLEVEL
        x = self.header
        for i in range(self.level - 1, -1, -1):
            while x.forward[i] is not None and zsl_compare(score, ele, x.forward[i]) > 0:
                x = x.forward[i]
            update[i] = x
        target = x.forward[0]
        if target is None or target.score != score or target.ele != ele:
            return False
        for i in range(self.level):
            if update[i].forward[i] is target:
                update[i].span[i] += target.span[i] - 1
                update[i].forward[i] = target.forward[i]
            else:
                update[i].span[i] -= 1
        if target.forward[0] is not None:
            target.forward[0].backward = target.backward
        else:
            self.tail = target.backward
        while self.level > 1 and self.header.forward[self.level - 1] is None:
            self.header.span[self.level - 1] = 0
            self.level -= 1
        self.length -= 1
        return True

    # -- 遍历 --------------------------------------------------------------
    def items(self) -> list[tuple[float, str]]:
        out, x = [], self.header.forward[0]
        while x is not None:
            out.append((x.score, x.ele))
            x = x.forward[0]
        return out

    def reverse_items(self) -> list[tuple[float, str]]:
        """level 0 的 backward 指针让 ZREVRANGE 无需从头再走一遍。"""
        out, x = [], self.tail
        while x is not None:
            out.append((x.score, x.ele))
            x = x.backward
        return out

    def pointer_count(self) -> int:
        return sum(n.level for n in self._nodes())

    def _nodes(self) -> list[Node]:
        out, x = [], self.header.forward[0]
        while x is not None:
            out.append(x)
            x = x.forward[0]
        return out


if __name__ == "__main__":
    sl = SkipList()
    for i, (s, e) in enumerate([(1, "a"), (3, "c"), (3, "b"), (2, "b")]):
        sl.insert(s, e)
    print("有序内容:", sl.items())
    print("rank(3,'b') =", sl.get_rank(3, "b"), "第 3 名 =", sl.get_element_by_rank(3).ele)
    sl.delete(3, "b")
    print("删除后:", sl.items(), "反向:", sl.reverse_items())
