"""Prospex —— 会话级协议规范抽取（Comparetti et al., IEEE S&P 2009）。

流水线（对应论文 §2）：

    messages --> 特征与相似度 --> PAM 聚类 --> Dunn 选 k --> 每类合并格式
         |                                            |
         +--> 会话 = 消息类型序列 --> APTA --> 前置条件标注 --> Exbar 最小化

本文件只做「可判定的那部分」：相似度/聚类/APTA/标注/状态合并。执行轨迹本身
（污点分析、系统调用跟踪）不在这里，而是以特征集合的形式喂进来。

运行：python main.py
"""

from __future__ import annotations

import itertools

# ---------------------------------------------------------------- 特征与距离

# 三组特征（论文 §2.2）：message / execution / file-system。
# 论文规定：每组总权重 1/3，组内特征等权。
FEATURE_GROUPS = (
    ("direction", "keywords"),   # message 组
    ("funcs", "syscalls"),       # execution 组
    ("fileops",),                # file-system 组
)


def feature_weights():
    """每组 1/3，组内等权。"""
    w = {}
    for group in FEATURE_GROUPS:
        for name in group:
            w[name] = 1.0 / (3 * len(group))
    return w


WEIGHTS = feature_weights()


def jaccard(a, b):
    """Jaccard 指数。两边都空时定义为 1（完全相似），否则 0/0 无定义。"""
    if not a and not b:
        return 1.0
    u = a | b
    if not u:
        return 1.0
    return len(a & b) / len(u)


def _eq(a, b):
    return 1.0 if a == b else 0.0


def similarity(a, b):
    """s_i(a,b) 逐特征相似度。direction 只有相等/不等两种，其余走 Jaccard。"""
    return {
        "direction": _eq(a.direction, b.direction),
        "keywords": jaccard(a.keywords, b.keywords),
        "funcs": jaccard(a.funcs, b.funcs),
        "syscalls": jaccard(a.syscalls, b.syscalls),
        "fileops": jaccard(a.fileops, b.fileops),
    }


def distance(a, b):
    """d(a,b) = 1 - sum_i w_i * s_i(a,b)（论文 §2.2.2 式）。"""
    s = similarity(a, b)
    return 1.0 - sum(WEIGHTS[k] * v for k, v in s.items())


class Message:
    """一条被监控到的协议消息。"""

    def __init__(self, mid, direction, keywords=(), funcs=(), syscalls=(),
                 fileops=()):
        self.mid = mid
        self.direction = direction
        self.keywords = frozenset(keywords)
        self.funcs = frozenset(funcs)
        self.syscalls = frozenset(syscalls)
        self.fileops = frozenset(fileops)

    def __repr__(self):
        return "Message(%s)" % self.mid


# ---------------------------------------------------------------- PAM 聚类

def _cost(medoids, points, dist):
    return sum(min(dist(p, m) for m in medoids) for p in points)


def _assign(medoids, points, dist):
    clusters = [[] for _ in medoids]
    for p in points:
        best = min(range(len(medoids)), key=lambda i: dist(p, medoids[i]))
        clusters[best].append(p)
    return clusters


def pam(points, k, dist, iters=40, seed=0):
    """Partitioning Around Medoids：先贪心选初始代表点，再 swap 到局部最优。"""
    n = len(points)
    if k >= n:
        return [[p] for p in points], [p for p in points]
    # BUILD：首个取离全局质心最近的点，之后取「能最大降低代价」的点
    chosen = [points[0]]
    while len(chosen) < k:
        cand, gain = None, None
        for p in points:
            if p in chosen:
                continue
            g = _cost(chosen, points, dist) - _cost(chosen + [p], points, dist)
            if gain is None or g > gain:
                cand, gain = p, g
        chosen.append(cand)
    # SWAP
    cur = _cost(chosen, points, dist)
    for _ in range(iters):
        improved = False
        for i, m in enumerate(chosen):
            for p in points:
                if p in chosen:
                    continue
                trial = list(chosen)
                trial[i] = p
                c = _cost(trial, points, dist)
                if c < cur - 1e-12:
                    chosen, cur, improved = trial, c, True
                    break
            if improved:
                break
        if not improved:
            break
    return _assign(chosen, points, dist), chosen


# ---------------------------------------------------------------- Dunn 指数

def _rng_diameter(cluster, dist):
    """基于 Relative Neighborhood Graph 的直径（论文 §2.2.2 引 [29]）。

    RNG 中 (a,b) 成边当且仅当不存在 c 使 max(d(a,c), d(b,c)) < d(a,b)。
    直径取 RNG 上的最大边权；单点/无边时为 0。
    """
    if len(cluster) < 2:
        return 0.0
    best = 0.0
    for a, b in itertools.combinations(cluster, 2):
        dab = dist(a, b)
        if all(max(dist(a, c), dist(b, c)) >= dab for c in cluster
               if c is not a and c is not b):
            best = max(best, dab)
    return best


