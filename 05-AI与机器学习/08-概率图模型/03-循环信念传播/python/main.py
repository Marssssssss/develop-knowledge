"""循环信念传播（loopy BP）：收敛判据与消息误差 —— 依 Ihler, Fisher & Willsky (JMLR 6, 2005) 转写。

来源（本轮实读，ilher05a.pdf 全文 32 页）：
  * 记号：mts 是 s→t 的消息；Mt(xt) = ψt(xt)∏_{u∈Γt} mut(xt) 是信念
  * 式(6) 动态范围：d(e) = sup_{a,b} sqrt(e(a)/e(b))
  * Lemma 1：log d(e) = ½(sup log e − inf log e)（即「零中心化」后的最大对数误差）
  * Theorem 2：消息都归一化时 |log m(x) − log m̂(x)| ≤ 2 log d(e)
  * 式(7) 势函数强度：d(ψts)² = sup_{a,b,c,d} [ψts(a,b)ψts(c,d)] / [ψts(a,d)ψts(c,b)]
    —— 二元对称势 [[η,1−η],[1−η,η]] 下即 (η/(1−η))²
  * Theorem 8（压缩率）：d(e^{i+1}_ts) ≤ [d(ψts)²·d(E^i_ts) + 1] / [d(ψts)² + d(E^i_ts)]
  * Theorem 10（Simon 条件 / Tatikonda & Jordan 2002）：
        max_t Σ_{u∈Γt} log d(ψ_ut) < 1  ⇒ loopy BP 必收敛
  * Theorem 11（本文更强条件）：令 g_ts(z) = Σ_{u∈Γt\\s} log[(d(ψ_ut)²e^z+1)/(d(ψ_ut)²+e^z)]，
    若 g_ts(0)=0、g''_ts(z) ≤ 0 且 g'_ts(0) < 1 则收敛；g'_ts(0) = Σ (d²−1)/(d²+1)
  * 关系：对 x ≥ 1 有 log x ≥ (x²−1)/(x²+1)（等号仅在 x→1）⇒ Simon 条件蕴含 Theorem 11
  * 单环图：每个结点最多两个邻居 ⇒ |Γt\\s| ≤ 1 ⇒ g'_ts(0) = (d²−1)/(d²+1) < 1 恒成立，
    故（有限强度势下）单环图上 BP 必收敛到唯一不动点（Weiss 2000）
"""

import math


# ------------------------------------------------------------------ 动态范围
def dynamic_range(f):
    """式(6)：d(e) = sup_{a,b} sqrt(e(a)/e(b))。"""
    vals = [v for v in f if v > 0]
    if not vals:
        return float("inf")
    return math.sqrt(max(vals) / min(vals))


def log_dynamic_range(f):
    """Lemma 1：log d(e) = ½(sup log e − inf log e)。"""
    vals = [v for v in f if v > 0]
    if not vals:
        return float("inf")
    return 0.5 * (math.log(max(vals)) - math.log(min(vals)))


def potential_strength(psi):
    """式(7)：d(ψ)² = sup_{a,b,c,d} ψ(a,b)ψ(c,d) / (ψ(a,d)ψ(c,b))；返回 d(ψ)。"""
    n = len(psi)
    best = 0.0
    for a in range(n):
        for b in range(n):
            for c in range(n):
                for d in range(n):
                    denom = psi[a][d] * psi[c][b]
                    if denom <= 0:
                        return float("inf")
                    best = max(best, psi[a][b] * psi[c][d] / denom)
    return math.sqrt(best)


def logd_of_psi(psi):
    """log d(ψ)（势函数强度的对数）。"""
    return math.log(potential_strength(psi))


