"""Prospex 模型的自检。

E1~E5 特征/距离/PAM/Dunn；E6~E10 APTA/前置条件/标注（逐条对齐论文 Fig 3）；
E11~E14 状态合并与接受-拒绝行为；E15 end-state 启发式。
断言全部基于实读 `oakland09_prospex.pdf`（IEEE S&P 2009）后的复现结论。

运行：python selfcheck_prospex.py
"""

import sys

from main import (  # noqa: F401
    AGOBOT_SESSIONS, APTA, Message, WEIGHTS, dunn_index, distance,
    end_types, feature_weights, infer_prerequisites, infer_state_machine,
    is_consistent, jaccard, label_realizable, label_states, matches, merge_states,
    pam, similarity,
)
from merging import _rgs  # noqa: F401

PASS = [0]
FAIL = [0]


def ok(cond, msg):
    if cond:
        PASS[0] += 1
    else:
        FAIL[0] += 1
        print("  FAIL:", msg)


def eq(got, want, msg):
    ok(got == want, "%s (got=%r want=%r)" % (msg, got, want))


def close(got, want, msg, tol=1e-9):
    ok(abs(got - want) < tol, "%s (got=%r want=%r)" % (msg, got, want))


def mk(mid, **kw):
    return Message(mid, kw.pop("direction", "in"), **kw)


# ------------------------------------------------------------ E1 权重

def e1_weights():
    w = feature_weights()
    close(sum(w.values()), 1.0, "E1 权重和为 1")
    close(w["direction"] + w["keywords"], 1 / 3, "E1 message 组占 1/3")
    close(w["funcs"] + w["syscalls"], 1 / 3, "E1 execution 组占 1/3")
    close(w["fileops"], 1 / 3, "E1 file-system 组占 1/3")
    eq(WEIGHTS["direction"], WEIGHTS["keywords"], "E1 组内等权")
    eq(WEIGHTS["funcs"], WEIGHTS["syscalls"], "E1 组内等权")


# ------------------------------------------------------------ E2 Jaccard

def e2_jaccard():
    eq(jaccard(set(), set()), 1.0, "E2 空集对空集定义为 1")
    eq(jaccard({1, 2}, {3, 4}), 0.0, "E2 不相交为 0")
    eq(jaccard({1, 2}, {1, 2}), 1.0, "E2 完全相同为 1")
    close(jaccard({1, 2, 3}, {2, 3, 4}), 2 / 4, "E2 交 2 并 4")


# ------------------------------------------------------------ E3 距离

def e3_distance():
    a = mk("a", keywords={"GET"}, funcs={"recv"}, syscalls={"read"})
    b = mk("b", direction="out", keywords={"PUT"})
    close(distance(a, a), 0.0, "E3 d(a,a)=0")
    close(distance(a, b), distance(b, a), "E3 对称")
    ok(0.0 <= distance(a, b) <= 1.0, "E3 距离落在 [0,1]")
    # direction 权重 1/6：只改方向时 d = 1 - (1/3 - 1/6) = 5/6
    c = mk("c", direction="out", keywords=set(a.keywords),
           funcs=set(a.funcs), syscalls=set(a.syscalls), fileops=set(a.fileops))
    close(distance(a, c), 1 / 6, "E3 只方向不同 -> 1/6")
    s = similarity(a, a)
    eq(s["direction"], 1.0, "E3 自身 direction 相似度为 1")
    eq(s["fileops"], 1.0, "E3 空 fileops 视为相同")


# ------------------------------------------------------------ E4 PAM

def _three_clusters():
    pts = []
    for i in range(4):
        pts.append(mk("a%d" % i, keywords={"LOGIN"}, funcs={"f1"},
                      syscalls={"read"}, fileops={("open", "/etc/A")}))
        pts.append(mk("b%d" % i, keywords={"GET"}, funcs={"f2"},
                      syscalls={"write"}, fileops={("write", "/var/log/B")}))
        pts.append(mk("c%d" % i, keywords={"QUIT"}, funcs={"f3"},
                      syscalls={"close"}, fileops={("open", "/tmp/C")}))
    return pts


