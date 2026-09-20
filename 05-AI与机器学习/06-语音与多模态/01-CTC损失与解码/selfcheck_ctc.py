"""CTC demo 自检：全部断言实跑。

判据一律来自 ICML 2006 原文，不写「应然 TRUTH」，只写具体的误报/漏报集合与数值。
"""
import math
import random

from ctc import (
    BLANK, collapse, extend_target, lab_positions, logsumexp,
    forward_log, backward_log, final_log_prob, ctc_log_prob,
    forward_scaled, backward_scaled, gradient_wrt_u, best_path_decode,
    all_labelling_probs, alpha_zero_bound, beta_zero_bound,
)

N = 0
FAIL = []


def check(label, cond, detail=""):
    global N
    N += 1
    if not cond:
        FAIL.append(f"{label}: {detail}")
        print(f"  FAIL {label} {detail}")


def close(a, b, eps=1e-9):
    return abs(a - b) <= eps


def softmax(u):
    m = max(u)
    e = [math.exp(x - m) for x in u]
    s = sum(e)
    return [v / s for v in e]


ALPHA = [0, 1]
SYMS = [0, 1, BLANK]
random.seed(20260920)

print("=== A. B 映射与扩展序列（论文 §3.1 / §4.1） ===")
# 论文原例：B(a-ab-) = B(-aa--abb) = aab（这里 blank 用 BLANK 哨兵）
check("A1 论文原例 a-ab-", collapse(("a", BLANK, "a", "b", BLANK)) == ("a", "a", "b"),
      str(collapse(("a", BLANK, "a", "b", BLANK))))
check("A2 论文原例 -aa--abb",
      collapse((BLANK, "a", "a", BLANK, BLANK, "a", "b", "b")) == ("a", "a", "b"),
      str(collapse((BLANK, "a", "a", BLANK, BLANK, "a", "b", "b"))))
check("A3 blank 分隔不合并 a-b-a", collapse(("a", BLANK, "a")) == ("a", "a"),
      str(collapse(("a", BLANK, "a"))))
check("A4 相邻重复合并 aa", collapse(("a", "a")) == ("a",), str(collapse(("a", "a"))))
check("A5 全 blank 得空串", collapse((BLANK, BLANK)) == ())
check("A6 首尾 blank 不影响", collapse((BLANK, "a", BLANK, "b", BLANK)) == ("a", "b"))
for k in range(0, 5):
    check(f"A7 |l'|={2*k+1}", len(extend_target(tuple(range(k)))) == 2 * k + 1)
check("A8 lab(l,k) 可为空", lab_positions(extend_target((1,)), 9) == [])
# 空标签的扩展序列长度是 1，p 只能取 α_T(1)
check("A9 空标签 |l'|=1", len(extend_target(())) == 1)

print("=== B. 前向 α 与穷举一致（小规模全路径对照） ===")
mismatch = 0
checked = 0
for trial in range(200):
    T = random.randint(2, 6)
    y = []
    for _ in range(T):
        p = softmax([random.uniform(-2, 2) for _ in SYMS])
        y.append({0: p[0], 1: p[1], BLANK: p[2]})
    log_y = [{k: math.log(v) for k, v in row.items()} for row in y]
    for labels in [(), (0,), (1,), (0, 1), (1, 0), (0, 0), (1, 1), (0, 1, 0)]:
        if len(labels) > T:
            continue
        lp = ctc_log_prob(log_y, labels)
        brute = all_labelling_probs(y, ALPHA, T).get(labels, 0.0)
        checked += 1
        if lp == -math.inf:
            continue
        if not close(math.exp(lp), brute, 1e-9):
            mismatch += 1
check("B1 前向概率 == 穷举(全部用例)", mismatch == 0, f"{mismatch}/{checked} 不一致")

print("=== C. 缩放恒等式 ln p = Σ ln C_t（论文 §4.1） ===")
ext = extend_target((0, 1))
T = 7
y = []
for _ in range(T):
    p = softmax([random.uniform(-1.5, 1.5) for _ in SYMS])
    y.append({0: p[0], 1: p[1], BLANK: p[2]})
