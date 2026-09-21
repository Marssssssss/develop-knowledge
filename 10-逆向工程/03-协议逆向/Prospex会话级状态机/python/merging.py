"""Prospex —— 状态合并（论文 §2.3.3 Exbar）。

一致性判据 + 最小块数穷举 + 合并后的 DFA。
"""

from __future__ import annotations


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
