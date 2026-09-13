# -*- coding: utf-8 -*-
"""Batch Normalization(Ioffe & Szegedy 2015, arXiv:1502.03167)从零实现:
Algorithm 1(训练前向)+ Algorithm 2(推理)+ 完整解析反向 + 数值梯度验证。

前向: mu_B = mean(x); var_B = mean((x-mu_B)^2); xhat = (x-mu_B)/sqrt(var_B+eps); y = gamma*xhat+beta
推理: 用训练期 EMA 累积的 running_mean / running_var
反向(Algorithm 2 展开):
  dgamma = sum(dout*xhat); dbeta = sum(dout)
  dxhat = dout*gamma
  dvar = sum(dxhat*(x-mu)*(-0.5)*(var+eps)^{-3/2})
  dmu = sum(dxhat*(-1/sqrt(var+eps))) + dvar*mean(-2(x-mu))
  dx = dxhat/sqrt(var+eps) + dvar*2(x-mu)/m + dmu/m

自测(python batchnorm.py):
1. 训练态输出均值≈beta、(方差*gamma^2)≈输出方差(归一化后缩放)
2. 数值梯度验证 dx/dgamma/dbeta(相对误差 < 1e-6)
3. running stats: 同分布批量喂数后,eval 输出 ≈ 训练态输出
4. gamma=1,beta=0 时训练态输出标准化(N(0,1))
"""
import numpy as np

RNG = np.random.default_rng(0)


class BatchNorm1d:
    """按特征维( axis=0 )做 BN,对应 torch.nn.BatchNorm1d(num_features)。"""

    def __init__(self, num_features, eps=1e-5, momentum=0.9):
        self.eps = eps
        self.momentum = momentum  # new = momentum*old + (1-momentum)*batch (PyTorch 语义)
        self.gamma = np.ones(num_features)
        self.beta = np.zeros(num_features)
        self.running_mean = np.zeros(num_features)
        self.running_var = np.ones(num_features)

    def forward(self, x, training):
        if training:
            mu = x.mean(axis=0)
            var = ((x - mu) ** 2).mean(axis=0)  # 有偏估计 m^{-1} sum(论文 Algorithm 1)
            self.running_mean = self.momentum * self.running_mean + (1 - self.momentum) * mu
            self.running_var = self.momentum * self.running_var + (1 - self.momentum) * var
        else:
            mu, var = self.running_mean, self.running_var
        xhat = (x - mu) / np.sqrt(var + self.eps)
        self.cache = (x, xhat, mu, var)
        return self.gamma * xhat + self.beta

    def backward(self, dout):
        x, xhat, mu, var = self.cache
        m = x.shape[0]
        dgamma = (dout * xhat).sum(axis=0)
        dbeta = dout.sum(axis=0)
        dxhat = dout * self.gamma
        inv_std = 1.0 / np.sqrt(var + self.eps)
        dvar = (dxhat * (x - mu) * (-0.5) * (var + self.eps) ** -1.5).sum(axis=0)
        dmu = (dxhat * -inv_std).sum(axis=0) + dvar * (-2.0 * (x - mu)).mean(axis=0)
        dx = dxhat * inv_std + dvar * 2.0 * (x - mu) / m + dmu / m
        return dx, dgamma, dbeta


def numerical_grads(bn, x, dout, eps=1e-6):
    """中心差分求 dx / dgamma / dbeta。

    注意:扰动 x 时 mu/var 必须随之重算(它们是 x 的函数),
    否则数值梯度只含 dxhat/sqrt(var+eps) 一项,会漏掉 dvar/dmu 路径。
    """
    def f_x(v):
        mu = v.mean(axis=0)
        var = ((v - mu) ** 2).mean(axis=0)
        xhat = (v - mu) / np.sqrt(var + bn.eps)
        return ((bn.gamma * xhat + bn.beta) * dout).sum()

    def f_param(v, which):
        xhat = bn.cache[1]
        if which == "g":
            return ((v * xhat + bn.beta) * dout).sum()
        return ((bn.gamma * xhat + v) * dout).sum()

    n_dx = np.zeros_like(x)
    it = np.nditer(x, flags=["multi_index"])
    while not it.finished:
        i = it.multi_index
        old = x[i]
        x[i] = old + eps; f1 = f_x(x)
        x[i] = old - eps; f2 = f_x(x)
        x[i] = old
        n_dx[i] = (f1 - f2) / (2 * eps)
        it.iternext()

    n_g, n_b = np.zeros_like(bn.gamma), np.zeros_like(bn.beta)
    for arr, num, which in ((bn.gamma, n_g, "g"), (bn.beta, n_b, "b")):
        for j in range(arr.size):
            old = arr[j]
            arr[j] = old + eps; f1 = f_param(arr, which)
            arr[j] = old - eps; f2 = f_param(arr, which)
            arr[j] = old
            num[j] = (f1 - f2) / (2 * eps)
    return n_dx, n_g, n_b


