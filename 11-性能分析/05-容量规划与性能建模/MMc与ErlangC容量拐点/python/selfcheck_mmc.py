"""M/M/c 与 Erlang C 自检：解析式互证 + 退化到 M/M/1 + 有限容量守恒。"""

import math
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from queuemodel import (  # noqa: E402
    MM1K,
    MMC,
    compare_configurations,
    erlang_b,
    erlang_b_inverse_recursive,
    erlang_b_recursive,
    erlang_c,
    erlang_c_via_b,
    exp_service_var,
    mg1,
    mg1_L_via_pk,
)

FAILED = []
N = 0


def ck(cond, msg):
    global N
    N += 1
    if not cond:
        FAILED.append(msg)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


# ------------------------------------------------------- 1. Erlang B 三种写法
for E in (0.5, 1.0, 2.0, 5.0, 10.0):
    for m in (1, 2, 3, 5, 10):
        a = erlang_b(E, m)
        b = erlang_b_recursive(E, m)
        c = erlang_b_inverse_recursive(E, m)
        ck(close(a, b), f"B({E},{m}) 递推 == 闭式")
        ck(close(a, c), f"B({E},{m}) 倒数递推 == 闭式")

# B(E,0) = 1；B(0,m>0) = 0
ck(erlang_b(3.0, 0) == 1.0, "B(E,0)=1")
ck(erlang_b_recursive(0.0, 4) == 0.0, "B(0,m>0)=0（无负载不阻塞）")
ck(erlang_b_inverse_recursive(0.0, 4) == 0.0, "倒数递推 E=0 退化正确")

# 手算：B(1,1) = 1/(1+1) = 0.5；B(1,2) = 0.5/2.5 = 0.2
ck(close(erlang_b(1.0, 1), 0.5), "B(1,1)=0.5")
ck(close(erlang_b(1.0, 2), 0.2), "B(1,2)=0.2")

# 单调性：服务器越多阻塞越少；负载越大阻塞越多
prev = 1.0
for m in range(1, 9):
    cur = erlang_b(5.0, m)
    ck(cur < prev, f"B(5,{m}) 随 m 递减")
    prev = cur
prev = 0.0
for E in (1.0, 2.0, 3.0, 4.0, 5.0):
    cur = erlang_b(E, 4)
    ck(cur > prev, f"B({E},4) 随 E 递增")
    prev = cur

# ------------------------------------------------- 2. Erlang C 与 B 的关系式
for E, m in ((0.5, 1), (1.0, 2), (2.0, 3), (4.0, 5), (8.0, 10)):
    ck(close(erlang_c(E, m), erlang_c_via_b(E, m)),
       f"C({E},{m}) 闭式 == B 关系式")

# 手算：C(0.5,1) = rho = 0.5；C(1,2) = 0.2/(1-0.5+0.1) = 1/3
ck(close(erlang_c(0.5, 1), 0.5), "C(0.5,1)=0.5")
ck(close(erlang_c(1.0, 2), 1.0 / 3.0), "C(1,2)=1/3")
ck(close(erlang_c_via_b(1.0, 2), 1.0 / 3.0), "C(1,2) 关系式同值")

# Erlang C 单调性
prev = 1.0
for m in range(5, 12):
    cur = erlang_c(4.0, m)
    ck(cur < prev, f"C(4,{m}) 随 m 递减")
    prev = cur
prev = 0.0
for E in (1.0, 2.0, 3.0, 4.0):
    cur = erlang_c(E, 5)
    ck(cur > prev, f"C({E},5) 随 E 递增")
    prev = cur

# 不稳定必须报错
try:
    erlang_c(2.0, 2)
    ck(False, "E>=m 应报不稳定")
except ValueError:
    ck(True, "E>=m 报 ValueError")

# --------------------------------------------------------------- 3. M/M/c
mmc = MMC(lam=8, mu=5, c=2)     # E = 1.6 erlang, rho = 0.8
ck(close(mmc.offered_load, 1.6), "E = lambda/mu = 1.6")
ck(close(mmc.rho, 0.8), "rho = lambda/(c*mu) = 0.8")
ck(close(mmc.C, erlang_c(1.6, 2)), "M/M/c 的 C 就是 Erlang C")
# Little 定律在 M/M/c 上仍精确成立
ck(close(mmc.lam * mmc.W, mmc.L), "M/M/c: lambda*W == L")
ck(close(mmc.lam * mmc.Wq, mmc.Lq), "M/M/c: lambda*Wq == Lq")
ck(close(mmc.W, mmc.Wq + 1.0 / mmc.mu), "M/M/c: W = Wq + 1/mu")
ck(close(mmc.busy_servers, mmc.L - mmc.Lq), "忙碌服务台数 = L - Lq")
ck(close(mmc.busy_servers, 1.6), "忙碌服务台数 = E = 1.6")
# pi0 与状态概率归一（数值求和到足够多项）
# 逐项递推求尾项：k >= c 时 pi_k = pi_{k-1} * rho（直接算幂会溢出）
ps = [mmc.pi0 * (mmc.c * mmc.rho) ** k / math.factorial(k) for k in range(mmc.c)]
pi_c = mmc.pi0 * (mmc.c * mmc.rho) ** mmc.c / math.factorial(mmc.c)
tail, term = 0.0, pi_c
for _ in range(4000):
    tail += term
    term *= mmc.rho
    if term < 1e-18 and tail > 0.999:
        break
