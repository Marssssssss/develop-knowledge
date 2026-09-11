"""demo 033 — 反向传播 (Backpropagation) 链式法则 — 1-hidden-layer MLP 最小实现

依据：
  - d2l.ai §5.3 Forward/Backward Propagation and Computational Graphs
    (https://en.d2l.ai/chapter_multilayer-perceptrons/backprop.html)
    公式 (5.3.1) ~ (5.3.14) 完整给出 1 隐藏层 MLP 的前向 + 反向推导

包含：
  1. NumPy 手写前向 (linear → ReLU → linear → softmax cross-entropy + L2 reg)
  2. 反向按 d2l.ai §5.3.3 链式法则逐节点求 ∂J/∂W1, ∂J/∂W2
  3. 数值梯度 (中心差分) 与解析梯度对比,误差 < 1e-7
  4. mini-batch SGD 训练 200 步验证学习能力
"""

import numpy as np


# ============================================================
# 1. 前向计算 (对应 d2l.ai 公式 5.3.1 ~ 5.3.6)
# ============================================================
def linear_forward(X, W):
    """X: (n, d_in) × W: (d_out, d_in) → Z: (n, d_out)"""
    return X @ W.T


def relu_forward(Z):
    return np.maximum(Z, 0.0)


def softmax_cross_entropy_forward(O, y):
    """O: (n, q) logits, y: (n,) int labels
    返回 (loss L, probability matrix P)"""
    O_max = O.max(axis=1, keepdims=True)
    exp = np.exp(O - O_max)
    P = exp / exp.sum(axis=1, keepdims=True)
    n = len(y)
    L = -np.log(P[np.arange(n), y]).mean()
    return L, P


def l2_reg(W_list, lam):
    """公式 5.3.5: s = λ/2 * (||W1||_F^2 + ||W2||_F^2)"""
    return 0.5 * lam * sum(np.sum(W * W) for W in W_list)


# ============================================================
# 2. MLP 主体 (前向 + 反向)
# ============================================================
class MLP:
    """1 隐藏层 MLP,对应 d2l.ai §5.3 的简化模型 (无 bias,L2 正则)"""

    def __init__(self, d_in, d_hidden, d_out, lam=0.0, seed=0):
        rng = np.random.default_rng(seed)
        self.W1 = rng.normal(0, 0.01, size=(d_hidden, d_in))   # 公式 5.3.1 隐藏层权重
        self.W2 = rng.normal(0, 0.01, size=(d_out, d_hidden))   # 公式 5.3.3 输出层权重
        self.lam = lam
        self.cache = {}

    def forward(self, X, y):
        # 公式 5.3.1 ~ 5.3.6
        Z = linear_forward(X, self.W1)          # 隐藏层线性: z = W1 x
        H = relu_forward(Z)                     # 隐藏层激活: h = φ(z)
        O = linear_forward(H, self.W2)          # 输出层线性: o = W2 h
        L, P = softmax_cross_entropy_forward(O, y)  # 公式 5.3.4 损失项
        s = l2_reg([self.W1, self.W2], self.lam)     # 公式 5.3.5 L2 正则项
        J = L + s                              # 公式 5.3.6 目标函数

        # 缓存中间变量供反向使用 (d2l.ai §5.3.4: 必须保留中间值)
        self.cache.update(X=X, Z=Z, H=H, O=O, P=P, y=y, L=L, s=s, J=J)
        return J

    def backward(self):
        """按 d2l.ai §5.3.3 链式法则反向求梯度"""
        c = self.cache
        n = len(c['y'])

        # 公式 5.3.8: ∂J/∂L = 1, ∂J/∂s = 1 (略, 直接用最终梯度)
        # 公式 5.3.9: ∂J/∂o = ∂L/∂o = (P - y_onehot) / n
        y_onehot = np.zeros_like(c['P'])
        y_onehot[np.arange(n), c['y']] = 1
        dO = (c['P'] - y_onehot) / n                       # (n, d_out)

        # 公式 5.3.10 + 5.3.11: ∂J/∂W2 = ∂J/∂o · h^T + λ W2
        dW2 = dO.T @ c['H'] + self.lam * self.W2

        # 公式 5.3.12: ∂J/∂h = W2^T · ∂J/∂o
        # 公式 5.3.13: ∂J/∂z = (∂J/∂h) ⊙ φ'(z), φ=ReLU → φ'(z) = 1[z>0]
        dZ = (self.W2.T @ dO.T).T * (c['Z'] > 0).astype(float)

        # 公式 5.3.14: ∂J/∂W1 = ∂J/∂z · x^T + λ W1
        dW1 = dZ.T @ c['X'] + self.lam * self.W1

        return dW1, dW2


