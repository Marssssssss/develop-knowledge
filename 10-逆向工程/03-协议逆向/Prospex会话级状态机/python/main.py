"""Prospex —— 会话级协议规范抽取（Comparetti et al., IEEE S&P 2009）。

流水线（对应论文 §2）：

    messages --> 特征与相似度 --> PAM 聚类 --> Dunn 选 k --> 每类合并格式
         |                                            |
         +--> 会话 = 消息类型序列 --> APTA --> 前置条件标注 --> Exbar 最小化

本文件只做「可判定的那部分」：APTA / 前置条件 / 标注 / 状态合并；
特征与聚类在 features.py。执行轨迹本身（污点分析、系统调用跟踪）不在这里，
而是以特征集合的形式喂进来。

运行：python main.py
"""

from __future__ import annotations

from features import (
    FEATURE_GROUPS,
    feature_weights,
    WEIGHTS,
    jaccard,
    similarity,
    distance,
    Message,
    pam,
    dunn_index,
    choose_k,
)

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

from merging import (  # noqa: F401
    DFA, is_consistent, label_realizable, merge_states,
)

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