ck(close(sum(ps) + tail, 1.0, tol=1e-6), "M/M/c 状态概率归一")
# pi0 与 Erlang C 的一致性：C = (c*rho)^c/(c!(1-rho)) * pi0
rhs = ((mmc.c * mmc.rho) ** mmc.c / math.factorial(mmc.c)
       / (1 - mmc.rho)) * mmc.pi0
ck(close(mmc.C, rhs), "C 可由 pi0 推出")

# c=1 必须退化为 M/M/1
one = MMC(lam=500, mu=1000, c=1)
ck(close(one.rho, 0.5), "c=1: rho=0.5")
ck(close(one.L, 1.0), "c=1: L = rho/(1-rho) = 1")
ck(close(one.W, 0.002), "c=1: W = 1/(mu-lambda) = 2ms")
ck(close(one.Lq, 0.5), "c=1: Lq = 0.5")
ck(close(one.Wq, 0.001), "c=1: Wq = 1ms")
ck(close(one.C, 0.5), "c=1: C = rho = 0.5")

# 同样总能力：c 台慢的 vs 1 台快的，快的平均逗留更短
for c in (2, 4, 8):
    w_mmc, w_fast = compare_configurations(lam=c * 4, mu=5, c=c)
    ck(w_fast < w_mmc, f"c={c}: 一台快的 W 小于 c 台慢的")
    ck(close(w_fast, 1.0 / (c * 5 - c * 4)), f"c={c}: 快机解析式")

# ------------------------------------------------------------- 4. M/G/1 P-K
lam, mu = 500, 1000
var_exp = exp_service_var(mu)
Wq_pk, W_pk, L_pk = mg1(lam, mu, var_exp)
ck(close(Wq_pk, 0.001), "指数服务下 P-K 的 Wq == M/M/1 的 1ms")
ck(close(W_pk, 0.002), "指数服务下 P-K 的 W == M/M/1 的 2ms")
ck(close(L_pk, mg1_L_via_pk(lam, mu, var_exp)), "P-K 两条 L 式一致")
ck(close(L_pk, 1.0), "指数服务下 L == 1")

# M/D/1：方差为 0，排队等待恰为 M/M/1 的一半
Wq_d, W_d, L_d = mg1(lam, mu, 0.0)
ck(close(Wq_d, 0.0005), "M/D/1 的 Wq 是 M/M/1 的一半")
ck(close(Wq_d * 2, Wq_pk), "M/D/1 vs M/M/1 排队等待 2:1")

# 高方差：Var(S) = 9/mu^2 -> Wq = (rho+9rho)/(2(mu-lambda)) = 5 倍
Wq_hi, _, _ = mg1(lam, mu, 9.0 / (mu * mu))
ck(close(Wq_hi, 5 * Wq_pk), "Var=9/mu^2 时 Wq 是 M/M/1 的 5 倍")
ck(close(Wq_hi, 0.005), "Var=9/mu^2 时 Wq = 5ms")

# 方差越大越差；M/D/1 是同 rho 下的下界
prev = -1.0
for v in (0.0, 0.5, 1.0, 4.0, 9.0):
    cur = mg1(lam, mu, v / (mu * mu))[0]
    ck(cur > prev, f"Wq 随 Var 递增（v={v}）")
    prev = cur

try:
    mg1(1000, 1000, 0.0)
    ck(False, "rho=1 应报错")
except ValueError:
    ck(True, "M/G/1 rho>=1 报错")

# ------------------------------------------------------------- 5. M/M/1/K
k = MM1K(lam=500, mu=1000, K=5)
ck(close(k.rho, 0.5), "M/M/1/K: rho=0.5")
ck(close(k.pi0, (1 - 0.5) / (1 - 0.5 ** 6)), "pi0 有限等比和")
ck(close(sum(k.pi(i) for i in range(6)), 1.0, tol=1e-12), "状态概率归一")
ck(close(k.lambda_a, k.throughput), "有效到达率 == 离开率（流量守恒）")
ck(close(k.lambda_a, 500 * (1 - k.pi(5))), "lambda_a = lambda(1-p_K)")
ck(k.lambda_a < k.mu, "有效吞吐不超过服务率")

# K 越大，阻塞越少、L 越大，并收敛到 M/M/1
prev_block = 1.0
prev_L = -1.0
for K in (1, 2, 5, 10, 20, 40):
    kk = MM1K(500, 1000, K)
    ck(kk.p_block < prev_block, f"K={K} 阻塞率递减")
    ck(kk.L > prev_L, f"K={K} 系统内数量递增")
    ck(close(kk.lambda_a, kk.throughput), f"K={K} 流量守恒")
    prev_block, prev_L = kk.p_block, kk.L
big = MM1K(500, 1000, 200)
ck(close(big.L, 1.0, tol=1e-6), "K=200 时 L 收敛到 M/M/1 的 1.0")
ck(close(big.W, 0.002, tol=1e-6), "K=200 时 W 收敛到 M/M/1 的 2ms")

# rho = 1 的边界：pi0 = 1/(K+1)，各状态等概率
k1 = MM1K(lam=1000, mu=1000, K=4)
ck(close(k1.pi0, 0.2), "rho=1 时 pi0 = 1/(K+1)")
ck(all(close(k1.pi(i), 0.2) for i in range(5)), "rho=1 时五态等概率")
ck(close(k1.p_block, 0.2), "rho=1 时丢弃率 = 1/(K+1)")

print(f"assertions: {N}, failed: {len(FAILED)}")
for f in FAILED:
    print("FAILED:", f)
sys.exit(1 if FAILED else 0)