def e4_pam():
    pts = _three_clusters()
    clusters, medoids = pam(pts, 3, distance)
    eq(len(clusters), 3, "E4 PAM 给出 3 簇")
    eq(sorted(len(c) for c in clusters), [4, 4, 4], "E4 每簇 4 条")
    for c in clusters:
        kinds = {p.mid[0] for p in c}
        eq(len(kinds), 1, "E4 簇内同类")
    eq(len({m.mid[0] for m in medoids}), 3, "E4 三个代表点来自不同类")


# ------------------------------------------------------------ E5 Dunn

def e5_dunn():
    pts = _three_clusters()
    good, _ = pam(pts, 3, distance)
    merged = [good[0] + good[1], good[2]]
    ok(dunn_index(good, distance) > dunn_index(merged, distance),
       "E5 正确划分的 Dunn 指数高于合并两簇")
    eq(dunn_index([pts], distance), 0.0, "E5 单簇时 Dunn 为 0（分母 0、无簇间距离）")
    sep = mk("s", keywords={"Z"})
    ok(dunn_index([[pts[0]], [sep]], distance) > 0,
       "E5 两簇分离度为正")


# ------------------------------------------------------------ E6 APTA

def e6_apta():
    tree = APTA(AGOBOT_SESSIONS)
    eq(len(tree.states()), 10, "E6 Agobot 两会话共 10 个前缀状态")
    eq(list(tree.children[()].values()), [("login",)], "E6 root 只有 login 一个后继")
    eq(tree.types(), ["bot.dns", "bot.status", "login", "mac.logout"],
       "E6 消息类型 4 种（按字典序）")
    eq(tree.step(("login",), "bot.dns"), ("login", "bot.dns"), "E6 树边存在")
    eq(tree.step(("login", "bot.dns"), "mac.logout"), None, "E6 未观测的转移不存在")


# ------------------------------------------------------------ E7 前置条件

def e7_prereq():
    types = APTA(AGOBOT_SESSIONS).types()
    prereq = infer_prerequisites(AGOBOT_SESSIONS, types)
    eq(prereq["login"], [], "E7 login 无前置条件（出现在会话最前）")
    want = ("login", frozenset({"bot.dns", "bot.status"}))
    for m in ("bot.dns", "bot.status", "mac.logout"):
        eq(prereq[m], [want], "E7 %s 的前置条件是 .*login(bot.dns|bot.status)*" % m)


def e7b_matches():
    rule = ("login", frozenset({"bot.dns", "bot.status"}))
    eq(matches(("login",), rule), True, "E7b [login] 满足")
    eq(matches(("login", "bot.dns", "bot.status"), rule), True, "E7b 连续两次可选消息仍满足")
    eq(matches(("login", "mac.logout"), rule), False, "E7b 夹入 mac.logout 后不再满足")
    eq(matches((), rule), False, "E7b 空路径不满足")
    eq(matches(("bot.dns", "login"), rule), True, "E7b 允许 r 之前有任意消息")


# ------------------------------------------------------------ E8/E9 标注

def e9_labels():
    tree = APTA(AGOBOT_SESSIONS)
    types = tree.types()
    prereq = infer_prerequisites(AGOBOT_SESSIONS, types)
    ends = end_types(AGOBOT_SESSIONS, types)
    eq(ends, set(), "E8 mac.logout 在会话 2 中非末位 -> 没有 end 类型")
    labels = label_states(tree, prereq, ends)
    full = frozenset({"login", "mac.logout", "bot.status", "bot.dns"})
    only_login = frozenset({"login"})
    # 论文 Figure 3 逐条对拍
    eq(labels[()], only_login, "E9 root 只允许 login")
    eq(labels[("login",)], full, "E9 [login] 允许全部四种")
    eq(labels[("login", "bot.dns")], full, "E9 [login,bot.dns] 允许全部四种")
    eq(labels[("login", "bot.dns", "bot.status")], full, "E9 三种之后仍允许全部")
    eq(labels[("login", "bot.dns", "bot.status", "mac.logout")], only_login,
       "E9 mac.logout 之后只剩 login")
    eq(labels[("login", "mac.logout")], only_login, "E9 [login,mac.logout] 只剩 login")
    eq(labels[("login", "mac.logout", "login")], full, "E9 二次 login 恢复全部")
    eq(labels[("login", "mac.logout", "login", "bot.status")], full,
       "E9 二次会话中段允许全部")
    eq(labels[("login", "mac.logout", "login", "bot.status", "bot.dns")], full,
       "E9 同上")
    eq(labels[("login", "mac.logout", "login", "bot.status", "bot.dns",
               "mac.logout")], only_login, "E9 末尾回到只剩 login")


