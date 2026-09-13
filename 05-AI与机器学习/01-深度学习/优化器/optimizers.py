# -*- coding: utf-8 -*-
"""优化器演进:SGD → Momentum → RMSProp → Adam(Kingma & Ba 2014, arXiv:1412.6980)从零实现。

Adam Algorithm 1(原文逐行对应):
  t <- t+1;  g = grad
  m = b1*m + (1-b1)*g                       # 偏一阶矩
  v = b2*v + (1-b2)*g^2                     # 偏二阶矩
  m_hat = m / (1 - b1^t)                    # 偏差校正
  v_hat = v / (1 - b2^t)
  theta = theta - alpha * m_hat / (sqrt(v_hat) + eps)   # 默认 0.001/0.9/0.999/1e-8

自测(python optimizers.py):
1. 偏差校正冷启动:常数梯度下第一步 |update| ≈ alpha(与梯度尺度无关)
2. 梯度尺度不变性:梯度 ×1000,轨迹几乎不变(除以 sqrt(v) 抵消)
3. 病态二次型 f = 0.002x^2 + 50y^2(曲率比 25000:1):Adam 迭代次数 << SGD
4. SGD lr 稍超最陡方向稳定上限(0.02)即发散
"""
import numpy as np


# ---------------------------------------------------------------- 四种优化器
class SGD:
    def __init__(self, lr):
        self.lr = lr

    def step(self, theta, grad):
        return theta - self.lr * grad


class Momentum:
    """Polyak heavy ball: v = mu*v + g; theta -= lr*v。"""

    def __init__(self, lr, mu=0.9):
        self.lr, self.mu, self.v = lr, mu, None

    def step(self, theta, grad):
        if self.v is None:
            self.v = np.zeros_like(theta)
        self.v = self.mu * self.v + grad
        return theta - self.lr * self.v


class RMSProp:
    """Tieleman & Hinton 2012: v = rho*v + (1-rho)*g^2; theta -= lr*g/(sqrt(v)+eps)。"""

    def __init__(self, lr, rho=0.9, eps=1e-8):
        self.lr, self.rho, self.eps, self.v = lr, rho, eps, None

    def step(self, theta, grad):
        if self.v is None:
            self.v = np.zeros_like(theta)
        self.v = self.rho * self.v + (1 - self.rho) * grad * grad
        return theta - self.lr * grad / (np.sqrt(self.v) + self.eps)


class Adam:
    """Kingma & Ba 2014 Algorithm 1 逐行实现(默认 alpha=1e-3, b1=0.9, b2=0.999, eps=1e-8)。"""

    def __init__(self, lr=1e-3, b1=0.9, b2=0.999, eps=1e-8):
        self.lr, self.b1, self.b2, self.eps = lr, b1, b2, eps
        self.m = None
        self.v = None
        self.t = 0

    def step(self, theta, grad):
        if self.m is None:
            self.m = np.zeros_like(theta)
            self.v = np.zeros_like(theta)
        self.t += 1
        self.m = self.b1 * self.m + (1 - self.b1) * grad
        self.v = self.b2 * self.v + (1 - self.b2) * grad * grad
        m_hat = self.m / (1 - self.b1 ** self.t)
        v_hat = self.v / (1 - self.b2 ** self.t)
        return theta - self.lr * m_hat / (np.sqrt(v_hat) + self.eps)


# ---------------------------------------------------------------- 实验与自测
def f(x, y):
    """病态二次型:x 曲率 0.004 / y 曲率 100(比 25000:1)。最优解 (0, 0)。

    SGD 稳定性由最陡方向决定:lr < 2/100 = 0.02,于是平缓 x 方向每步
    只前进 lr*0.004*|x|,收敛极慢 —— 自适应优化器的用武之地。
    """
    return 0.002 * x * x + 50.0 * y * y


def grad_f(x, y):
    return np.array([0.004 * x, 100.0 * y])


