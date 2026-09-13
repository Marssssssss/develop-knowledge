# -*- coding: utf-8 -*-
"""Dropout(Srivastava et al., JMLR 2014)从零实现:原始版 + inverted 版,前向/反向 + 期望守恒验证。

- 原始论文版:训练时不缩放,推理时乘 (1-p)
- inverted 版(现代框架默认):训练时掩码后乘 1/(1-p),推理恒等
- 反向:梯度只流过幸存神经元,同样缩放 1/(1-p)

自测(python dropout.py):
1. inverted 版 E[out] ≈ x(相对误差 < 2%,10 万样本)
2. 原始版 E[(1-p)*out_train] ≈ x
3. 掩码分布:丢弃率 ≈ p;不同样本掩码独立
4. 数值梯度验证 backward(dx 相对误差 < 1e-6)
5. 推理恒等:eval 模式 out == x
"""
import numpy as np

RNG = np.random.default_rng(7)


# ---------------------------------------------------------------- inverted dropout(现代默认)
def dropout_forward(x, p, training=True, rng=RNG):
    """inverted dropout 前向。training=False 时恒等(推理无需任何缩放)。"""
    if not training or p == 0.0:
        return x, None
    if p >= 1.0:
        return np.zeros_like(x), np.zeros_like(x)
    mask = (rng.random(x.shape) >= p).astype(x.dtype)  # 保留概率 1-p
    out = x * mask / (1.0 - p)
    return out, mask


def dropout_backward(dout, mask, p):
    """反向:梯度只流过幸存神经元,并携带同样的 1/(1-p) 缩放。"""
    return dout * mask / (1.0 - p)


# ---------------------------------------------------------------- 原始论文版(2014)
def dropout_forward_original(x, p, training=True, rng=RNG):
    """原始版:训练只掩码不缩放,推理乘 (1-p)。"""
    if not training:
        return x * (1.0 - p), None
    mask = (rng.random(x.shape) >= p).astype(x.dtype)
    return x * mask, mask


def dropout_backward_original(dout, mask, p=None):
    """原始版反向:推理期缩放并入前向时,反向只乘掩码。"""
    return dout * mask


# ---------------------------------------------------------------- 验证工具
def numerical_gradient(f, x, dout, mask, p, eps=1e-6):
    """对 x 做中心差分数值梯度,验证 dropout_backward。"""
    num = np.zeros_like(x)
    it = np.nditer(x, flags=["multi_index"])
    while not it.finished:
        idx = it.multi_index
        old = x[idx]
        x[idx] = old + eps
        f1 = (f(x, mask) * dout).sum()
        x[idx] = old - eps
        f2 = (f(x, mask) * dout).sum()
        x[idx] = old
        num[idx] = (f1 - f2) / (2 * eps)
        it.iternext()
    return num


def self_test():
    p = 0.4
    x = RNG.normal(0.0, 2.0, size=(500, 100))  # 5 万样本

    # 1. inverted:期望守恒 E[out] = x
    out, mask = dropout_forward(x, p, training=True)
    keep_rate = mask.mean()
    assert abs(keep_rate - (1 - p)) < 0.01, f"保留率 {keep_rate:.3f} 偏离 {1-p}"
    err = np.abs(out.mean(axis=0) - x.mean(axis=0)).max()
    assert err < 0.2, f"E[out]-x 偏差 {err:.3f} 过大"
    print(f"[1] inverted 期望守恒: 保留率={keep_rate:.3f} (目标 {1-p}), "
          f"E[out]-x max偏差={err:.4f} PASS")

    # 2. 原始版:推理缩放后期望守恒
    out_o, _ = dropout_forward_original(x, p, training=True)
    err_o = np.abs(out_o.mean(axis=0) * (1 - p) - x.mean(axis=0)).max()
    assert err_o < 0.2
    print(f"[2] 原始版 (1-p)·E[训练输出] ≈ x: max偏差={err_o:.4f} PASS")

    # 3. inverted 推理恒等(对应 model.eval())
    out_eval, m = dropout_forward(x, p, training=False)
    assert m is None and np.array_equal(out_eval, x)
    print("[3] inverted 推理恒等(out == x, mask=None) PASS")

    # 4. 数值梯度验证:固定掩码,backward 应与中心差分一致
    xt = RNG.normal(0.0, 1.0, size=(12, 8))
    dout = RNG.normal(0.0, 1.0, size=(12, 8))
    _, mask = dropout_forward(xt, p, training=True)
    analytic = dropout_backward(dout, mask, p)
    numeric = numerical_gradient(
        lambda v, m: v * m / (1.0 - p), xt, dout, mask, p)
    rel = np.abs(analytic - numeric).max() / (np.abs(numeric).max() + 1e-12)
    assert rel < 1e-6, f"梯度相对误差 {rel:.2e}"
    print(f"[4] 数值梯度验证: max相对误差={rel:.2e} PASS")

    # 5. 梯度不流过被丢弃单元
    assert np.all(analytic[mask == 0] == 0)
    print("[5] 被丢弃单元梯度恒为 0 PASS")

    # 6. 边界:p=0 恒等
    out0, m0 = dropout_forward(xt, 0.0, training=True)
    assert np.array_equal(out0, xt) and m0 is None
    print("[6] p=0 恒等 PASS")

    print("\nself_test: 全部 6 项 PASS")


if __name__ == "__main__":
    self_test()