# ------------------------------------------------------------ E11-14 合并

def e11_merge():
    tree = APTA(AGOBOT_SESSIONS)
    tree, prereq, labels, dfa, blocks = infer_state_machine(AGOBOT_SESSIONS)
    eq(dfa.n_states(), 2, "E11 最小一致机器 2 个接收状态")
    ok(is_consistent(dfa.block_of, tree, tree.types()), "E11 划分一致")
    # 最小性：穷举块数 m=1 的全部划分，确认没有合法解
    states = tree.states()
    found_one = False
    for code in _rgs(len(states), 2):
        if 1 + max(code) != 1:
            continue
        bo = {states[i]: code[i] for i in range(len(states))}
        same = len({labels[s] for s in states}) == 1
        if same and is_consistent(bo, tree, tree.types()):
            found_one = True
    ok(not found_one, "E11 不存在合法的 1 块划分 -> 2 是可判定的最小")
    eq(len({labels[s] for s in states}), 2, "E11 只有两种不同 label")
    eq(label_realizable(tree, labels, dfa.block_of), False,
       "E14 label 不可完全实现（训练集没出现过的行为学不到）")
    return dfa


def e12_accept():
    dfa = infer_state_machine(AGOBOT_SESSIONS)[3]
    for s in AGOBOT_SESSIONS:
        ok(dfa.accepts(s), "E12 接受训练集会话 %s" % s)
        for i in range(len(s) + 1):
            ok(dfa.accepts(s[:i]), "E12 接受前缀 %s" % s[:i])


def e13_reject():
    dfa = infer_state_machine(AGOBOT_SESSIONS)[3]
    for seq in (["bot.dns"], ["bot.status"], ["mac.logout"],
                ["login", "mac.logout", "bot.dns"],
                ["login", "bot.dns", "mac.logout", "bot.status"],
                ["login", "login"]):
        ok(not dfa.accepts(seq), "E13 拒绝非法序列 %s" % seq)
    eq(dfa.run(["bot.dns"]), dfa.reject, "E13 非法序列落到 reject")


# ------------------------------------------------------------ E15 end-state

def e15_end_state():
    sessions = [["HELO", "MAIL", "QUIT"], ["HELO", "RCPT", "DATA", "QUIT"]]
    types = APTA(sessions).types()
    eq(end_types(sessions, types), {"QUIT"}, "E15 QUIT 总是最后出现")
    tree = APTA(sessions)
    prereq = infer_prerequisites(sessions, types)
    labels = label_states(tree, prereq, end_types(sessions, types))
    eq(labels[("HELO", "MAIL", "QUIT")], frozenset(), "E15 end 之后 label 为空集")
    # DATA 多一条前置条件：RCPT 总在 DATA 之前，且两者相邻（A_r 为空）
    eq(prereq["DATA"][-1], ("RCPT", frozenset()), "E15 DATA 还需 .*RCPT")
    eq(labels[("HELO",)], frozenset({"HELO", "MAIL", "RCPT", "QUIT"}),
       "E15 HELO 之后 DATA 尚不可用（缺 RCPT）")
    eq(labels[("HELO", "RCPT")] , frozenset(types),
       "E15 HELO,RCPT 之后才允许全部五种")


def main():
    for fn in (e1_weights, e2_jaccard, e3_distance, e4_pam, e5_dunn,
               e6_apta, e7_prereq, e7b_matches, e9_labels,
               e11_merge, e12_accept, e13_reject, e15_end_state):
        fn()
    print("PASS=%d FAIL=%d" % (PASS[0], FAIL[0]))
    return 1 if FAIL[0] else 0


if __name__ == "__main__":
    sys.exit(main())