def dunn_index(clusters, dist):
    """D(k) = min_{i≠j} δ(Ci,Cj) / max_i Δ(Ci)，δ 取 single-linkage。"""
    nonempty = [c for c in clusters if c]
    if len(nonempty) < 2:
        return 0.0
    sep = min(dist(a, b) for ci, cj in itertools.combinations(nonempty, 2)
              for a in ci for b in cj)
    dia = max(_rng_diameter(c, dist) for c in nonempty)
    if dia == 0.0:
        return float("inf") if sep > 0 else 0.0
    return sep / dia


def choose_k(points, dist, kmax):
    """枚举 k = 2..kmax，取 Dunn 指数最大的那个。"""
    best_k, best_v = 1, -1.0
    for k in range(2, min(kmax, len(points)) + 1):
        clusters, _ = pam(points, k, dist)
        v = dunn_index(clusters, dist)
        if v > best_v:
            best_k, best_v = k, v
    return best_k


# ---------------------------------------------------------------- APTA

class APTA:
    """Augmented Prefix Tree Acceptor：会话序列的前缀树（论文 §2.3.1）。

    节点用 tuple(path) 表示，root = ()。每个节点带一个 label（允许的消息类型集合）。
    """

    def __init__(self, sessions):
        self.sessions = [tuple(s) for s in sessions]
        self.children = {(): {}}
        self.parents = {(): None}
        for s in self.sessions:
            node = ()
            for sym in s:
                nxt = node + (sym,)
                if nxt not in self.children:
                    self.children[nxt] = {}
                    self.parents[nxt] = node
                self.children[node].setdefault(sym, nxt)
                node = nxt

    def states(self):
        return sorted(self.children, key=lambda s: (len(s), s))

    def types(self):
        out = set()
        for s in self.sessions:
            out.update(s)
        return sorted(out)

    def step(self, state, sym):
        return self.children[state].get(sym)


# ---------------------------------------------------------------- 前置条件

def infer_prerequisites(sessions, types):
    """论文 §2.3.2 式 (2)：prerequisite = .* r (a1|..|aj)*

    - r 是「在所有会话中都出现在 m 之前」的消息类型；
    - M_r = 所有满足该条件的 m；
    - A_r = 至少在一个会话中，出现在「最后一个 r」与某个 m∈M_r 之间的类型集合。

    返回 {m: [(r, frozenset(A_r)), ...]}。
    """
    prereq = {m: [] for m in types}
    for r in types:
        m_r = []
        a_r = set()
        for m in types:
            if m == r:
                continue
            ok = True
            for s in sessions:
                idxs = [i for i, t in enumerate(s) if t == m]
                if idxs and not any(s[j] == r for j in range(min(idxs))):
                    ok = False
                    break
            if not ok:
                continue
            # 该 m 的每一次出现，其「最后一个 r」之前都确实有 r
            for s in sessions:
                last = -1
                for i, t in enumerate(s):
                    if t == r:
                        last = i
                    elif t == m and last >= 0:
                        a_r.update(s[last + 1:i])
            m_r.append(m)
        if m_r:
            for m in m_r:
                prereq[m].append((r, frozenset(a_r)))
    return prereq


def matches(path, rule):
    """路径是否满足 .* r (A)* —— 存在 j 使 path[j]==r 且其后全部落在 A 中。"""
    r, allowed = rule
    for j in range(len(path) - 1, -1, -1):
        if path[j] != r:
            continue
        if all(t in allowed for t in path[j + 1:]):
            return True
    return False


def end_types(sessions, types):
    """End-state 启发式：在所有会话中「只出现在最后」的消息类型。"""
    out = set()
    for m in types:
        seen = False
        only_last = True
        for s in sessions:
            for i, t in enumerate(s):
                if t != m:
                    continue
                seen = True
                if i != len(s) - 1:
                    only_last = False
        if seen and only_last:
            out.add(m)
    return out


def label_states(tree, prereq, ends):
    """给 APTA 每个状态标上「该状态允许接收的消息类型集合」。

    - 若到达该状态的路径以某个 end 类型结尾 -> label = 空集；
    - 否则 m 允许当且仅当 m 的所有前置条件都被该路径满足。
    """
    labels = {}
    for st in tree.states():
        if st and st[-1] in ends:
            labels[st] = frozenset()
            continue
        allowed = set()
        for m, rules in prereq.items():
            if all(matches(st, rule) for rule in rules):
                allowed.add(m)
        labels[st] = frozenset(allowed)
    return labels


# ---------------------------------------------------------------- 状态合并

def is_consistent(block_of, tree, syms):
    """划分是否可合并：同块内的两个状态不能把同一个符号指向不同块。

    这是 Exbar 意义下的「与 APTA 一致」——未定义的转移不构成冲突（合并后的
    状态只是保留那些被定义过的转移），只有**都被定义且落在不同块**才算冲突。
    """
    for a in syms:
        seen = {}
        for s, b in block_of.items():
            nxt = tree.step(s, a)
            if nxt is None:
                continue
            prev = seen.setdefault(b, block_of[nxt])
            if prev != block_of[nxt]:
                return False
    return True


