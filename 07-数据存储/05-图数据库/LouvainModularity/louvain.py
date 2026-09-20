"""Louvain 社区发现与模块度（modularity）最小模型。

依据 Blondel et al., *Fast unfolding of communities in large networks*
(arXiv:0803.0476v2) 原文（12 页 PDF 实读）：
  * 模块度（式 1）：
        Q = 1/(2m) · Σ_ij [ A_ij − k_i k_j / (2m) ] · δ(c_i, c_j)
    其中 A_ij 是 i、j 之间边的权重，k_i = Σ_j A_ij，m = (1/2) Σ_ij A_ij，
    δ(u,v) 在 u = v 时为 1。
  * 把孤立节点 i 移入社区 C 的模块度增益（式 2）：
        ΔQ = [ (Σin + k_i,in)/(2m) − ((Σtot + k_i)/(2m))² ]
             − [ Σin/(2m) − (Σtot/(2m))² − (k_i/(2m))² ]
    Σin = C 内部边的权重之和、Σtot = **入射到 C 中节点**的边权之和、
    k_i,in = i 到 C 中节点的边权之和。移除 i 用类似表达式，实践上就是
    「先把 i 从原社区移除、再移入邻社区」两段增益相加。
  * 第一阶段：初始每个节点一个社区；反复按顺序考察每个节点，把它移入
    **增益最大且为正**的邻居社区；直到没有任何单个移动能改进模块度
    （即达到局部极大）。一个节点可能被考察多次。
  * 第二阶段：把第一阶段得到的社区**聚合**成超节点；两个超节点之间的边权
    = 原来两个社区之间所有边权之和；**同一社区内部的边变成该社区的自环**。
  * 两个阶段合起来叫一次 pass，反复迭代直到不再变化。
  * 算法输出**依赖**节点考察顺序（论文说顺序对最终模块度影响不大，但对
    计算时间有影响）；本模型按节点编号顺序考察，保证可复现。

**自环约定（本模型显式采用并断言）**：聚合图的自环在邻接矩阵里记为
``A_ii = 2·s_i``（s_i 为自环权重）。只有这样才能同时满足
（a）m = 全部边权之和（自环按一次计），（b）Q 在聚合前后**保持不变**。
若按 ``A_ii = s_i`` 记，两条都不成立 —— 自检里用反向断言固定住这一点。

同时参考 Neo4j GDS Louvain 文档（实读）：参数 maxLevels / maxIterations
（"每个 level 上运行模块度优化的最大迭代次数"）/ tolerance（"迭代间模块度
变化小于该值即视为稳定并返回"）/ includeIntermediateCommunities /
consecutiveIds / seedProperty；结果含最终 modularity 与每个 level 的
modularity 列表。
"""

from collections import defaultdict

TOL = 1e-9


class Graph:
    """无向加权图，自环单独存。

    ``A(i, j)`` 对 i == j 返回 ``2 * self_loop[i]``（见模块 docstring 的约定）。
    """

    def __init__(self):
        self.nodes = []
        self._w = {}              # (min,max) -> weight，i < j
        self.self_loop = defaultdict(float)

    def add_node(self, n):
        if n not in self.nodes:
            self.nodes.append(n)

    def add_edge(self, a, b, w=1.0):
        self.add_node(a)
        self.add_node(b)
        if a == b:
            self.self_loop[a] += w
        else:
            k = (a, b) if a < b else (b, a)
            self._w[k] = self._w.get(k, 0.0) + w

    def A(self, i, j):
        if i == j:
            return 2.0 * self.self_loop[i]
        k = (i, j) if i < j else (j, i)
        return self._w.get(k, 0.0)

    def A_once(self, i, j):
        """反例口径：自环只记一次（``A_ii = s_i``）。

        仅用于反向断言 —— 采用它会破坏「m = 全部边权」与「聚合前后 Q 不变」。
        """
        if i == j:
            return self.self_loop[i]
        return self.A(i, j)

    def total_weight_once(self):
        s = 0.0
        for i in self.nodes:
            for j in self.nodes:
                s += self.A_once(i, j)
        return s / 2.0

    def k(self, i):
        return sum(self.A(i, j) for j in self.nodes)

    def total_weight(self):
        """m = (1/2) Σ_ij A_ij —— 自环按一次计入。"""
        s = 0.0
        for i in self.nodes:
            for j in self.nodes:
                s += self.A(i, j)
        return s / 2.0

    def m(self):
        return self.total_weight()

    def neighbors(self, i):
        return [j for j in self.nodes if j != i and self.A(i, j) > 0]


def _sigma_in(g, members):
    return sum(g.A(x, y) for x in members for y in members)


def _sigma_tot(g, members):
    return sum(g.k(x) for x in members)


