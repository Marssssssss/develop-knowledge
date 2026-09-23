"""Little 定律与 M/M/1 自检：全部实跑，断言基于手算/官方公式。"""

import math
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from little import (  # noqa: E402
    MM1,
    dead_queue,
    knee_table,
    little_l,
    pool_size,
    simulate_mm1,
    throughput_ceiling,
    utilization,
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


# ---------------------------------------------------------- 1. Little 定律三用
ck(little_l(lam=500, W=0.2) == 100.0, "L = 500*0.2 = 100")
ck(close(little_l(L=50, lam=100), 0.5), "W = 50/100 = 0.5s")
ck(close(little_l(L=100, W=0.2), 500.0), "lambda = 100/0.2 = 500")

# 隐性等待：L=50, lambda=100rps -> W=0.5s，服务时间 50ms => 450ms 在排队
ck(close(little_l(L=50, lam=100) - 0.05, 0.45), "隐性排队 450ms")

# 吞吐上限：S=10ms 单 worker -> 100/s
ck(close(throughput_ceiling(0.01), 100.0), "S=10ms 单 worker 上限 100/s")
ck(close(throughput_ceiling(0.01, workers=4), 400.0), "4 worker 上限 400/s")

# 利用率定律 U = lambda*S
ck(close(utilization(50, 0.01), 0.5), "U = 50*0.01 = 0.5")

# ------------------------------------------------------------------ 2. M/M/1
m = MM1(lam=500, mu=1000)          # S = 1ms, rho = 0.5
ck(close(m.rho, 0.5), "rho = 0.5")
ck(close(m.L, 1.0), "L = rho/(1-rho) = 1")
ck(close(m.Lq, 0.5), "Lq = rho^2/(1-rho) = 0.5")
ck(close(m.W, 0.002), "W = 1/(mu-lam) = 2ms")
ck(close(m.Wq, 0.001), "Wq = rho/(mu-lam) = 1ms")

# Little 定律在 M/M/1 上必须精确成立（这是代数恒等式，不是数值巧合）
ck(close(m.lam * m.W, m.L), "M/M/1: lambda*W == L")
ck(close(m.lam * m.Wq, m.Lq), "M/M/1: lambda*Wq == Lq")
ck(close(m.W, m.Wq + m.S), "W = Wq + S")
ck(close(m.L, m.Lq + m.rho), "L = Lq + rho")
ck(close(m.Lq, m.rho * m.L), "Lq = rho * L")

# 状态概率 pi_k = (1-rho)rho^k，且求和为 1
s = sum(m.state_prob(k) for k in range(4000))
ck(close(s, 1.0, tol=1e-6), "pi_k 求和为 1")
ck(close(m.state_prob(0), 0.5), "pi_0 = 1-rho = 0.5")
ck(close(m.state_prob(1), 0.25), "pi_1 = 0.25")

# 稳定性条件：rho >= 1 必须报错
for bad_lam in (1000, 2000):
    try:
        MM1(lam=bad_lam, mu=1000)
        ck(False, f"rho={bad_lam/1000} 应判不稳定")
    except ValueError:
        ck(True, "rho>=1 报 ValueError")
try:
    MM1(lam=-1, mu=1000)
    ck(False, "负到达率应报错")
except ValueError:
    ck(True, "负到达率报 ValueError")

# ---------------------------------------------------------------- 3. 拐点表
tab = {r: (mul, w) for r, mul, w in knee_table(S=0.1)}
for rho, mul in ((0.1, 1.111111), (0.5, 2.0), (0.7, 3.333333), (0.8, 5.0),
                 (0.9, 10.0), (0.95, 20.0), (0.99, 100.0)):
    ck(close(tab[rho][0], mul, tol=1e-5), f"rho={rho} 倍率 {mul}")
ck(close(tab[0.9][1], 1.0), "S=100ms,rho=0.9 -> W=1s")
ck(close(tab[0.99][1], 10.0), "S=100ms,rho=0.99 -> W=10s")

# 70% -> 80% 多付 50% 延迟；90% -> 95% 再翻倍
ck(close(tab[0.8][1] / tab[0.7][1], 1.5, tol=1e-6), "80%/70% = 1.5")
ck(close(tab[0.95][1] / tab[0.9][1], 2.0, tol=1e-6), "95%/90% = 2.0")

# -------------------------------------------------------------- 4. 死队列
wasted, useful = dead_queue(10000, 500, 2.0)
ck((wasted, useful) == (9000, 1000), "10000 深、500/s、2s 超时 -> 9000 作废")
ck(close(dead_queue(10000, 500, 2.0)[1] / 500, 2.0), "首个作废请求恰好等到 2s")
# 恰好整除边界：depth/drain == timeout 时该元素仍算有效（<=）
ck(dead_queue(1000, 500, 2.0) == (0, 1000), "1000 深 2s 超时 -> 全有效")
ck(dead_queue(1001, 500, 2.0) == (1, 1000), "1001 深 -> 1 个作废")
ck(dead_queue(10, 1, 100) == (0, 10), "排空远快于超时 -> 全有效")

# ------------------------------------------------------------- 5. 池子尺寸
ck(pool_size(500, 0.2) == 100, "500rps x 200ms -> 100")
ck(pool_size(500, 0.2, headroom=1.5) == 150, "1.5 倍余量 -> 150")

# --------------------------------------------------------- 6. 事件驱动仿真
L_hat, lam_hat, W_hat, n_dep = simulate_mm1(500, 1000, t_end=20000.0)
ck(n_dep > 100000, f"样本量足够（{n_dep}）")
# Little 定律在仿真上成立（统计误差）
rel = abs(L_hat - lam_hat * W_hat) / L_hat
ck(rel < 0.02, f"仿真 Little 定律相对误差 {rel:.5f} < 2%")
# 仿真值逼近解析值
ck(abs(L_hat - 1.0) < 0.08, f"仿真 L_hat={L_hat:.4f} 逼近 1.0")
ck(abs(W_hat - 0.002) / 0.002 < 0.08, f"仿真 W_hat={W_hat:.6f} 逼近 2ms")
ck(abs(lam_hat - 500) / 500 < 0.02, f"仿真 lambda_hat={lam_hat:.2f} 逼近 500")

# 换个负载点再验一次：rho=0.8 -> L=4, W=5ms
L2, lam2, W2, n2 = simulate_mm1(800, 1000, t_end=20000.0)
ck(abs(L2 - 4.0) < 0.6, f"rho=0.8 仿真 L={L2:.3f} 逼近 4")
ck(abs(L2 - lam2 * W2) / L2 < 0.03, f"rho=0.8 Little 误差 {abs(L2-lam2*W2)/L2:.5f}")

# 仿真确定性：同种子两次结果必须完全一致
a = simulate_mm1(500, 1000, t_end=3000.0, seed=7)
b = simulate_mm1(500, 1000, t_end=3000.0, seed=7)
ck(a == b, "同种子仿真可复现")

print(f"assertions: {N}, failed: {len(FAILED)}")
for f in FAILED:
    print("FAILED:", f)
sys.exit(1 if FAILED else 0)