# -------------------------------------------------------------- 图与势函数
class MRF:
    """二元变量的成对马尔可夫随机场。"""

    def __init__(self, nodes, edges, node_pot, edge_pot):
        self.nodes = list(nodes)
        self.edges = [tuple(e) for e in edges]
        self.node_pot = node_pot          # node -> [p0, p1]
        self.edge_pot = edge_pot          # frozenset((u,v)) -> [[..],[..]]
        self.gamma = {n: [] for n in self.nodes}
        for u, v in self.edges:
            self.gamma[u].append(v)
            self.gamma[v].append(u)

    def psi(self, u, v):
        return self.edge_pot[frozenset((u, v))]


def symmetric_potential(eta):
    """二元对称（Ising 型）势：[[η, 1−η], [1−η, η]]。"""
    return [[eta, 1.0 - eta], [1.0 - eta, eta]]


# ------------------------------------------------------------------ 和积 BP
def init_messages(mrf):
    return {(u, v): [1.0, 1.0] for u, v in mrf.edges for u, v in [(u, v), (v, u)]}


def message_update(mrf, messages, t, s):
    """Ihler 式(3)(4)：Mts(xt) ∝ ψt(xt)∏_{u∈Γt\\s} mut(xt)；

    m^{i+1}_ts(xs) ∝ Σ_{xt} ψts(xs,xt)·Mts(xt)  —— **结果是 xs 的函数**（键 (t,s) = t→s）。
    """
    psi = mrf.psi(t, s)
    out = [0.0, 0.0]
    for xs in (0, 1):
        acc = 0.0
        for xt in (0, 1):
            prod = mrf.node_pot[t][xt] * psi[xs][xt]
            for u in mrf.gamma[t]:
                if u == s:
                    continue
                prod *= messages[(u, t)][xt]
            acc += prod
        out[xs] = acc
    z = out[0] + out[1]
    return [out[0] / z, out[1] / z] if z > 0 else [0.5, 0.5]


def belief(mrf, messages, t):
    """Mt(xt) ∝ ψt(xt) ∏_{u∈Γt} mut(xt)。"""
    out = [0.0, 0.0]
    for xt in (0, 1):
        p = mrf.node_pot[t][xt]
        for u in mrf.gamma[t]:
            p *= messages[(u, t)][xt]
        out[xt] = p
    z = sum(out)
    return [out[0] / z, out[1] / z] if z > 0 else [0.5, 0.5]


def run_bp(mrf, max_iter=200, tol=1e-12, messages=None, damping=0.0):
    """同步（或带阻尼的）和积 BP。返回 (messages, history, status)。

    status ∈ {"converged", "oscillating", "not-converged"}
    """
    msgs = messages if messages is not None else init_messages(mrf)
    history = []
    recent = []          # 最近三个消息快照，用于周期 2 检测
    status = "not-converged"
    for i in range(max_iter):
        new = {}
        for (u, v) in list(msgs.keys()):
            m = message_update(mrf, msgs, u, v)
            if damping > 0.0:
                old = msgs[(u, v)]
                m = [
                    (1 - damping) * math.log(m[k]) + damping * math.log(old[k])
                    for k in (0, 1)
                ]
                z = math.log(math.exp(m[0]) + math.exp(m[1]))
                m = [math.exp(m[0] - z), math.exp(m[1] - z)]
            new[(u, v)] = m
        delta = max(abs(new[k][0] - msgs[k][0]) for k in msgs)
        history.append(delta)
        # 周期 2 检测：直接比消息 —— m_i ≈ m_{i−2} 而 m_i ≠ m_{i−1}
        if len(recent) >= 2 and delta > 1e-9:
            same = max(abs(new[k][0] - recent[-2][k][0]) for k in new)
            if same < tol:
                msgs = new
                status = "oscillating"
                break
        recent.append(new)
        if len(recent) > 3:
            recent.pop(0)
        msgs = new
        if delta < tol:
            status = "converged"
            break
    return msgs, history, status