def optimize(opt, theta0, iters, scale=1.0):
    theta = theta0.copy()
    for _ in range(iters):
        theta = opt.step(theta, grad_f(*theta) * scale)
    return theta


def iters_to_converge(opt_ctor, theta0, threshold=1e-6, max_iters=20000):
    opt = opt_ctor()
    theta = theta0.copy()
    for i in range(1, max_iters + 1):
        theta = opt.step(theta, grad_f(*theta))
        if f(*theta) < threshold:
            return i
    return -1


def self_test():
    theta0 = np.array([-8.0, 4.0])

    # 1. 偏差校正冷启动:常数梯度 g 下第一步 |Δ| ≈ lr(与梯度尺度无关)
    for g_scale, lr in ((1.0, 0.1), (1e-3, 0.1), (1e3, 0.1)):
        adam = Adam(lr=lr)
        th = np.array([1.0])
        th_new = adam.step(th, np.array([g_scale]))  # t=1: m_hat=g, v_hat=g^2
        step = abs(th_new[0] - th[0])
        expected = lr * g_scale / (abs(g_scale) + 1e-8)
        assert abs(step - expected) < 1e-10, (step, expected)
    print("[1] 冷启动第一步 |Δ| = lr·g/(|g|+eps) ≈ lr·sign(g), 与梯度尺度无关 PASS")

    # 2. 梯度尺度不变性:grad ×1000, 轨迹几乎重合(v 开方抵消尺度)
    a1 = Adam(lr=0.05)
    a2 = Adam(lr=0.05)
    t1, t2 = theta0.copy(), theta0.copy()
    for _ in range(50):
        t1 = a1.step(t1, grad_f(*t1))
        t2 = a2.step(t2, grad_f(*t2) * 1000.0)
    rel = np.abs(t1 - t2).max() / (np.abs(t1).max() + 1e-12)
    assert rel < 1e-3, f"尺度不变性偏差 {rel:.2e}"
    print(f"[2] 梯度尺度不变性: ×1000 后 50 步偏差 {rel:.2e} PASS")

    # 3+4. 病态二次型对比:SGD 的 lr 上限受最陡方向约束,平缓方向极慢
    ctors = [
        ("SGD      lr=0.019", lambda: SGD(0.019)),  # lr>0.02 时 y 方向发散
        ("Momentum lr=0.03 ", lambda: Momentum(0.03)),
        ("RMSProp  lr=0.05", lambda: RMSProp(0.05)),
        ("Adam     lr=0.05", lambda: Adam(0.05)),
    ]
    print(f"== 病态二次型 f=0.002x²+50y², 起点 {theta0.tolist()}, 阈值 f<0.05 ==")
    results = {}
    for name, ctor in ctors:
        n = iters_to_converge(ctor, theta0, threshold=0.05)
        results[name] = n
        print(f"  {name}: {n if n > 0 else '>20000'} 步")
    adam_n = results["Adam     lr=0.05"]
    sgd_n = results["SGD      lr=0.019"]
    assert 0 < adam_n < 1000, f"Adam 应在千步内收敛, 实测 {adam_n}"
    assert sgd_n > 3000, f"SGD 应数千步起步, 实测 {sgd_n}"
    assert adam_n < sgd_n / 3, f"Adam 应显著快于 SGD: {adam_n} vs {sgd_n}"
    # SGD 发散验证:lr 稍超 0.02 即在最陡方向发散
    diverged = f(*optimize(SGD(0.021), theta0, 200)) > 1e6
    assert diverged
    print("  SGD lr=0.021 时 y 方向发散(f>1e6)— lr 上限受最陡方向约束 PASS")
    print(f"  结论: Adam {adam_n} 步 vs SGD {sgd_n} 步 (约 {sgd_n // adam_n} 倍差距)")
    print("\nself_test: 全部 PASS")


if __name__ == "__main__":
    self_test()