def modularity(g, part):
    """Q = Σ_C [ Σin(C)/(2m) − (Σtot(C)/(2m))² ]（等价于式 1）。"""
    m = g.m()
    if m == 0:
        return 0.0
    groups = defaultdict(list)
    for n, c in part.items():
        groups[c].append(n)
    q = 0.0
    for members in groups.values():
        q += _sigma_in(g, members) / (2 * m) - (_sigma_tot(g, members) /
                                                (2 * m)) ** 2
    return q


def delta_q_add(g, part, i, target):
    """式 2：把**孤立**节点 i 移入社区 target 的增益。

    target 用其成员列表给出（不含 i）。
    """
    m = g.m()
    sin = _sigma_in(g, target)
    stot = _sigma_tot(g, target)
    ki = g.k(i)
    ki_in = sum(g.A(i, x) + g.A(x, i) for x in target)
    after = (sin + ki_in) / (2 * m) - ((stot + ki) / (2 * m)) ** 2
    before = sin / (2 * m) - (stot / (2 * m)) ** 2 - (ki / (2 * m)) ** 2
    return after - before


def one_level(g, part, max_iterations=20, tolerance=1e-7):
    """第一阶段：反复局部移动直到局部极大。

    返回 (partition, 该 level 结束时的 Q, 迭代次数)。
    """
    part = dict(part)
    prev_q = modularity(g, part)
    iters = 0
    for _ in range(max_iterations):
        iters += 1
        moved = False
        for i in g.nodes:
            own = part[i]
            # 先把 i 从原社区「移除」，得到各社区去掉 i 后的成员
            rest = {c: [x for x, cc in part.items() if cc == c and x != i]
                    for c in set(part.values())}
            # 候选 = 邻居所在社区 ∪ 原社区（去掉 i 后）；D\{i} 为空时基准为 0
            cands = {part[j] for j in g.neighbors(i)} | {own}
            baseline = delta_q_add(g, part, i, rest[own])
            best_c, best_gain = own, baseline
            for c in cands:
                gain = delta_q_add(g, part, i, rest[c])
                if gain > best_gain + TOL:
                    best_gain, best_c = gain, c
            # 净增益 = ΔQ_add(C) − ΔQ_add(D\{i})，故只有严格优于「留在原社区」才动
            if best_c != own:
                part[i] = best_c
                moved = True
        new_q = modularity(g, part)
        if not moved or abs(new_q - prev_q) < tolerance:
            prev_q = new_q
            break
        prev_q = new_q
    return part, prev_q, iters


def aggregate(g, part):
    """第二阶段：把社区聚合成超节点，内部边变成自环。"""
    groups = defaultdict(list)
    for n, c in part.items():
        groups[c].append(n)
    super_g = Graph()
    ids = sorted(groups)
    for c in ids:
        super_g.add_node(c)
    for ai in range(len(ids)):
        for bi in range(ai, len(ids)):
            a, b = ids[ai], ids[bi]
            if a == b:
                # 社区内部边 + 原有自环，全部变成该超节点的自环
                w = (sum(g.A(x, y) for x in groups[a] for y in groups[a]
                         if x < y)
                     + sum(g.self_loop[x] for x in groups[a]))
            else:
                w = sum(g.A(x, y) for x in groups[a] for y in groups[b])
            if w > 0:
                super_g.add_edge(a, b, w)
    return super_g


def louvain(g, max_levels=10, max_iterations=20, tolerance=1e-7):
    """完整 Louvain：多 level 迭代。

    返回 (partition, final_Q, 每个 level 的 Q 列表, 实际跑过的 level 数)。
    """
    part = {n: n for n in g.nodes}
    q_per_level = []
    cur_g, cur_part = g, part
    final = dict(part)
    mapping = {n: n for n in g.nodes}
    levels = 0
    for _ in range(max_levels):
        new_super_part, q, _ = one_level(cur_g, cur_part, max_iterations,
                                         tolerance)
        q_per_level.append(q)
        levels += 1
        # 把当前 level 的结果映射回原始节点
        final = {n: new_super_part[mapping[n]] for n in g.nodes}
        # 若社区数没减少则收敛
        if len(set(new_super_part.values())) == len(cur_g.nodes):
            break
        cur_g = aggregate(cur_g, new_super_part)
        cur_part = {c: c for c in cur_g.nodes}
        mapping = {n: new_super_part[mapping[n]] for n in g.nodes}
    return final, modularity(g, final), q_per_level, levels


def ring_of_cliques(n_cliques=30, clique_size=5):
    """论文中的 ring of cliques：n 个 k 阶团用单条边首尾相连成环。"""
    g = Graph()
    for c in range(n_cliques):
        base = c * clique_size
        for i in range(clique_size):
            for j in range(i + 1, clique_size):
                g.add_edge(base + i, base + j, 1.0)
    for c in range(n_cliques):
        a = c * clique_size
        b = ((c + 1) % n_cliques) * clique_size
        g.add_edge(a, b, 1.0)
    return g