# --------------------------------------------------------------- 收敛判据
def simon_condition(mrf):
    """Theorem 10：max_t Σ_{u∈Γt} log d(ψ_ut)。< 1 即保证收敛。"""
    best = 0.0
    for t in mrf.nodes:
        s = sum(logd_of_psi(mrf.psi(t, u)) for u in mrf.gamma[t])
        best = max(best, s)
    return best


def theorem11_derivative(mrf):
    """Theorem 11 的 g'(0)：max_{(t,s)} Σ_{u∈Γt\\s} (d(ψ_ut)²−1)/(d(ψ_ut)²+1)。"""
    best = 0.0
    for t in mrf.nodes:
        for s in mrf.gamma[t]:
            acc = 0.0
            for u in mrf.gamma[t]:
                if u == s:
                    continue
                d2 = potential_strength(mrf.psi(t, u)) ** 2
                acc += (d2 - 1.0) / (d2 + 1.0)
            best = max(best, acc)
    return best


def g_curve(mrf, t, s, z):
    """Theorem 11 的 g_ts(z)。"""
    acc = 0.0
    for u in mrf.gamma[t]:
        if u == s:
            continue
        d2 = potential_strength(mrf.psi(t, u)) ** 2
        acc += math.log((d2 * math.exp(z) + 1.0) / (d2 + math.exp(z)))
    return acc


def contraction_step(dpsi_sq, err):
    """Theorem 8：d(e^{i+1}) ≤ [d(ψ)²·d(E^i) + 1] / [d(ψ)² + d(E^i)]。"""
    return (dpsi_sq * err + 1.0) / (dpsi_sq + err)


# ------------------------------------------------------------------ 真值参照
def brute_force_marginals(mrf):
    """暴力枚举全部赋值求单变量边缘（BP 的独立参照）。"""
    n = len(mrf.nodes)
    joint = {}
    for mask in range(1 << n):
        assign = [(mask >> (n - 1 - i)) & 1 for i in range(n)]
        val = {}
        for i, node in enumerate(mrf.nodes):
            val[node] = assign[i]
        p = 1.0
        for node in mrf.nodes:
            p *= mrf.node_pot[node][val[node]]
        for u, v in mrf.edges:
            p *= mrf.psi(u, v)[val[u]][val[v]]
        joint[mask] = p
    z = sum(joint.values())
    out = {}
    for i, node in enumerate(mrf.nodes):
        p1 = sum(p for mask, p in joint.items() if (mask >> (n - 1 - i)) & 1) / z
        out[node] = [1.0 - p1, p1]
    return out


def make_chain(n, eta=0.7):
    nodes = ["X%d" % i for i in range(n)]
    edges = [(nodes[i], nodes[i + 1]) for i in range(n - 1)]
    node_pot = {x: [1.0, 1.0] for x in nodes}
    edge_pot = {frozenset(e): symmetric_potential(eta) for e in edges}
    return MRF(nodes, edges, node_pot, edge_pot)


def make_cycle(n, eta=0.7):
    nodes = ["X%d" % i for i in range(n)]
    edges = [(nodes[i], nodes[(i + 1) % n]) for i in range(n)]
    node_pot = {x: [1.0, 1.0] for x in nodes}
    edge_pot = {frozenset(e): symmetric_potential(eta) for e in edges}
    return MRF(nodes, edges, node_pot, edge_pot)


def make_grid(rows, cols, eta=0.7):
    nodes = ["X%d_%d" % (r, c) for r in range(rows) for c in range(cols)]
    edges = []
    for r in range(rows):
        for c in range(cols):
            if c + 1 < cols:
                edges.append((nodes[r * cols + c], nodes[r * cols + c + 1]))
            if r + 1 < rows:
                edges.append((nodes[r * cols + c], nodes[(r + 1) * cols + c]))
    node_pot = {x: [1.0, 1.0] for x in nodes}
    edge_pot = {frozenset(e): symmetric_potential(eta) for e in edges}
    return MRF(nodes, edges, node_pot, edge_pot)