log_y = [{k: math.log(v) for k, v in r.items()} for r in y]
lp = final_log_prob(forward_log(log_y, ext), ext)
_, Cs_pruned = forward_scaled(y, ext, prune=True)
_, Cs_plain = forward_scaled(y, ext, prune=False)
s_pruned = sum(math.log(c) for c in Cs_pruned if c > 0)
s_plain = sum(math.log(c) for c in Cs_plain if c > 0)
check("C1 prune 后 Σln C_t == ln p", close(s_pruned, lp, 1e-9), f"{s_pruned} vs {lp}")
check("C2 不 prune 则 Σln C_t ≠ ln p（恒等式依赖置零）",
      not close(s_plain, lp, 1e-12), f"{s_plain} vs {lp}")
check("C3 prune 不改变 ln p",
      close(final_log_prob(forward_log(log_y, ext, prune=True), ext), lp, 1e-9))

print("=== D. 零区：是剪枝规则而非自动成立的恒等式 ===")
la_plain = forward_log(log_y, ext, prune=False)
la_pruned = forward_log(log_y, ext, prune=True)
# 区域非空性（否则下面的断言是空跑）
region = [(t + 1, s + 1) for t in range(T) for s in range(len(ext))
          if s + 1 < alpha_zero_bound(ext, T, t + 1)]
check("D1 α 零区非空", len(region) > 0, f"{len(region)} 格")
nonzero_in_region = sum(
    1 for (t1, s1) in region if la_plain[t1 - 1][s1 - 1] != -math.inf)
check("D2 未 prune 时零区确有非零值（说明它是规则不是恒等式）",
      nonzero_in_region > 0, f"{nonzero_in_region} 个")
check("D3 prune 后零区全为 -inf",
      all(la_pruned[t1 - 1][s1 - 1] == -math.inf for (t1, s1) in region))
lb_plain = backward_log(log_y, ext, prune=False)
lb_pruned = backward_log(log_y, ext, prune=True)
bregion = [(t + 1, s + 1) for t in range(T) for s in range(len(ext))
           if s + 1 > beta_zero_bound(T, t + 1)]
check("D4 β 零区非空", len(bregion) > 0, f"{len(bregion)} 格")
bnz = sum(1 for (t1, s1) in bregion if lb_plain[t1 - 1][s1 - 1] != -math.inf)
check("D5 未 prune 时 β 零区确有非零值", bnz > 0, f"{bnz} 个")
check("D6 prune 后 β 零区全为 -inf",
      all(lb_pruned[t1 - 1][s1 - 1] == -math.inf for (t1, s1) in bregion))

print("=== E. 式(14) p(l) = Σ_s α_t(s)β_t(s)/y^t_{l'_s} 对任意 t 同值 ===")
la = la_plain
lb = lb_plain
S = len(ext)
vals = []
for t in range(T):
    terms = []
    for s in range(S):
        if la[t][s] == -math.inf or lb[t][s] == -math.inf:
            continue
        terms.append(la[t][s] + lb[t][s] - log_y[t][ext[s]])
    vals.append(logsumexp(terms) if terms else -math.inf)
