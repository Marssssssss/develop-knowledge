"""demo 034 — 2D 卷积 (im2col) forward + backward

依据:
  - arXiv 2408.12561 ssProp paper §Preliminaries (Eq 3, 4, 5)
    https://arxiv.org/html/2408.12561v1
    im2col 把卷积 → GEMM,反向用 col2im 把梯度散回输入
  - arXiv 2009.00612 Operational vs Convolutional (Eq 2, 7, 8, 9, 10)
    Y 矩阵的 Hadamard 形式 + 显式 ψ/φ 函数分解
  - Stanford CS231n 作业: https://cs231n.github.io/convolutional-networks/

包含:
  1. im2col / col2im 用 np.lib.stride_tricks 实现 (零拷贝 view)
  2. Conv2D forward: im2col + W reshape + GEMM + bias
  3. Conv2D backward: dW = cols.T @ dY_col, db = sum, dX = col2im
  4. 数值梯度 (中心差分) 验证 dW < 1e-7 误差
"""

import numpy as np


# ============================================================
# 1. im2col / col2im (向量化,零拷贝视图)
# ============================================================
def im2col(X, kh, kw, stride, pad):
    """X: (N, C, H, W) → cols: (C*kh*kw, N*Hout*Wout)
    通过 np.lib.stride_tricks.as_strided 直接构造窗口视图,不复制数据。
    """
    N, C, H, W = X.shape
    Hout = (H + 2 * pad - kh) // stride + 1
    Wout = (W + 2 * pad - kw) // stride + 1

    if pad > 0:
        X = np.pad(X, ((0, 0), (0, 0), (pad, pad), (pad, pad)), mode='constant')

    # stride 沿 N, C, H, W 4 维;窗口沿 kh, kw, Hout, Wout 4 维
    sN, sC, sH, sW = X.strides
    cols = np.lib.stride_tricks.as_strided(
        X,
        shape=(N, C, kh, kw, Hout, Wout),
        strides=(sN, sC, sH, sW, stride * sH, stride * sW),
    )
    # (N, C, kh, kw, Hout, Wout) → (N*Hout*Wout, C*kh*kw) → .T → (C*kh*kw, N*Hout*Wout)
    cols = cols.transpose(0, 4, 5, 1, 2, 3).reshape(N * Hout * Wout, -1).T
    return cols


def col2im(cols, X_shape, kh, kw, stride, pad):
    """cols: (C*kh*kw, N*Hout*Wout) → X: (N, C, H, W)
    反向传播: 把每个输入位置的多个窗口梯度累加回原位置。
    """
    N, C, H, W = X_shape
    Hout = (H + 2 * pad - kh) // stride + 1
    Wout = (W + 2 * pad - kw) // stride + 1

    # (C, kh, kw, N, Hout, Wout) — 注意先转 C 维到第一轴
    cols = cols.reshape(C, kh, kw, N, Hout, Wout).transpose(3, 0, 1, 2, 4, 5)
    # cols shape (N, C, kh, kw, Hout, Wout)

    if pad > 0:
        H_pad, W_pad = H + 2 * pad, W + 2 * pad
    else:
        H_pad, W_pad = H, W

    Xp = np.zeros((N, C, H_pad, W_pad), dtype=cols.dtype)

    # 把每个 (kh, kw) 位置的窗口梯度按 stride 步长累加回 Xp
    # 朴素 for 循环是 O(kh*kw) 次 stride 视图赋值,比 np.add.at 快且易读
    for i in range(kh):
        for j in range(kw):
            Xp[:, :, i:i + stride * Hout:stride, j:j + stride * Wout:stride] += \
                cols[:, :, i, j, :, :]

    if pad > 0:
        return Xp[:, :, pad:pad + H, pad:pad + W]
    return Xp