def self_test():
    bn = BatchNorm1d(num_features=6)
    x = RNG.normal(3.0, 2.5, size=(64, 6)) * RNG.uniform(0.5, 3.0, size=(1, 6))

    # 1. gamma=1,beta=0:训练态输出标准化
    y = bn.forward(x, training=True)
    assert abs(y.mean()) < 1e-7 and abs(y.std() - 1.0) < 0.05
    print(f"[1] 训练态标准化: 输出 mean={y.mean():.2e}, std={y.std():.4f} PASS")

    # 2. 解析梯度 vs 数值梯度
    dout = RNG.normal(0.0, 1.0, size=x.shape)
    dx, dgamma, dbeta = bn.backward(dout)
    n_dx, n_g, n_b = numerical_grads(bn, x, dout)
    rdx = np.abs(dx - n_dx).max() / (np.abs(n_dx).max() + 1e-12)
    rdg = np.abs(dgamma - n_g).max() / (np.abs(n_g).max() + 1e-12)
    rdb = np.abs(dbeta - n_b).max() / (np.abs(n_b).max() + 1e-12)
    assert rdx < 1e-6 and rdg < 1e-6 and rdb < 1e-6, (rdx, rdg, rdb)
    print(f"[2] 数值梯度: dx={rdx:.2e}, dgamma={rdg:.2e}, dbeta={rdb:.2e} PASS")

    # 3. 仿射参数生效:gamma=2,beta=1 → 输出均值=1, 标准差≈2
    bn2 = BatchNorm1d(6)
    bn2.gamma[:] = 2.0
    bn2.beta[:] = 1.0
    y2 = bn2.forward(x, training=True)
    assert abs(y2.mean() - 1.0) < 1e-7 and abs(y2.std() - 2.0) < 0.1
    print(f"[3] 仿射参数: gamma=2,beta=1 → mean={y2.mean():.4f}, std={y2.std():.4f} PASS")

    # 4. running stats: 同分布喂 200 个 batch 后, eval 输出近似标准化
    #    注:running stats 是 batch 统计的 EMA(窗口 ~1/(1-momentum)=10 个 batch),
    #    保留单批均值的抽样噪声(~sigma/sqrt(m)),m 越大 eval 越准 —— BN 小批量
    #    性能下降的根源之一
    bn3 = BatchNorm1d(6)
    for _ in range(200):
        batch = RNG.normal(3.0, 2.5, size=(512, 6))
        bn3.forward(batch, training=True)
    x_eval = RNG.normal(3.0, 2.5, size=(512, 6))  # 与训练同分布的新样本
    y_eval = bn3.forward(x_eval, training=False)
    err = np.abs(y_eval.mean(axis=0)).max()
    assert err < 0.25, f"eval 均值偏差 {err:.3f}"
    print(f"[4] running stats: eval 输出 mean|max|={err:.4f} (含 EMA 抽样噪声) PASS")

    # 5. batch 统计的轻微正则效应:同一输入两次 forward(不同 batch 混入)输出不同
    bn4 = BatchNorm1d(6)
    mixed = np.vstack([x, RNG.normal(0.0, 10.0, size=(8, 6))])
    ya = bn4.forward(x, training=True)
    yb = bn4.forward(mixed, training=True)[: x.shape[0]]
    assert not np.allclose(ya, yb)
    print("[5] batch 统计噪声: 同一输入在不同 batch 中输出不同(正则效应来源) PASS")

    print("\nself_test: 全部 5 项 PASS")


if __name__ == "__main__":
    self_test()