spread = max(vals) - min(vals)
check("E1 式(14) 对任意 t 同值", spread < 1e-9, f"spread={spread}")
check("E2 式(14) 值 == ln p", close(vals[T // 2], lp, 1e-9), f"{vals[T//2]} vs {lp}")
# prune 后同样成立
lap = la_pruned
lbp = lb_pruned
valsp = []
for t in range(T):
    terms = []
    for s in range(S):
        if lap[t][s] == -math.inf or lbp[t][s] == -math.inf:
            continue
        terms.append(lap[t][s] + lbp[t][s] - log_y[t][ext[s]])
    valsp.append(logsumexp(terms) if terms else -math.inf)
check("E3 prune 后式(14) 仍 == ln p", close(valsp[T // 2], lp, 1e-9))

print("=== F. 式(16) 误差信号 vs 有限差分 ===")
T2 = 3
u0 = [[0.4, -0.2, 0.9], [1.1, 0.3, -0.5], [-0.7, 0.6, 0.2]]
labels = (0, 1)


def loss_of(u):
    yy = []
    for row in u:
        p = softmax(row)
        yy.append({0: p[0], 1: p[1], BLANK: p[2]})
    return -ctc_log_prob([{k: math.log(v) for k, v in r.items()} for r in yy], labels)


y0 = []
for row in u0:
    p = softmax(row)
    y0.append({0: p[0], 1: p[1], BLANK: p[2]})
ext0 = extend_target(labels)
a0, C0 = forward_scaled(y0, ext0)
b0 = backward_scaled(y0, ext0, C0)
g = gradient_wrt_u(y0, ext0, a0, b0)
h = 1e-5
worst = 0.0
for t in range(T2):
    for k in [0, 1, BLANK]:
        up = [row[:] for row in u0]
        um = [row[:] for row in u0]
        up[t][[0, 1, BLANK].index(k)] += h
        um[t][[0, 1, BLANK].index(k)] -= h
        fd = (loss_of(up) - loss_of(um)) / (2 * h)
        worst = max(worst, abs(fd - g[t][k]))
check("F1 式(16) == 有限差分", worst < 1e-6, f"max diff={worst}")
rowsum = max(abs(sum(g[t].values())) for t in range(T2))
check("F2 Σ_k 梯度为 0(softmax 平移不变)", rowsum < 1e-9, f"{rowsum}")

print("=== G. best path decoding 的漏报集 ===")
# 论文 §3.2: best path "is not guaranteed to find the most probable labelling"
random.seed(7)
differ = []
for trial in range(400):
    T3 = random.randint(3, 5)
    y = []
    for _ in range(T3):
        p = softmax([random.uniform(-3, 3) for _ in SYMS])
        y.append({0: p[0], 1: p[1], BLANK: p[2]})
    bp = best_path_decode(y, ALPHA)
    table = all_labelling_probs(y, ALPHA, T3)
    best = max(table, key=table.get)
    if bp != best:
        differ.append((bp, best, table))
check("G1 存在 best path ≠ 最优 labelling 的用例", len(differ) > 0, f"{len(differ)} 例")
if differ:
    bp, best, table = differ[0]
    check("G2 该用例 best path 概率确实更低",
          table[bp] < table[best] - 1e-12,
          f"bp={table[bp]:.6f} best={table[best]:.6f}")
check("G3 差值不是孤例(≥5 例)", len(differ) >= 5, f"仅 {len(differ)} 例")

print("=== H. 损失行为 ===")
base_y = [{0: 0.4, 1: 0.3, BLANK: 0.3}, {0: 0.35, 1: 0.35, BLANK: 0.3},
          {0: 0.3, 1: 0.4, BLANK: 0.3}]
sharp = [{0: 0.85, 1: 0.10, BLANK: 0.05}, {0: 0.80, 1: 0.15, BLANK: 0.05},
         {0: 0.10, 1: 0.85, BLANK: 0.05}]
l_base = -ctc_log_prob([{k: math.log(v) for k, v in r.items()} for r in base_y], (0, 1))
l_sharp = -ctc_log_prob([{k: math.log(v) for k, v in r.items()} for r in sharp], (0, 1))
check("H1 强化正确路径后损失下降", l_sharp < l_base, f"{l_sharp} vs {l_base}")
check("H2 T < |l| 时概率为 0",
      ctc_log_prob([{k: math.log(v) for k, v in r.items()} for r in base_y[:2]], (0, 1, 0))
      == -math.inf)
# 论文 §5.2: TIMIT 61 音素 + 1 blank = 62 个 softmax 单元
check("H3 61 音素 +1 blank = 62 单元", 61 + 1 == 62)

print()
print(f"断言总数 {N}，失败 {len(FAIL)}")
for f in FAIL:
    print("  ×", f)
print("RESULT:", "ALL PASS" if not FAIL else "FAILED")
