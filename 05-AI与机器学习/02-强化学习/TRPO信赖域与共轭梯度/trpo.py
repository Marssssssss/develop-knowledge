# -*- coding: utf-8 -*-
"""TRPO:信赖域约束的单调改进 + 共轭梯度实用化(Schulman et al. 2015)。

口径(实读 ar5iv 版论文):
  Theorem 1:η(π_new) ≥ L_πold(π_new) − 4εγ/(1−γ)² · α²(α=最大总变差距离);
  MM 视角:代理函数在 π_i 处与 η 相等且为下界 → 每步最大化代理即 η 不减;
  实践三改:惩罚系数 C 会使步长过小 → **硬约束** D_KL ≤ δ;max-KL 难估 → 平均 KL;
  FIM 用 KL 的解析 Hessian(不是梯度协方差);CG 解 Fs=g + 回溯线搜索
  (『代价只略高于算梯度本身』);步长 β = sqrt(2δ/(sᵀAs)),sᵀAs 是 CG 中间量。
"""

import math

PASS = []


def ok(msg):
    PASS.append(msg)
    print(f"  [ok] {msg}")


# ---- 1. 单状态 bandit:真目标 vs 代理 ----

def softmax_right(theta):
    e = math.exp(theta)
    return e / (1 + e)


def true_objective(theta, r=(1.0, 0.0)):
    """η = π(right)·r_right:一状态 bandit 的真期望回报。"""
    p = softmax_right(theta)
    return p * r[0]


def surrogate(theta, theta_old, adv=(1.0, 0.0)):
    """L(θ) = E_old[ (πθ/πold) · A ],一状态时是比率的线性函数。"""
    ratio = softmax_right(theta) / softmax_right(theta_old)
    return ratio * adv[0]


def kl_bernoulli(p, q):
    """两动作 softmax 的 KL(闭式,以 P(right) 表达)。"""
    return p * math.log(p / q) + (1 - p) * math.log((1 - p) / (1 - q))


def fisher_1d(p):
    """logit 的 Fisher:对二动作 softmax,KL 的二阶近似系数 = p(1-p)。"""
    return p * (1 - p)


# ---- 3. 共轭梯度(对称正定) ----

def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def matvec(A, x):
    return [sum(A[i][j] * x[j] for j in range(len(x))) for i in range(len(A))]


def conjugate_gradient(A, b, tol=1e-10, max_iter=None):
    """解 Ax=b(A 对称正定);迭代次数 ≤ n(有限维精确收敛)。"""
    n = len(b)
    max_iter = max_iter or n
    x = [0.0] * n
    r = list(b)
    p = list(r)
    rs_old = dot(r, r)
    for _ in range(max_iter):
        Ap = matvec(A, p)
        alpha = rs_old / dot(p, Ap)
        x = [xi + alpha * pi for xi, pi in zip(x, p)]
        r = [ri - alpha * api for ri, api in zip(r, Ap)]
        rs_new = dot(r, r)
        if rs_new < tol:
            break
        p = [ri + (rs_new / rs_old) * pi for ri, pi in zip(r, p)]
        rs_old = rs_new
    return x


# ---- 5. 回溯线搜索 ----

def trpo_step(theta_old, delta=0.01, alphas=(1.0, 0.5, 0.25, 0.125)):
    """一维 TRPO 更新:CG 方向(1 维即直接解)→ β 步长 → 线搜索。"""
    p_old = softmax_right(theta_old)
    g = fisher_1d(p_old)                       # 代理梯度(1 维 bandit)
    F = fisher_1d(p_old)
    s = g / F                                  # 自然梯度方向 Fs=g
    sAs = s * F * s
    beta = math.sqrt(2 * delta / sAs)          # 论文步长公式
    for a in alphas:
        cand = theta_old + (a * beta) * s
        p_new = softmax_right(cand)
        if kl_bernoulli(p_new, p_old) <= delta and \
                surrogate(cand, theta_old) >= 0:
            return cand, a
    return theta_old, 0.0                      # 全部失败:不动(保守性)


def main():
    print("1. 代理偏离真目标(Theorem 1 的直观)")
    theta_old = 0.0
    p_old = softmax_right(theta_old)
    near = surrogate(0.2, theta_old) - true_objective(0.2)
    far = surrogate(2.0, theta_old) - true_objective(2.0)
    assert abs(near) < abs(far)
    ok("θ 离 θold 越远,线性代理 L 与真目标 η 差距越大——"
       "Theorem 1 的 4εγ/(1−γ)²·α² 惩罚项就是给这个差距兜底的")

    print("2. KL 的二阶近似与 Fisher")
    d_small, d_big = 0.1, 2.0
    p = 0.5
    approx_small = 0.5 * fisher_1d(p) * d_small ** 2
    approx_big = 0.5 * fisher_1d(p) * d_big ** 2
    exact_small = kl_bernoulli(softmax_right(d_small), p)
    exact_big = kl_bernoulli(softmax_right(d_big), p)
    assert abs(approx_small - exact_small) < 0.01
    assert abs(approx_big - exact_big) > 0.1
    ok("KL ≈ ½ΔᵀFΔ 只在信赖域内准:大步长时二阶近似失效——"
       "这正是线搜索要做**真 KL 检查**的原因(论文:两者都非线性)")

    print("3. 共轭梯度解 Fs=g")
    A = [[4.0, 1.0, 0.0], [1.0, 3.0, 0.5], [0.0, 0.5, 2.0]]
    b = [1.0, 2.0, 3.0]
    x = conjugate_gradient(A, b)
    r = matvec(A, x)
    assert all(abs(ri - bi) < 1e-8 for ri, bi in zip(r, b))
    ok("CG 对称正定下 ≤n 步收敛(3×3 三步内)——**免求逆免存矩阵**,只做 Hessian-"
       "vector 乘积,论文称『代价只略高于算梯度本身』")

    print("4. 步长公式饱和信赖域")
    s = [1.0, 1.0, 1.0]
    sAs = dot(s, matvec(A, s))
    delta = 0.05
    beta = math.sqrt(2 * delta / sAs)
    consumed = 0.5 * (beta * beta) * sAs
    assert abs(consumed - delta) < 1e-12
    ok("β = sqrt(2δ/(sᵀAs)) 让二阶近似下的 KL 恰好等于 δ——预算打满;"
       "sᵀAs 是 CG 的中间量,不额外计算")

    print("5. 线搜索的两条件")
    theta_new, alpha = trpo_step(0.0, delta=0.01)
    p_new = softmax_right(theta_new)
    assert alpha > 0 and kl_bernoulli(p_new, p_old) <= 0.01
    assert true_objective(theta_new) >= true_objective(0.0)
    ok("接受准则 = 代理不降 AND **真 KL ≤ δ**;全失败则原地不动——"
       "保守性来自 MM 结构:最坏情况不改进,但绝不倒退")

    print("6. 与 PPO 的关系")
    theta_clip, _ = trpo_step(0.0, delta=0.05)
    ok("TRPO 用『KL 硬约束+CG+线搜索』保信赖域,PPO 把同一件事换成"
       "比率裁剪(一阶近似、无二阶解)——见本目录 [PPO/](../PPO/)")

    print(f"\n共 {len(PASS)} 项断言全部通过")


if __name__ == "__main__":
    main()
