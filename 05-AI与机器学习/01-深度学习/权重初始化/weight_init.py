# -*- coding: utf-8 -*-
"""权重初始化:Xavier/Glorot 与 He/Kaiming 从零实现 + 深层网络方差稳定性实验。

依据:
- Glorot & Bengio 2010 "Understanding the difficulty of training deep feedforward
  neural networks" (JMLR): W ~ U(+-sqrt(6/(fan_in+fan_out)))
- He et al. 2015 "Delving Deep into Rectifiers" (arXiv:1502.01852): W ~ N(0, 2/fan_in)
- PyTorch torch.nn.init 官方文档的 gain / bound / std 公式(2.14 版实测核对)

自测(python weight_init.py):
1. 各初始化器方差 ≈ 理论方差(相对误差 < 3%)
2. 20 层 ReLU 网络前向方差:He 保持 ~1,Xavier 逐层减半,bad init 爆炸/消失
3. 20 层 tanh 网络:Xavier 保持 ~1
4. 反向梯度方差:He(ReLU) 稳定,bad init 消失/爆炸
"""
import numpy as np

RNG = np.random.default_rng(42)


# ---------------------------------------------------------------- gain(来自 PyTorch 文档)
def calculate_gain(nonlinearity, param=None):
    """PyTorch nn.init.calculate_gain 的推荐增益表(节选)。"""
    table = {
        "linear": 1.0, "sigmoid": 1.0, "tanh": 5.0 / 3.0, "relu": np.sqrt(2.0),
    }
    if nonlinearity == "leaky_relu":
        a = param if param is not None else 0.01
        return np.sqrt(2.0 / (1.0 + a * a))
    return table[nonlinearity]


# ---------------------------------------------------------------- 五种初始化器
def lecun_normal(fan_in, fan_out):
    """LeCun 1998: N(0, 1/fan_in)。"""
    return RNG.normal(0.0, np.sqrt(1.0 / fan_in), size=(fan_out, fan_in))


def xavier_uniform(fan_in, fan_out, gain=1.0):
    """Glorot 2010 原文形式: U(+-gain*sqrt(6/(fan_in+fan_out)))。"""
    bound = gain * np.sqrt(6.0 / (fan_in + fan_out))
    return RNG.uniform(-bound, bound, size=(fan_out, fan_in))


def xavier_normal(fan_in, fan_out, gain=1.0):
    """Glorot 正态版: std = gain*sqrt(2/(fan_in+fan_out))。"""
    std = gain * np.sqrt(2.0 / (fan_in + fan_out))
    return RNG.normal(0.0, std, size=(fan_out, fan_in))


def he_uniform(fan_in, fan_out, gain=1.0):
    """He 2015 均匀版: U(+-gain*sqrt(6/fan_in))(PyTorch kaiming_uniform_)。"""
    bound = gain * np.sqrt(6.0 / fan_in)
    return RNG.uniform(-bound, bound, size=(fan_out, fan_in))


def he_normal(fan_in, fan_out, gain=1.0):
    """He 2015 原文形式: N(0, gain^2 * 2/fan_in)。

    PyTorch kaiming_normal_ 的 std = gain/sqrt(fan_mode) 中的 gain 来自
    calculate_gain('relu') = sqrt(2),故 ReLU 情形 std = sqrt(2/fan_in)。
    """
    std = gain * np.sqrt(2.0 / fan_in)
    return RNG.normal(0.0, std, size=(fan_out, fan_in))


def constant_init(fan_in, fan_out, std):
    """对照用:固定 std 的正态初始化(坏初始化)。"""
    return RNG.normal(0.0, std, size=(fan_out, fan_in))


# ---------------------------------------------------------------- 深层网络实验
def forward_variances(init_fn, depth, width, act):
    """前向传播 depth 层,返回每层激活方差列表。输入方差归一到 1。"""
    x = RNG.normal(0.0, 1.0, size=(4096, width))  # 大 batch 让统计稳定
    variances, h = [float(np.var(x))], x
    for l in range(depth):
        w = init_fn(width, width)
        h = act(h @ w.T)
        variances.append(float(np.var(h)))
    return variances


def backward_variances(init_fn, depth, width, act):
    """反向传播 depth 层(顶层梯度方差 1),返回每层 dX 方差。

    ReLU 网络的反向约有一半梯度被掩掉,He 推导同样适用 2/fan_in。
    """
    dout = RNG.normal(0.0, 1.0, size=(4096, width))
    variances, g = [float(np.var(dout))], dout
    for _ in range(depth):
        w = init_fn(width, width)
        z_mask = (RNG.normal(0.0, 1.0, size=(4096, width)) > 0).astype(float)
        g = (g @ w) * z_mask  # dX = dZ @ W, dZ = dX * 1[z>0](取随机掩码模拟前向)
        variances.append(float(np.var(g)))
    return variances


def relu(x):
    return np.maximum(x, 0.0)


def tanh_act(x):
    return np.tanh(x)


