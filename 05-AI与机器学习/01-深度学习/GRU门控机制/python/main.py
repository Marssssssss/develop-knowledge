"""GRU 演示入口：门控的极端取值、两种 n_t 写法的分歧、长程梯度衰减。"""

import math

from gru import gru, gru_cell, gru_layer, init_params, sigmoid, stdv_for


def main():
    H, X = 4, 3
    x = [1.0, 0.5, -2.0]
    h = [0.3, -0.7, 1.2, 0.0]

    print("== 参数规模约定 ==")
    print("  GRU gate_size = 3H =", 3 * H, "（LSTM 是 4H）")
    print("  stdv = 1/sqrt(H) =", "%.6f" % stdv_for(H))

    print("\n== 更新门 z 的三种极端 ==")
    for zb, tag in ((30.0, "z→1"), (-30.0, "z→0"), (0.0, "z=0.5")):
        g = 3 * H
        p = {"w_ih": [[0.0] * X for _ in range(g)], "w_hh": [[0.0] * H for _ in range(g)],
             "b_ih": [0.0] * g, "b_hh": [0.0] * g}
        for i in range(H, 2 * H):
            p["b_ih"][i] = zb
            p["b_hh"][i] = zb
        for i in range(H):
            p["b_ih"][2 * H + i] = 0.7
        hp, _r, z, n = gru_cell(x, h, p, H)
        print("  %-6s z=%.6f  n=%.6f  h'=%s" % (tag, z[0], n[0], ["%.4f" % v for v in hp]))

    print("\n== PyTorch 写法 vs 原论文写法的 n_t ==")
    g = 3 * H
    p = {"w_ih": [[0.0] * X for _ in range(g)], "w_hh": [[0.0] * H for _ in range(g)],
         "b_ih": [0.0] * g, "b_hh": [0.0] * g}
    p["w_hh"][2 * H + 0] = [0.0, 1.0, 0.0, 0.0]     # n_0 读 h_1（非对角）
    p["b_hh"][2 * H + 0] = 1.0
    _a, r, _z, n_torch = gru_cell(x, h, p, H, paper_variant=False)
    _b, _r, _z2, n_paper = gru_cell(x, h, p, H, paper_variant=True)
    print("  r = %.6f" % r[0])
    print("  PyTorch  n_0 = tanh(r·(W_hn h + b_hn)) = %.9f" % n_torch[0])
    print("  原论文    n_0 = tanh(W_hn(r⊙h) + b_hn) = %.9f" % n_paper[0])

    print("\n== 长程梯度：r=0 且 W_hz=0 时 ∂h_T/∂h_0 = ∏ z_t ==")
    seq = [[[0.4]], [[-0.3]], [[1.1]], [[0.2]], [[-0.8]]]
    for zb in (0.6, 1.2, 2.5, 20.0):
        g3 = 3
        q = {"w_ih": [[0.0] * 1 for _ in range(g3)], "w_hh": [[0.0] * 1 for _ in range(g3)],
             "b_ih": [-40.0, zb, 0.9], "b_hh": [-40.0, zb, 0.0]}
        z = sigmoid(2 * zb)
        _, hA, _ = gru_layer(seq, [[0.5]], q, 1)
        eps = 1e-6
        _, hB, _ = gru_layer(seq, [[0.5 + eps]], q, 1)
        fd = (hB[0][0] - hA[0][0]) / eps
        print("  z=%.6f → 数值导数 %.9f，z^5 = %.9f" % (z, fd, z ** 5))

    print("\n== 多层 / 双向的形状 ==")
    Tn, N = 4, 2
    xs = [[[0.1 * (t + 1) + 0.05 * b for _ in range(X)] for b in range(N)] for t in range(Tn)]
    pl = [[init_params(X, H, seed=41)], [init_params(H, H, seed=42)]]
    out, hn = gru(xs, None, pl, H, num_layers=2)
    print("  两层单向：output[T][N][H] = %s，h_n 层数 = %d" % ([len(out), len(out[0]), len(out[0][0])], len(hn)))
    outb, hnb = gru(xs, None, [[init_params(X, H, seed=51), init_params(X, H, seed=52)]], H, bidirectional=True)
    print("  双向：output 特征维 = %d（= 2H），h_n 层数 = %d" % (len(outb[0][0]), len(hnb)))


if __name__ == "__main__":
    main()
