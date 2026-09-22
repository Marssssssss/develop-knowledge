"""循环信念传播 —— 自检（全部实跑）。

依据 Ihler, Fisher & Willsky, *Loopy Belief Propagation: Convergence and Effects
of Message Errors*, JMLR 6 (2005) 905–936（本轮实读全文）：式(6)(7)、Lemma 1、
Theorem 2、Theorem 8、Theorem 10（Simon 条件）、Theorem 11 与单环结论。
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (  # noqa: E402
    MRF,
    belief,
    brute_force_marginals,
    contraction_step,
    dynamic_range,
    g_curve,
    init_messages,
    log_dynamic_range,
    logd_of_psi,
    make_chain,
    make_cycle,
    make_grid,
    message_update,
    potential_strength,
    run_bp,
    simon_condition,
    symmetric_potential,
    theorem11_derivative,
)

PASS = 0


def ok(cond, label):
    global PASS
    assert cond, "FAIL: " + label
    PASS += 1


# ------------------------------------------------------- 1. 动态范围（式 6）
e = [2.0, 8.0]
ok(abs(dynamic_range(e) - 2.0) < 1e-12, "d(e) = sqrt(8/2) = 2")
ok(abs(log_dynamic_range(e) - 0.5 * math.log(4)) < 1e-12, "Lemma 1：log d = ½(sup−inf)")
ok(log_dynamic_range([3.0, 3.0]) == 0.0, "常数误差 ⇒ log d = 0（等价于消息相等）")
ok(log_dynamic_range([1.0, 1.0, 1.0]) == 0.0, "三个分量全同也是 0")

# ------------------------------------------------- 2. Theorem 2 的误差上界
m = [0.3, 0.7]
for e2 in ([1.5, 0.5], [4.0, 1.0], [1.0, 3.0]):
    raw = [m[i] * e2[i] for i in range(2)]
    z = sum(raw)
    mhat = [v / z for v in raw]
    err = max(abs(math.log(m[i]) - math.log(mhat[i])) for i in range(2))
    ok(err <= 2 * log_dynamic_range(e2) + 1e-12,
       "Theorem 2：|log m − log m̂| ≤ 2 log d(e)（%.4f ≤ %.4f）"
       % (err, 2 * log_dynamic_range(e2)))

# ----------------------------------------------- 3. 势函数强度（式 7 / 二元）
for eta in (0.6, 0.7, 0.9):
    psi = symmetric_potential(eta)
    expect = max(eta / (1 - eta), (1 - eta) / eta)
    ok(abs(potential_strength(psi) - expect) < 1e-12,
       "对称势 η=%.1f 的 d(ψ) = %.4f" % (eta, expect))
ok(potential_strength(symmetric_potential(0.5)) == 1.0, "η=0.5 无耦合 ⇒ d(ψ)=1")
ok(logd_of_psi(symmetric_potential(0.5)) == 0.0, "η=0.5 ⇒ log d(ψ)=0")

# ------------------------------------------------- 4. Theorem 8 的压缩映射
ok(abs(contraction_step(81.0, 1.0) - 1.0) < 1e-12, "误差为 1（无误差）时压缩式不动")
ok(abs(contraction_step(81.0, 2.0) - 163.0 / 83.0) < 1e-12, "d(ψ)²=81, d(E)=2 ⇒ 163/83")
ok(contraction_step(81.0, 2.0) < 2.0, "压缩后误差变小（1.9639 < 2）")
for err in (1.5, 2.0, 5.0, 20.0):
    ok(contraction_step(81.0, err) < err, "d(E)=%.1f ⇒ 严格压缩" % err)
ok(contraction_step(1.0, 5.0) == 1.0, "d(ψ)=1（完全不耦合）⇒ 一步压到 1")

# -------------------------------------- 5. Simon 条件 ⟹ Theorem 11（x≥1 不等式）
for x in (1.0, 1.2, 2.0, 5.0, 20.0):
    ok(math.log(x) >= (x * x - 1) / (x * x + 1) - 1e-12,
       "log x ≥ (x²−1)/(x²+1) 在 x=%.1f 成立" % x)
for g in (make_chain(6, 0.8), make_cycle(6, 0.8), make_grid(3, 3, 0.85)):
    ok(simon_condition(g) >= theorem11_derivative(g) - 1e-9,
       "Simon 值 %.4f ≥ Theorem 11 值 %.4f（前者蕴含后者）"
       % (simon_condition(g), theorem11_derivative(g)))

# ---------------------------------------- 6. 单环：Theorem 11 恒成立（Weiss 2000）
for eta in (0.6, 0.75, 0.9, 0.99):
    c = make_cycle(6, eta)
    ok(theorem11_derivative(c) < 1.0,
       "单环 η=%.2f：|Γt\\s| ≤ 1 ⇒ g'(0) = %.4f < 1，必收敛"
       % (eta, theorem11_derivative(c)))
    ok(g_curve(c, c.nodes[0], c.gamma[c.nodes[0]][0], 0.0) == 0.0,
       "单环 η=%.2f：g_ts(0) = 0" % eta)
c4 = make_cycle(4, 0.9)
z = 0.5
ok(g_curve(c4, c4.nodes[0], c4.gamma[c4.nodes[0]][0], z) < z,
   "单环上 g(z) < z（凹性 + g'(0)<1 ⇒ 全局压缩）")

# --------------------------------------------------------- 7. 树上 BP 精确
chain = make_chain(5, 0.7)
chain.node_pot = {n: ([0.8, 0.2] if i % 2 else [0.3, 0.7])
                  for i, n in enumerate(chain.nodes)}
msgs, hist, status = run_bp(chain, max_iter=200)
truth = brute_force_marginals(chain)
ok(status == "converged", "链上 BP 收敛")
ok(len(hist) <= len(chain.nodes), "链上迭代数 ≤ 结点数（树的直径级别）")
for n in chain.nodes:
    b = belief(chain, msgs, n)
    ok(abs(b[0] - truth[n][0]) < 1e-9, "树上变量 %s 的边缘**精确**等于真值" % n)

# ------------------------------------------------- 8. 单环收敛但结果不精确
cyc = make_cycle(6, 0.75)
cyc.node_pot = {n: ([0.8, 0.2] if i % 2 else [0.3, 0.7])
                for i, n in enumerate(cyc.nodes)}
msgs_c, hist_c, status_c = run_bp(cyc, max_iter=400)
truth_c = brute_force_marginals(cyc)
ok(status_c == "converged", "6-环（单环）BP 收敛 —— 与 Theorem 11 一致")
gap = max(abs(belief(cyc, msgs_c, n)[0] - truth_c[n][0]) for n in cyc.nodes)
ok(gap > 1e-6, "单环上信念仍与真值有偏差（%.4f）—— 收敛 ≠ 正确" % gap)

# ------------------------------------------------- 9. 多环 + 强耦合 ⇒ 振荡
OSC_NODES = ["A", "B", "C", "D"]
OSC_EDGES = [("A", "B"), ("B", "C"), ("C", "A"), ("B", "D"), ("D", "C")]
OSC_EDGE_POT = {
    frozenset(("A", "B")): [[0.010978397260510713, 1.0], [1.0, 0.019413738807808292]],
    frozenset(("B", "C")): [[0.004712618849374387, 1.0], [1.0, 0.003938346153881924]],
    frozenset(("A", "C")): [[0.0038705150091150552, 1.0], [1.0, 0.002065030001216686]],
    frozenset(("B", "D")): [[0.012678507316066592, 1.0], [1.0, 0.013142560290812521]],
    frozenset(("C", "D")): [[0.02892728734048402, 1.0], [1.0, 0.013599781195054766]],
}
OSC_NODE_POT = {
    "A": [0.5571823433712233, 1.203893155848943],
    "B": [1.716334307931209, 1.123364717787998],
    "C": [0.8346887594412482, 1.7620081689486722],
    "D": [0.2745322889884101, 1.5735495344778874],
}
osc = MRF(OSC_NODES, OSC_EDGES, OSC_NODE_POT, OSC_EDGE_POT)
ok(theorem11_derivative(osc) > 1.0, "振荡实例：Theorem 11 判据 %.4f > 1（不满足）"
   % theorem11_derivative(osc))
ok(simon_condition(osc) > 1.0, "振荡实例：Simon 条件 %.3f > 1（也不满足）"
   % simon_condition(osc))
mosc, hist_o, status_o = run_bp(osc, max_iter=300)
ok(status_o == "oscillating", "双三角（多环）+ 强势 ⇒ 同步 BP 陷入周期 2")

# 自证：m_i 与 m_{i−2} 真的逐分量相等，而 m_i ≠ m_{i−1}
snap = [mosc]
cur = mosc
for _ in range(4):
    cur = {k: message_update(osc, cur, k[0], k[1]) for k in cur}
    snap.append(cur)
same2 = max(abs(snap[2][k][0] - snap[0][k][0]) for k in snap[0])
diff1 = max(abs(snap[1][k][0] - snap[0][k][0]) for k in snap[0])
ok(same2 < 1e-12, "自证：m_{i} 与 m_{i−2} 逐分量相同（%.2e）" % same2)
ok(diff1 > 1e-3, "自证：m_{i} 与 m_{i−1} 明显不同（%.4f）—— 周期 2 不是误判" % diff1)
truth_o = brute_force_marginals(osc)
bad = abs(belief(osc, mosc, "A")[0] - truth_o["A"][0])
ok(bad > 0.5, "振荡时 BP 的信念与真值相差 %.4f（> 0.5）：环上结果是近似的甚至离谱" % bad)

# -------------------------------- 10. 初值无关性：收敛时不同初值落到同一不动点
alt = {k: ([0.9, 0.1] if i % 3 else [0.4, 0.6]) for i, k in enumerate(init_messages(cyc))}
msgs_a, _, st_a = run_bp(cyc, max_iter=400, messages=alt)
ok(st_a == "converged", "换初值后单环仍收敛")
ok(max(abs(belief(cyc, msgs_a, n)[0] - belief(cyc, msgs_c, n)[0])
       for n in cyc.nodes) < 1e-9,
   "单环不动点与初值无关（Theorem 11 保证唯一性）")

print("PASS =", PASS)