# ============================================================
# 3. 数值梯度 (中心差分) — 用于验证解析梯度正确性
# ============================================================
def numerical_grad(W, f, eps=1e-6):
    """中心差分: ∂f/∂W[i] ≈ (f(W[i]+ε) - f(W[i]-ε)) / (2ε)"""
    W_flat = W.flatten()
    grad = np.zeros_like(W_flat)
    for i in range(len(W_flat)):
        W_flat[i] += eps
        fp = f()
        W_flat[i] -= 2 * eps
        fm = f()
        W_flat[i] += eps
        grad[i] = (fp - fm) / (2 * eps)
    return grad.reshape(W.shape)


# ============================================================
# 4. 演示
# ============================================================
def main():
    rng = np.random.default_rng(42)
    n, d_in, d_hidden, d_out = 32, 10, 16, 4
    X = rng.normal(0, 1, (n, d_in)).astype(np.float64)
    W_true = rng.normal(0, 1, (d_out, d_in))
    y = (X @ W_true.T).argmax(axis=1)

    net = MLP(d_in, d_hidden, d_out, lam=0.0, seed=0)

    # ============ 数值梯度验证 ============
    print("=" * 60)
    print("Test 1: 数值梯度 vs 解析梯度 (单样本, lam=0)")
    print("=" * 60)
    X1, y1 = X[:1], y[:1]

    def f_W1():
        return net.forward(X1, y1)

    net.forward(X1, y1)                          # 触发缓存
    dW1_ana, dW2_ana = net.backward()
    dW1_num = numerical_grad(net.W1, f_W1)
    dW2_num = numerical_grad(net.W2, f_W1)

    err1 = np.abs(dW1_ana - dW1_num).max()
    err2 = np.abs(dW2_ana - dW2_num).max()
    print(f"  dW1 max |ana - num| = {err1:.2e}")
    print(f"  dW2 max |ana - num| = {err2:.2e}")
    print(f"  dW1[0,0] ana={dW1_ana[0,0]:.6f}  num={dW1_num[0,0]:.6f}")
    print(f"  dW2[0,0] ana={dW2_ana[0,0]:.6f}  num={dW2_num[0,0]:.6f}")
    assert err1 < 1e-7 and err2 < 1e-7, "梯度验证失败!"

    # ============ L2 正则项梯度验证 ============
    print()
    print("=" * 60)
    print("Test 2: L2 正则项 (λ=0.5) 梯度验证")
    print("=" * 60)
    net.lam = 0.5
    net.forward(X1, y1)
    dW1_ana, dW2_ana = net.backward()

    def f_W1_reg():
        return net.forward(X1, y1)

    dW1_num = numerical_grad(net.W1, f_W1_reg)
    dW2_num = numerical_grad(net.W2, f_W1_reg)
    err1 = np.abs(dW1_ana - dW1_num).max()
    err2 = np.abs(dW2_ana - dW2_num).max()
    print(f"  λ=0.5 时 dW1 max err = {err1:.2e}")
    print(f"  λ=0.5 时 dW2 max err = {err2:.2e}")
    assert err1 < 1e-7 and err2 < 1e-7

    # ============ mini-batch SGD 训练 ============
    print()
    print("=" * 60)
    print("Test 3: mini-batch SGD 训练 200 步 (lam=0.01, lr=0.5)")
    print("=" * 60)
    net.lam = 0.01
    lr = 0.5
    for step in range(200):
        idx = rng.choice(n, 16, replace=False)
        net.forward(X[idx], y[idx])
        dW1, dW2 = net.backward()
        net.W1 -= lr * dW1
        net.W2 -= lr * dW2
        if step % 50 == 0:
            acc = (net.cache['P'].argmax(axis=1) == y[idx]).mean()
            print(f"  step {step:3d}: J={net.cache['J']:.4f}  train_acc={acc:.2f}")

    net.forward(X, y)
    acc_full = (net.cache['P'].argmax(axis=1) == y).mean()
    print(f"\n  200 步后 full train acc = {acc_full:.2f}")

    # ============ 总结: 前向 vs 反向的计算/内存对比 ============
    print()
    print("=" * 60)
    print("Test 4: 前向 vs 反向内存开销")
    print("=" * 60)
    # 1 隐藏层 MLP 需要保留: X, Z, H, O, P  →  5 个中间张量
    print(f"  缓存张量数 = 5 (X, Z, H, O, P)")
    print(f"  预测时只需 W1, W2 + 当前输入 → 不需要保留中间变量")
    print(f"  这是 d2l.ai §5.3.4 '训练比推理需要显著更多内存' 的根本原因")


if __name__ == "__main__":
    main()