# ============================================================
# 2. Conv2D 层 (forward + backward)
# ============================================================
class Conv2D:
    def __init__(self, in_channels, out_channels, kh=3, kw=3,
                 stride=1, pad=1, seed=0):
        rng = np.random.default_rng(seed)
        # He init (Kaiming): 方差 = 2 / (C_in * kh * kw)
        scale = np.sqrt(2.0 / (in_channels * kh * kw))
        self.W = rng.normal(0, scale, (out_channels, in_channels, kh, kw))
        self.b = np.zeros(out_channels)
        self.kh, self.kw = kh, kw
        self.stride = stride
        self.pad = pad
        self.cache = {}
        self.dW = None
        self.db = None

    def forward(self, X):
        """X: (N, C_in, H, W) → Y: (N, C_out, Hout, Wout)"""
        N, C_in, H, W = X.shape
        Hout = (H + 2 * self.pad - self.kh) // self.stride + 1
        Wout = (W + 2 * self.pad - self.kw) // self.stride + 1

        # Step 1: im2col → (C_in*kh*kw, N*Hout*Wout)
        cols = im2col(X, self.kh, self.kw, self.stride, self.pad)

        # Step 2: 把 W reshape 为 (C_out, C_in*kh*kw) → .T → (C_in*kh*kw, C_out)
        W_col = self.W.reshape(self.W.shape[0], -1).T

        # Step 3: GEMM: (C_out, C_in*kh*kw) @ (C_in*kh*kw, N*Hout*Wout) → (C_out, N*Hout*Wout)
        # 注意 W_col 已经 .T,所以这里 W_col.T 是 (C_out, C_in*kh*kw)
        Y_col = self.W.reshape(self.W.shape[0], -1) @ cols + self.b.reshape(-1, 1)

        # Step 4: reshape 回 (N, C_out, Hout, Wout)
        Y = Y_col.reshape(self.W.shape[0], N, Hout, Wout).transpose(1, 0, 2, 3)

        # 缓存用于 backward
        self.cache = dict(X_shape=X.shape, cols=cols, W_col=W_col)
        return Y

    def backward(self, dY):
        """dY: (N, C_out, Hout, Wout) → dX: (N, C_in, H, W)
        同时计算 self.dW, self.db"""
        N, C_out, Hout, Wout = dY.shape

        # dY_col: (C_out, N*Hout*Wout)
        dY_col = dY.transpose(1, 0, 2, 3).reshape(C_out, -1)

        # 公式 (Eq 3 简化): dW_col = cols @ dY_col.T → (C_in*kh*kw, C_out)
        dW_col = self.cache['cols'] @ dY_col.T
        self.dW = dW_col.T.reshape(self.W.shape)              # (C_out, C_in, kh, kw)

        # 公式 (Eq 5): db = Σ dY over (N, Hout, Wout) → (C_out,)
        self.db = dY_col.sum(axis=1)

        # dX: W_col (C_in*kh*kw, C_out) @ dY_col (C_out, N*Hout*Wout) → dcols (C_in*kh*kw, N*Hout*Wout)
        dcols = self.cache['W_col'] @ dY_col
        dX = col2im(dcols, self.cache['X_shape'], self.kh, self.kw,
                    self.stride, self.pad)
        return dX


# ============================================================
# 3. 数值梯度 (中心差分) — 仅验证 dW
# ============================================================
def numerical_grad_dW(W, X, dY, kh, kw, stride, pad, eps=1e-5):
    N, C_in, H, W_in = X.shape
    C_out = W.shape[0]
    Hout = (H + 2 * pad - kh) // stride + 1
    Wout = (W_in + 2 * pad - kw) // stride + 1

    cols = im2col(X, kh, kw, stride, pad)            # (C_in*kh*kw, N*Hout*Wout)
    W_flat = W.flatten().copy()
    grad = np.zeros_like(W_flat)

    def y_full(W_arr):
        W_col = W_arr.reshape(C_out, -1)
        Y = (W_col @ cols).reshape(C_out, N, Hout, Wout).transpose(1, 0, 2, 3)
        return Y

    for i in range(len(W_flat)):
        W_flat[i] += eps
        fp = (dY * y_full(W_flat)).sum()
        W_flat[i] -= 2 * eps
        fm = (dY * y_full(W_flat)).sum()
        W_flat[i] += eps
        grad[i] = (fp - fm) / (2 * eps)
    return grad.reshape(W.shape)


