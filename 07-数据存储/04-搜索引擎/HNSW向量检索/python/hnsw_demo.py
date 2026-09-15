#!/usr/bin/env python3
r"""HNSW (Hierarchical Navigable Small World) 向量近似近邻搜索演示 —— 纯标准库。

权威来源：Malkov & Yashunin, *Efficient and robust approximate nearest neighbor
search using Hierarchical Navigable Small World graphs*, arXiv:1603.09320v4 (2018)

核心思想是"多层级邻近图"：

    +-------------------+     高层（层 3）：稀疏、长程"高速公路"，快速跨区域跳跃
    |   L3  ● ─ ●      |
    |         \        |
    +------------------+
            ▼          ◀ 用 ef=1 简单贪心从 entry 走到局部最优
    +-------------------+     中层（层 2）：中等密度
    |    ●──●         |
    |    │  \         |
    |    ●──●         |
    +------------------+
            ▼
    +-------------------+     底层（层 1）：最密，全部节点都在
    |   ●──●──●──●     |
    |   │  │  │  │     |
    |   ●──●──●──●     |
    +------------------+

搜索从最高层 entry 开始，贪心向下传递（每次在当前层找到 ef 个最近，进入下一层后
其邻域内继续），最后在层 1 用 ef（候选队列上限）跑完，取 top-k。

本 demo 用一个 2D 点集（方便人在脑中可视化邻接关系）演示：
  1. 插入 20 个随机 2D 点，逐个按指数衰减概率分配最高层；
  2. 用纯贪心由顶向下找出 q 的 top-3 最近邻；
  3. 对比 brute-force 暴力搜索作为 baseline，演示 recall@3（HNSW 找回率）。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# ──────────────── 默认超参（与 HNSW paper 一致的小值） ────────────────
M = 4                          # 每节点（除层 0）最多保留 M 条边
M0 = 2 * M                     # 层 0 用 2M 边（层 0 通常更稠密以保证 recall）
EF_CONSTRUCTION = 8            # 插入时候选队列上限（越大 recall 越好但越慢）
EF_SEARCH = 16                 # 搜索时候选队列上限（影响 recall/qps trade-off）
ML = 1.0 / math.log(M)         # 层数指数衰减因子 = 1/log(M)


# ────────────── 节点 / 图结构 ──────────────
@dataclass
class Node:
    """一个向量节点：含 vector、各层邻居、顶层编号。"""

    vid: int                          # 节点全局 id
    vec: tuple[float, ...]            # 向量值（demo 用 2D）
    level: int                        # 该节点出现的最高层（0..level）
    neighbors: list[list[int]] = field(default_factory=list)
    # neighbors[l] = 第 l 层的邻居 id 列表（无序、无自环）


def assign_level(rng: random.Random) -> int:
    """按 paper 算法 1：l = floor(−log(uniform()) · mL)

    用 uniform ∈ (0, 1]，所以 −log ∈ [0, ∞)；大多数节点 l = 0，
    偶尔拿到 l = 1, 2, ...，层数越高节点越稀疏（"高速公路"）。
    """
    return int(math.floor(-math.log(rng.random()) * ML))


def euclid(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ────────────── search_layer / k-NN ──────────────
def search_layer(
    q: tuple[float, ...],
    entry_points: list[int],
    ef: int,
    lc: int,
    nodes: dict[int, Node],
) -> list[int]:
    """Paper Algorithm 2: 在指定层 lc 上从 entry_points 出发，找出 ef 个最近邻。

    维护两个集合：
      C  = candidates（按到 q 的距离升序的 min-heap，待扩展邻居）
      W  = dynamic nearest list（按到 q 的距离降序的 max-heap，已经发现的最近邻）

    while C 非空：
      取 C 中最近的 c
      若 c 距离 > W 中最远者 → 提前 break（再走也只可能更差）
      否则扩展 c 的层 lc 邻居，对每个新邻居 e：
        若 e 未访问过：
          若 e 比 W 中最远者更近 ∨ W 还没满 ef 个 → 加入 C 和 W
        若 W 满了 ef → 移除最远者
    """
    visited: set[int] = set()
    W: list[tuple[float, int]] = []     # (距离, id)，按距离降序
    for ep in entry_points:
        visited.add(ep)
        W.append((euclid(q, nodes[ep].vec), ep))
    W.sort(reverse=True)                # 按距离降序，W[0] = 最远
    C: list[tuple[float, int]] = sorted((d, i) for d, i in W)  # min-heap 替代

    while C:
        d_c, c_id = C.pop(0)           # 取出当前最近的候选
        d_farthest = W[0][0]
        if d_c > d_farthest and len(W) >= ef:
            break                       # 再扩展也不可能更好
        c_node = nodes[c_id]
        for e in c_node.neighbors[lc]:
            if e in visited:
                continue
            visited.add(e)
            d_e = euclid(q, nodes[e].vec)
            d_cur_farthest = W[0][0]
            if d_e < d_cur_farthest or len(W) < ef:
                C.append((d_e, e))
                W.append((d_e, e))
                W.sort(reverse=True)
                C.sort()                # 这里用排序代替真 heap，仅为可读
                if len(W) > ef:
                    W.pop(0)            # 丢弃最远
    return [nid for _, nid in W]


def knn_search(
    q: tuple[float, ...], k: int, nodes: dict[int, Node], entry: int,
) -> list[int]:
    """Paper Algorithm 5: K-NN-SEARCH(hnsw, q, K, ef)"""
    ep = entry
    target_level = nodes[entry].level
    W: list[int] = []

    # 自顶向下，每层 ef=1（贪心）算 transition
    for lc in range(target_level, 0, -1):
        W = search_layer(q, [ep], 1, lc, nodes)
        if W:
            ep = W[0]
            W = []

    # 层 0 用完整 ef 跑
    W = search_layer(q, [ep], EF_SEARCH, 0, nodes)
    # 返回按距离升序的前 k 个
    return [nid for _, nid in sorted(W)[:k]]


# ────────────── 插入 ──────────────
def insert(
    vid: int,
    vec: tuple[float, ...],
    rng: random.Random,
    nodes: dict[int, Node],
    entry: int | None,
) -> tuple[Node, int]:
    """Paper Algorithm 1（精简）：
      1. 分配层 l
      2. 在 l+1 .. top_level 各层用 ef=1 贪心找 ep（每层只取最近一个）
      3. 在 0 .. l 各层用 efConstruction 找邻居集，与本节点双向连边
    """
    level = assign_level(rng)
    node = Node(vid=vid, vec=vec, level=level,
                neighbors=[[] for _ in range(level + 1)])
    nodes[vid] = node

    if entry is None:
        return node, vid                # 第一个节点：自己就是 entry

    top = nodes[entry].level
    ep = entry
    # phase 1：自顶向下找 entry point（每层 ef=1）
    for lc in range(top, level, -1):
        W = search_layer(vec, [ep], 1, lc, nodes)
        if W:
            ep = W[0]

    # phase 2：层 0 .. level 找邻居集，双向连边
    for lc in range(level, -1, -1):
        ef = EF_CONSTRUCTION
        W = search_layer(vec, [ep], ef, lc, nodes)
        # M 个最近邻（层 0 用 M0）
        m = M0 if lc == 0 else M
        picked = [nid for _, nid in sorted(W)[:m]]
        node.neighbors[lc] = picked
        for neighbor_id in picked:
            neighbor = nodes[neighbor_id]
            neighbor.neighbors[lc].append(vid)
            # 限制邻居数量（层 0 用 M0，其他层用 M），超过则裁剪
            cap = M0 if lc == 0 else M
            if len(neighbor.neighbors[lc]) > cap:
                # 保留到 q 距离最近的 cap 个（paper heuristic: simple）
                neighbor.neighbors[lc] = sorted(
                    neighbor.neighbors[lc],
                    key=lambda x: euclid(vec, nodes[x].vec) if x != vid
                    else euclid(neighbor.vec, vec),
                )[:cap]

    # 新节点的最高层 > 既有最高层时，更新 entry
    new_entry = entry if level <= top else vid
    return node, new_entry


# ────────────── baseline + 主流程 ──────────────
def brute_force(q: tuple[float, ...], k: int, nodes: dict[int, Node]) -> list[int]:
    """暴力对比：扫全部节点按欧氏距离排序，取 top-k。"""
    dists = [(euclid(q, n.vec), nid) for nid, n in nodes.items()]
    dists.sort()
    return [nid for _, nid in dists[:k]]


def recall_at_k(predicted: list[int], truth: list[int]) -> float:
    return len(set(predicted) & set(truth)) / len(truth)


def main() -> None:
    rng = random.Random(42)
    nodes: dict[int, Node] = {}
    entry: int | None = None

    # 在 2D 平面上构造 20 个向量（人脑可想象的"邻域"）
    POINTS = [
        (0.1, 0.1), (0.2, 0.4), (0.3, 0.0), (0.4, 0.3),
        (0.5, 0.5), (0.6, 0.2), (0.7, 0.4), (0.8, 0.1),
        (0.9, 0.5), (1.0, 0.3), (0.4, 0.7), (0.5, 0.9),
        (0.6, 0.8), (0.8, 0.7), (0.9, 0.8), (1.0, 0.9),
        (0.3, 0.6), (0.2, 0.8), (0.1, 0.5), (0.0, 0.3),
    ]
    print("Demo 1 · 逐点插入 + 分配层")
    print(f"  M={M}  M0={M0}  efConstruction={EF_CONSTRUCTION}  efSearch={EF_SEARCH}  mL={ML:.3f}")
    print(f"  {'id':>3s} {'vec':>14s}  {'level':>5s}")
    for vid, vec in enumerate(POINTS):
        _, entry = insert(vid, vec, rng, nodes, entry)
        n = nodes[vid]
        print(f"  {vid:>3d} {str(vec):>14s}  {n.level:>5d}  neighbors=["
              f"{', '.join(str(x) for x in n.neighbors[n.level])}]"
              f" @ L{n.level}")

    # ---- 演示搜索 ----
    print("\nDemo 2 · K-NN search vs brute force (k=3)")
    QUERIES = [(0.45, 0.45), (0.0, 0.0), (0.95, 0.85), (0.15, 0.65)]
    total_recall = 0.0
    for qid, q in enumerate(QUERIES):
        pred = knn_search(q, 3, nodes, entry)
        truth = brute_force(q, 3, nodes)
        rec = recall_at_k(pred, truth)
        total_recall += rec
        print(f"\n  q[{qid}] = {q}  (top entry node id = {entry})")
        print(f"    暴力真实 top-3: {[(tid, round(euclid(q, nodes[tid].vec), 3)) for tid in truth]}")
        print(f"    HNSW  找到 top-3: {[(pid, round(euclid(q, nodes[pid].vec), 3)) for pid in pred]}")
        print(f"    recall@3 = {rec:.2f}")

    print(f"\n  平均 recall@3 = {total_recall / len(QUERIES):.3f}")

    # ---- 演示高层稀疏性（"高速公路") ----
    print("\nDemo 3 · 层级分布（高层稀疏 ⇒ 长程跳转）")
    counts: dict[int, int] = {}
    for n in nodes.values():
        counts[n.level] = counts.get(n.level, 0) + 1
    for lvl in sorted(counts):
        bar = "█" * counts[lvl]
        print(f"  L{lvl}: {counts[lvl]:>3d} node(s) {bar}")
    print(f"  (节点数随层数指数衰减 ⇒ 高层如同'高速公路')")


if __name__ == "__main__":
    main()