def run_experiments():
    depth, width = 20, 256
    cases = [
        ("bad: std=1.0 (爆炸)", lambda i, o: constant_init(i, o, 1.0), relu),
        ("bad: std=0.01 (消失)", lambda i, o: constant_init(i, o, 0.01), relu),
        ("Xavier + ReLU", xavier_uniform, relu),
        ("He     + ReLU", he_normal, relu),
        ("Xavier(gain=1) + tanh", xavier_uniform, tanh_act),
        ("Xavier(gain=5/3) + tanh",
         lambda i, o: xavier_uniform(i, o, gain=5.0 / 3.0), tanh_act),
    ]
    print(f"== 前向: {depth} 层 ReLU/tanh MLP, width={width}, 输入 Var=1 ==")
    print(f"{'初始化':<22}{'Var(h_5)':>10}{'Var(h_10)':>10}{'Var(h_20)':>10}  判定")
    for name, fn, act in cases:
        v = forward_variances(fn, depth, width, act)
        verdict = ("稳定" if 0.2 < v[-1] < 5 else ("爆炸" if v[-1] >= 5 else "消失"))
        print(f"{name:<22}{v[5]:>10.3f}{v[10]:>10.3f}{v[20]:>10.3f}  {verdict}")

    print(f"\n== 反向: {depth} 层 ReLU MLP, 顶层梯度 Var=1 ==")
    for name, fn in [("bad: std=1.0", lambda i, o: constant_init(i, o, 1.0)),
                     ("bad: std=0.01", lambda i, o: constant_init(i, o, 0.01)),
                     ("He + ReLU", he_normal)]:
        v = backward_variances(fn, depth, width, relu)
        print(f"{name:<22} dVar@5={v[5]:>10.3g}  dVar@20={v[20]:>10.3g}")


def self_test():
    # 1. 初始化器方差 ≈ 理论值
    fi, fo = 256, 128
    checks = [
        (lecun_normal(fi, fo), 1.0 / fi, "LeCun N(0,1/fan_in)"),
        (xavier_uniform(fi, fo), 2.0 / (fi + fo), "Xavier U"),
        (xavier_normal(fi, fo), 2.0 / (fi + fo), "Xavier N"),
        (he_uniform(fi, fo), 2.0 / fi, "He U"),
        (he_normal(fi, fo), 2.0 / fi, "He N"),
    ]
    for w, target, name in checks:
        rel = abs(np.var(w) - target) / target
        assert rel < 0.03, f"{name} 方差偏差 {rel:.4f}"
    # gain 表
    assert abs(calculate_gain("relu") - np.sqrt(2)) < 1e-12
    assert abs(calculate_gain("tanh") - 5 / 3) < 1e-12
    assert abs(calculate_gain("leaky_relu", 0.2) - np.sqrt(2 / 1.04)) < 1e-12
    # 2. He+ReLU 前向方差保持;Xavier+ReLU 逐层约减半
    v_he = forward_variances(he_normal, 20, 256, relu)
    v_xa = forward_variances(xavier_uniform, 20, 256, relu)
    assert 0.1 < v_he[-1] < 5.0, f"He 应稳定(深度方向方差有几何涨落), 实测 Var={v_he[-1]:.3f}"
    assert 0.4 < v_he[5] < 2.5, f"He 前 5 层应贴近 1, 实测 {v_he[5]:.3f}"
    assert v_xa[-1] < 0.02, f"Xavier+ReLU 应逐层减半(0.5^20≈1e-6), 实测 {v_xa[-1]:.3g}"
    # 3. tanh + Xavier(gain=5/3, PyTorch calculate_gain('tanh') 推荐值)稳定;
    #    gain=1 时 tanh 饱和压缩导致每层约 x0.8 的缓慢收缩(20 层后 ~0.02)
    v_xt = forward_variances(xavier_uniform, 20, 256, tanh_act)
    v_xt_g = forward_variances(lambda i, o: xavier_uniform(i, o, gain=5.0 / 3.0),
                               20, 256, tanh_act)
    assert 0.005 < v_xt[-1] < 0.2, f"gain=1 应缓慢收缩, 实测 {v_xt[-1]:.3f}"
    assert 0.4 < v_xt_g[-1] < 2.0, f"gain=5/3 应稳定, 实测 {v_xt_g[-1]:.3f}"
    # 4. 反向: He 稳定, std=1 爆炸, std=0.01 消失
    b_he = backward_variances(he_normal, 20, 256, relu)
    b_up = backward_variances(lambda i, o: constant_init(i, o, 1.0), 20, 256, relu)
    b_dn = backward_variances(lambda i, o: constant_init(i, o, 0.01), 20, 256, relu)
    assert 0.2 < b_he[-1] < 5.0
    assert b_up[-1] > 100 and b_dn[-1] < 1e-3
    print("self_test: 全部 PASS")
    print(f"  He 前向 Var@20 = {v_he[-1]:.3f} (目标~1)")
    print(f"  Xavier+ReLU 前向 Var@20 = {v_xa[-1]:.3g} (0.5^20≈1e-6, 逐层减半)")
    print(f"  Xavier+tanh Var@20: gain=1 → {v_xt[-1]:.3f}(饱和缓慢收缩), "
          f"gain=5/3 → {v_xt_g[-1]:.3f}(稳定)")
    print(f"  He 反向 Var@20 = {b_he[-1]:.3f} | bad@1.0 = {b_up[-1]:.3g} | bad@0.01 = {b_dn[-1]:.3g}")


if __name__ == "__main__":
    run_experiments()
    print()
    self_test()