def label_realizable(tree, labels, block_of):
    """「label 里允许的类型都必须真的存在转移」——比 is_consistent 更强的要求。

    这条**对训练集里没出现过的行为是永远满足不了的**：例如 Agobot 示例中
    `login` 之后的 label 含 `login`（前置条件推导允许二次登录），但两个会话里
    从未出现过连续的 `login`，于是任何划分下该块都没有 `login` 转移。
    这正是论文自述的局限「trace-based 方法学不到训练集中不存在的 behavior」。
    """
    for b in set(block_of.values()):
        members = [s for s in tree.states() if block_of[s] == b]
        lab = labels[members[0]]
        for m in tree.types():
            if m in lab and not any(tree.step(s, m) is not None
                                    for s in members):
                return False
    return True


def _rgs(n, max_blocks):
    """受限增长串：枚举 n 个元素、块数 < max_blocks 的全部集合划分。"""
    if n == 0:
        yield ()
        return
    code = [0] * n
    while True:
        yield tuple(code)
        i = n - 1
        while i >= 1:
            lim = 1 + max(code[:i])
            if code[i] + 1 <= lim and code[i] + 1 < max_blocks:
                code[i] += 1
                for j in range(i + 1, n):
                    code[j] = 0
                break
            i -= 1
        if i < 1:
            return


def merge_states(tree, labels, exhaustive_n=11, exhaustive_m=4):
    """Exbar 的最小一致 DFA：label 不同绝不合块，label 相同则尽量合。

    状态数少时按块数 m 由小到大**穷举**全部划分，返回可判定的最小机器；
    状态多时退回贪心两两合并（可能不是最小）。返回 (block_of, blocks)。
    """
    states = tree.states()
    syms = tree.types()

    def block_of_from(blocks):
        return {s: k for k, members in blocks.items() for s in members}

    if len(states) <= exhaustive_n:
        for m in range(1, exhaustive_m + 1):
            for code in _rgs(len(states), m + 1):
                if 1 + max(code) != m:
                    continue
                bo = {states[i]: code[i] for i in range(len(states))}
                same_label = all(
                    len({labels[s] for s in states if bo[s] == b}) == 1
                    for b in set(code))
                if not same_label:
                    continue
                if is_consistent(bo, tree, syms):
                    blocks = {}
                    for s in states:
                        blocks.setdefault(bo[s], []).append(s)
                    return bo, blocks

    blocks = {i: [s] for i, s in enumerate(states)}
    changed = True
    while changed:
        changed = False
        keys = list(blocks)
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                if labels[blocks[a][0]] != labels[blocks[b][0]]:
                    continue
                trial = {k: v for k, v in blocks.items()}
                trial[a] = trial[a] + trial[b]
                del trial[b]
                if is_consistent(block_of_from(trial), tree, syms):
                    blocks = trial
                    changed = True
                    break
            if changed:
                break
    return block_of_from(blocks), blocks


class DFA:
    """合并后的协议状态机；未定义的转移一律落到 reject（-1）。"""

    def __init__(self, tree, block_of, labels):
        self.syms = tree.types()
        self.block_of = block_of
        self.labels = labels
        self.start = block_of[()]
        self.reject = -1
        self.trans = {}
        for s in tree.states():
            q = block_of[s]
            for a in self.syms:
                nxt = tree.step(s, a)
                if nxt is None:
                    continue
                self.trans[(q, a)] = block_of[nxt]

    def run(self, seq):
        q = self.start
        for sym in seq:
            q = self.trans.get((q, sym), self.reject)
            if q == self.reject:
                return self.reject
        return q

    def accepts(self, seq):
        return self.run(seq) != self.reject

    def n_states(self):
        return len(set(self.block_of.values()))


def infer_state_machine(sessions):
    tree = APTA(sessions)
    types = tree.types()
    prereq = infer_prerequisites(sessions, types)
    ends = end_types(sessions, types)
    labels = label_states(tree, prereq, ends)
    block_of, blocks = merge_states(tree, labels)
    return tree, prereq, labels, DFA(tree, block_of, labels), blocks


# ---------------------------------------------------------------- 演示

AGOBOT_SESSIONS = [
    ["login", "bot.dns", "bot.status", "mac.logout"],
    ["login", "mac.logout", "login", "bot.status", "bot.dns", "mac.logout"],
]


def demo():
    tree, prereq, labels, dfa, blocks = infer_state_machine(AGOBOT_SESSIONS)
    print("message types  :", tree.types())
    print("prerequisites  :", {m: [(r, sorted(a)) for r, a in v]
                               for m, v in prereq.items() if v})
    print("end types      :", sorted(end_types(AGOBOT_SESSIONS, tree.types())))
    for st in tree.states():
        print("  label%-52s -> %s" % (list(st), sorted(labels[st])))
    print("merged states  :", dfa.n_states(), "+ reject")
    print("label realizable:", label_realizable(tree, labels, dfa.block_of))
    for s in AGOBOT_SESSIONS:
        print("  accept", s, "->", dfa.accepts(s))
    print("  reject", ["bot.dns"], "->", dfa.accepts(["bot.dns"]))


if __name__ == "__main__":
    demo()