# ============================================================
# 4. 演示
# ============================================================
def main():
    print("=" * 60)
    print("Test 1: Conv2D forward + numerical dW check (stride=1, pad=1)")
    print("=" * 60)
    rng = np.random.default_rng(0)
    N, C_in, C_out = 2, 3, 4
    H, W = 5, 5
    kh, kw, stride, pad = 3, 3, 1, 1

    X = rng.normal(0, 1, (N, C_in, H, W))
    conv = Conv2D(C_in, C_out, kh, kw, stride, pad, seed=42)
    Y = conv.forward(X)
    print(f"  X: {X.shape}  →  Y: {Y.shape}  (Hout=5, Wout=5, 'same' padding)")

    dY = rng.normal(0, 1, Y.shape)
    conv.backward(dY)

    dW_num = numerical_grad_dW(conv.W, X, dY, kh, kw, stride, pad)
    err = np.abs(conv.dW - dW_num).max()
    print(f"  dW max |ana - num| = {err:.2e}")
    print(f"  dW[0,0,0,0] ana={conv.dW[0,0,0,0]:.6f}  num={dW_num[0,0,0,0]:.6f}")
    assert err < 1e-7, "dW 数值梯度验证失败!"

    # ============ stride=2 测试 ============
    print()
    print("=" * 60)
    print("Test 2: stride=2 (Hout=(5+2-3)//2+1 = 3)")
    print("=" * 60)
    conv2 = Conv2D(C_in, C_out, kh=3, kw=3, stride=2, pad=1, seed=42)
    Y2 = conv2.forward(X)
    print(f"  X: {X.shape}  →  Y2: {Y2.shape}")
    assert Y2.shape == (N, C_out, 3, 3)

    # ============ 形状汇总 ============
    print()
    print("=" * 60)
    print("Test 3: im2col / col2im 形状对齐 (依据 arxiv 2408.12561)")
    print("=" * 60)
    cols = im2col(X, 3, 3, 1, 1)
    print(f"  X shape:        {X.shape}  = (N, C_in, H, W)")
    print(f"  cols shape:     {cols.shape}  = (C_in*kh*kw, N*Hout*Wout)")
    print(f"  W_col shape:    ({C_in*9}, {C_out})  = (C_in*kh*kw, C_out)")
    print(f"  Y_col shape:    ({C_out}, {N*5*5})  = (C_out, N*Hout*Wout)")
    print(f"  → reshape: (C_out, N, Hout, Wout) = {Y.shape}")

    # ============ 多次累加验证 ============
    print()
    print("=" * 60)
    print("Test 4: col2im 多次累加 (kernel 滑窗重叠处正确累加)")
    print("=" * 60)
    X_small = np.array([[[[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]]]],
                       dtype=np.float64)  # (1, 1, 3, 3)
    cols = im2col(X_small, kh=2, kw=2, stride=1, pad=0)
    print(f"  输入 3x3 → 4 个 2x2 窗口:")
    print(f"    pos (0,0) → {cols[:, 0]}")
    print(f"    pos (0,1) → {cols[:, 1]}")
    print(f"    pos (1,0) → {cols[:, 2]}")
    print(f"    pos (1,1) → {cols[:, 3]}")
    # 还原
    X_rec = col2im(cols, X_small.shape, kh=2, kw=2, stride=1, pad=0)
    print(f"  col2im 还原:")
    print(f"    期望: {X_small[0,0]}")
    print(f"    实际: {X_rec[0,0]}")
    assert np.allclose(X_rec, X_small)


if __name__ == "__main__":
    main()
