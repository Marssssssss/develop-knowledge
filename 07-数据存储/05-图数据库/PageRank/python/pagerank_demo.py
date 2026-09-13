"""PageRank 幂迭代算法 —— 纯标准库。

权威来源：
  - Wikipedia "PageRank"
    https://en.wikipedia.org/wiki/PageRank
    "the PageRank value for any page u can be expressed as:
        PR(u) = sum_{v in B_u} PR(v) / L(v)"
  - Gleif/Callut et al. SIAM Review "An Inner-Outer Iteration for Computing PageRank"
    https://www.cs.ubc.ca/~greif/Publications/gggl2010.pdf
    "alpha = 0.85 is the damping factor; power method linear convergence rate alpha"

公式 (Wikipedia 形式)：
  PR(u) = (1-d)/N + d * sum_{v→u} PR(v) / L(v)
其中 d=0.85, L(v) = v 的出度。
收敛判据：max |r_{k+1} - r_k| < tol，或迭代次数到达 max_iter。

实现要点：
  - 阻尼项 (1-d)/N 一次性加到每个节点的累积值上
  - 处理悬挂节点(dangling node)：Wikipedia 原式把所有节点的 PR 平摊过来
  - 幂迭代直至收敛
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

DAMPING = 0.85
TOL = 1e-6
MAX_ITER = 100


def pagerank(edges: List[Tuple[str, str]],
             damping: float = DAMPING,
             tol: float = TOL,
             max_iter: int = MAX_ITER) -> Dict[str, float]:
    """幂迭代求 PageRank。edges = 边列表 [(src, dst), ...]。"""
    # 构建出度
    nodes: set[str] = set()
    out_deg: Dict[str, int] = defaultdict(int)
    for u, v in edges:
        nodes.add(u); nodes.add(v)
        out_deg[u] += 1
    N = len(nodes)
    # 处理孤立节点（出度 0 但不是端点）：用均匀分布避免悬挂节点
    for n in nodes:
        if n not in out_deg:
            out_deg[n] = 0
    # 初始 PR：均匀分布
    pr: Dict[str, float] = {n: 1.0 / N for n in nodes}
    teleport = (1.0 - damping) / N
    for it in range(max_iter):
        new_pr: Dict[str, float] = {n: teleport for n in nodes}
        # 累加 link 贡献
        for u, v in edges:
            if out_deg[u] > 0:
                new_pr[v] += damping * pr[u] / out_deg[u]
        # 悬挂节点贡献：把 dangling 的 PR * damping 平均分给所有人
        dangling_sum = sum(pr[n] for n in nodes if out_deg[n] == 0)
        if dangling_sum > 0:
            share = damping * dangling_sum / N
            for n in nodes:
                new_pr[n] += share
        # 收敛检查
        diff = max(abs(new_pr[n] - pr[n]) for n in nodes)
        pr = new_pr
        if diff < tol:
            print(f"在第 {it + 1} 轮收敛, max|Δ|={diff:.2e}")
            return pr
    print(f"达到 max_iter={max_iter}, max|Δ|={diff:.2e}（未严格收敛）")
    return pr


if __name__ == "__main__":
    # 经典 4 节点示例（Wikipedia "PageRank"）：A 是 hub
    #   B → C, A
    #   C → A
    #   D → A, B, C
    edges = [
        ("B", "A"), ("B", "C"),
        ("C", "A"),
        ("D", "A"), ("D", "B"), ("D", "C"),
    ]
    pr = pagerank(edges)
    # 按 PR 排序输出
    for node, score in sorted(pr.items(), key=lambda x: -x[1]):
        print(f"  {node}: PR = {score:.6f}")
    # 自检：PR 之和应 ≈ 1
    s = sum(pr.values())
    print(f"sum(PR) = {s:.6f}（理论上应等于 1）")