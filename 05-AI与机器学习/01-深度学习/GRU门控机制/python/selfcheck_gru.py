"""GRU 自检：门控语义 + PyTorch 与原论文的两处差异 + 长程梯度。"""

import math

from gru import (
    LCG,
    check_input_dim,
    gate_size,
    gru,
    gru_cell,
    gru_layer,
    init_params,
    sigmoid,
    split_gates,
    stdv_for,
    to_batch_first,
)

TOL = 1e-9
FAILED = []
PASSED = 0


def ok(name, cond):
    global PASSED
    if cond:
        PASSED += 1
        print("  ok  %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL %s" % name)


def close(name, a, b, tol=1e-7):
    ok("%s (%r ≈ %r)" % (name, a, b), abs(a - b) <= tol * max(1.0, abs(a), abs(b)))


def flatten(v):
    out = []
    for e in v:
        if isinstance(e, list):
            out.extend(flatten(e))
        else:
            out.append(e)
    return out


def allclose(name, xs, ys, tol=1e-7):
    xs, ys = flatten(xs), flatten(ys)
    return _allclose(name, xs, ys, tol)


def _allclose(name, xs, ys, tol=1e-7):
    ok("%s (逐项 %d 个)" % (name, len(xs)),
       len(xs) == len(ys) and all(abs(a - b) <= tol * max(1.0, abs(a), abs(b)) for a, b in zip(xs, ys)))


def zeros(rows, cols):
    return [[0.0] * cols for _ in range(rows)]


def hand_params(hidden_size, input_size, z_bias=None, r_bias=None, w_hz_zero=False, w_hn=None, b_hn=None):
    """手工构造参数：默认所有权重为 0，只通过 bias 指定门值。"""
    g = 3 * hidden_size
    p = {
        "w_ih": zeros(g, input_size),
        "w_hh": zeros(g, hidden_size),
        "b_ih": [0.0] * g,
        "b_hh": [0.0] * g,
    }
    if z_bias is not None:
        for j in range(hidden_size, 2 * hidden_size):
            p["b_ih"][j] = z_bias
            p["b_hh"][j] = z_bias
    if r_bias is not None:
        for j in range(0, hidden_size):
            p["b_ih"][j] = r_bias
            p["b_hh"][j] = r_bias
    if not w_hz_zero:
        pass
    if w_hn is not None:
        for i in range(hidden_size):
            p["w_hh"][2 * hidden_size + i] = list(w_hn[i])
    if b_hn is not None:
        for i in range(hidden_size):
            p["b_hh"][2 * hidden_size + i] = b_hn[i]
    return p


def main():
    H, X = 4, 3

    # ---- 1. gate_size / stdv ----
    ok("LSTM gate_size = 4H", gate_size("lstm", 10) == 40)
    ok("GRU  gate_size = 3H", gate_size("gru", 10) == 30)
    ok("RNN  gate_size = H", gate_size("rnn", 10) == 10)
    close("stdv = 1/sqrt(H)：H=16 → 0.25", stdv_for(16), 0.25)
    close("hidden_size=0 → stdv 0（源码三元式）", stdv_for(0), 0.0)

    # ---- 2. 参数形状与初始化范围 ----
    p = init_params(X, H, seed=7)
    ok("weight_ih 形状 (3H, input_size)", len(p["w_ih"]) == 3 * H and len(p["w_ih"][0]) == X)
    ok("weight_hh 形状 (3H, H)", len(p["w_hh"]) == 3 * H and len(p["w_hh"][0]) == H)
    ok("bias 长度 3H", len(p["b_ih"]) == 3 * H and len(p["b_hh"]) == 3 * H)
    s = stdv_for(H)
    ok("所有参数落在 U(-1/√H, 1/√H) 内",
       all(-s <= v <= s for row in p["w_ih"] for v in row)
       and all(-s <= v <= s for row in p["w_hh"] for v in row)
       and all(-s <= v <= s for v in p["b_ih"] + p["b_hh"]))
    r_w, z_w, n_w = split_gates(p["w_ih"], H)
    ok("(W_ir|W_iz|W_in) 三段各 H 行", len(r_w) == H and len(z_w) == H and len(n_w) == H)
    ok("W_ir 是前 H 行（门序）", r_w[0] is p["w_ih"][0] and z_w[0] is p["w_ih"][H] and n_w[0] is p["w_ih"][2 * H])

    # ---- 3. 门控的极端情形 ----
    x = [1.0, 0.5, -2.0]
    h = [0.3, -0.7, 1.2, 0.0]
    # 让 n 取一个非平凡值：给 W_in / b_in 定值（通过 b_in 通道）
    pz1 = hand_params(H, X, z_bias=30.0)
    for i in range(H):
        pz1["b_ih"][2 * H + i] = 0.7 + 0.1 * i      # n = tanh(0.7+0.1i)
    hp1, _r1, z1, n1 = gru_cell(x, h, pz1, H)
    allclose("z→1 时 h' = h（更新门全开，状态直通）", hp1, h)
    ok("z→1 的 z 值确实 ≈1", all(abs(v - 1.0) < 1e-9 for v in z1))

    pz0 = hand_params(H, X, z_bias=-30.0)
    for i in range(H):
        pz0["b_ih"][2 * H + i] = 0.7 + 0.1 * i
    hp0, _r0, z0, n0 = gru_cell(x, h, pz0, H)
    allclose("z→0 时 h' = n（完全重写）", hp0, n0)
    ok("z→0 的 z 值确实 ≈0", all(v < 1e-9 for v in z0))

    # z = 0.5：b=0 → sigmoid(0)=0.5
    pzh = hand_params(H, X, z_bias=0.0)
    for i in range(H):
        pzh["b_ih"][2 * H + i] = 0.7
    hph, _rh, zh, nh = gru_cell(x, h, pzh, H)
    allclose("z=0.5 时 h' = 0.5·n + 0.5·h", hph, [0.5 * nh[i] + 0.5 * h[i] for i in range(H)])

    # ---- 4. reset gate ----
    r0 = hand_params(H, X, r_bias=-30.0)
    for i in range(H):
        r0["b_ih"][2 * H + i] = 0.7
        r0["w_hh"][2 * H + i] = [1.0, 2.0, 3.0, 4.0]        # W_hn 非对角且很大
        r0["b_hh"][2 * H + i] = 5.0
    _hp_a, ra, _za, na = gru_cell(x, h, r0, H)
    _hp_b, rb, _zb, nb = gru_cell(x, [-9.0, 9.0, -9.0, 9.0], r0, H)
    ok("r→0 时 r≈0", all(v < 1e-9 for v in ra))
    allclose("r→0 时 n 与 h 无关（换 h 结果不变）", na, nb)
    allclose("r→0 时 n = tanh(b_in)（W_hn h + b_hn 被抹掉）", na, [math.tanh(0.7)] * H)

    # r 全开（b_ir/b_hr 各 +15 → sigmoid(30)≈1）
    r1 = hand_params(H, X, r_bias=15.0)
    for i in range(H):
        r1["b_ih"][2 * H + i] = 0.7
        r1["w_hh"][2 * H + i] = [1.0, 2.0, 3.0, 4.0]
        r1["b_hh"][2 * H + i] = 5.0
    _hp, r1v, _z, n1v = gru_cell(x, h, r1, H)
    ok("r≈1（两处 bias 相加得 30）", all(abs(v - 1.0) < 1e-9 for v in r1v))
    wh_n = sum(1.0 * h[0] + 2.0 * h[1] + 3.0 * h[2] + 4.0 * h[3] for _ in [0])
    allclose("r→1 时 n = tanh(b_in + W_hn·h + b_hn)", n1v, [math.tanh(0.7 + wh_n + 5.0)] * H)

    # ---- 5. PyTorch 变体 vs 原论文变体 ----
    pdiff = hand_params(H, X, r_bias=0.0)     # r = 0.5（b_ir=b_hr=0）
    for i in range(H):
        pdiff["b_ih"][2 * H + i] = 0.0
        pdiff["w_hh"][2 * H + i] = [0.0] * H
        pdiff["b_hh"][2 * H + i] = 0.0
    pdiff["w_hh"][2 * H + 0] = [0.0, 1.0, 0.0, 0.0]      # 非对角：n_0 读 h_1
    pdiff["b_hh"][2 * H + 0] = 1.0
    _a, ra2, _z, n_torch = gru_cell(x, h, pdiff, H, paper_variant=False)
    _b, _r, _z2, n_paper = gru_cell(x, h, pdiff, H, paper_variant=True)
    close("r = sigmoid(0) = 0.5", ra2[0], 0.5)
    close("PyTorch：n = tanh(r·(W_hn h + b_hn)) = tanh(0.5·(h_1+1))", n_torch[0], math.tanh(0.5 * (h[1] + 1.0)))
    close("原论文：n = tanh(W_hn(r⊙h) + b_hn) = tanh(0.5·h_1 + 1)", n_paper[0], math.tanh(0.5 * h[1] + 1.0))
    ok("两种写法在 b_hn≠0 时确实不同", abs(n_torch[0] - n_paper[0]) > 1e-6)
    # 成对用例：b_hn = 0 时二者仍然不同吗？——非对角时 r 位置不同 → 仍不同
    pdiff0 = hand_params(H, X, r_bias=0.0)
    pdiff0["w_hh"][2 * H + 0] = [0.0, 1.0, 0.0, 0.0]
    _a0, _r0v, _z, nt0 = gru_cell(x, h, pdiff0, H, paper_variant=False)
    _b0, _r, _z, np0 = gru_cell(x, h, pdiff0, H, paper_variant=True)
    close("b_hn=0 且非对角：PyTorch = tanh(0.5·h_1)", nt0[0], math.tanh(0.5 * h[1]))
    close("b_hn=0 且非对角：原论文 = tanh(0.5·h_1)（此时一致）", np0[0], math.tanh(0.5 * h[1]))
    # 对角 W_hn：r⊙(Wh) == W(r⊙h)，两种写法恒等
    pdiag = hand_params(H, X, r_bias=0.0)
    for i in range(H):
        pdiag["w_hh"][2 * H + i] = [0.0] * H
        pdiag["w_hh"][2 * H + i][i] = 2.0
    _ad, _rd, _zd, nt_d = gru_cell(x, h, pdiag, H, paper_variant=False)
    _bd, _r, _z, np_d = gru_cell(x, h, pdiag, H, paper_variant=True)
    allclose("W_hn 对角时两种写法等价（成对负控）", nt_d, np_d)

    # ---- 6. 长程梯度：r=0 且 W_hz=0 时 ∂h_T/∂h_0 = ∏ z_t ----
    H1 = 1
    pg = hand_params(H1, X)
    pg["b_ih"][0] = -40.0                 # r = sigmoid(-40) ≈ 0
    pg["b_hh"][0] = -40.0
    pg["b_ih"][H1] = 1.2                  # z = sigmoid(1.2)
    pg["b_hh"][H1] = 1.2
    pg["w_hh"][H1][0] = 0.0               # z 与 h 无关
    pg["b_ih"][2 * H1] = 0.9              # n = tanh(0.9) 常数
    seq = [[[0.4]], [[-0.3]], [[1.1]], [[0.2]], [[-0.8]]]  # T=5, N=1
    zval = sigmoid(1.2 + 1.2)
    prod = zval ** 5
    eps = 1e-6
    _, hA, _ = gru_layer(seq, [[0.5]], pg, H1)
    _, hB, _ = gru_layer(seq, [[0.5 + eps]], pg, H1)
    close("T=5 的 ∂h_T/∂h_0 数值导数 = z^5", (hB[0][0] - hA[0][0]) / eps, prod, tol=1e-5)
    ok("z^5 仍显著大于 0（乘性门衰减但不到 0）", prod > 0.5)
    # z→1 时梯度不衰减
    pg1 = hand_params(H1, X)
    for k in (0, H1, 2 * H1):
        pg1["b_ih"][k] = 40.0
        pg1["b_hh"][k] = 40.0
    pg1["b_ih"][0] = -40.0
    pg1["b_hh"][0] = -40.0
    pg1["w_hh"][H1][0] = 0.0
    pg1["b_ih"][2 * H1] = 0.9
    _, hA1, _ = gru_layer(seq, [[0.5]], pg1, H1)
    _, hB1, _ = gru_layer(seq, [[0.5 + eps]], pg1, H1)
    close("z→1 时 ∂h_T/∂h_0 ≈ 1（梯度直通）", (hB1[0][0] - hA1[0][0]) / eps, 1.0, tol=1e-5)

    # ---- 7. 序列级语义 ----
    Tn, N = 4, 2
    xs = [[[LCG(100 + t * 7 + b).uniform(-1, 1) for _ in range(X)] for b in range(N)] for t in range(Tn)]
    pp = init_params(X, H, seed=11)
    out, hn = gru(xs, None, [[pp]], H)
    ok("output 形状 [T][N][H]", len(out) == Tn and len(out[0]) == N and len(out[0][0]) == H)
    ok("h_n 形状 [D*num_layers][N][H] = [1][N][H]", len(hn) == 1 and len(hn[0]) == N)
    allclose("单层单向：h_n == 最后一个时间步的 output", hn[0], out[Tn - 1])
    allclose("h0 缺省为 0（与显式传 0 一致）", out[0], gru(xs, [[[0.0] * H for _ in range(N)]], [[pp]], H)[0][0])

    # batch_first
    out_bf, hn_bf = gru(to_batch_first(xs), None, [[pp]], H, batch_first=True)
    allclose("batch_first=True 与 False 输出一致", out_bf, out)
    allclose("batch_first=True 的 h_n 一致", hn_bf[0], hn[0])

    # 双向
    p0 = init_params(X, H, seed=21)
    p1 = init_params(X, H, seed=22)
    out_bi, hn_bi = gru(xs, None, [[p0, p1]], H, bidirectional=True)
    ok("双向 output 特征维 = 2H", len(out_bi[0][0]) == 2 * H)
    fwd, _ = gru(xs, None, [[p0]], H)
    allclose("双向输出前半 = 正向层输出", [row[:H] for row in out_bi[0]], fwd[0])
    ok("h_n 有 D*num_layers = 2 个方向层", len(hn_bi) == 2)
    allclose("双向 h_n[0] 等于正向层的 h_T", hn_bi[0], fwd[Tn - 1])
    rev = [xs[Tn - 1 - t] for t in range(Tn)]
    rev_out, rev_hn = gru(rev, None, [[p1]], H)
    allclose("双向 h_n[1] = 反向层（倒序喂入）的 h_T", hn_bi[1], rev_hn[0])
    allclose("双向输出后半 = 反向层输出翻转回来", [row[H:] for row in out_bi[Tn - 1]], rev_out[0])

    # 多层
    p2 = init_params(H, H, seed=31)
    out_2l, hn_2l = gru(xs, None, [[p0], [p2]], H, num_layers=2)
    ok("两层：h_n 有 2 项", len(hn_2l) == 2)
    ok("两层输出仍是 [T][N][H]", len(out_2l[0][0]) == H)
    l1, _ = gru(xs, None, [[p0]], H)
    l2, _ = gru(l1, None, [[p2]], H)
    allclose("第 2 层的输入 = 第 1 层的输出（无 dropout）", out_2l, l2)
    # dropout 只作用于非最后层
    mask = [[[0.0] * H for _ in range(N)] for _ in range(Tn)]
    out_p0, _ = gru(xs, None, [[p0]], H)
    out_d, _ = gru(xs, None, [[p0]], H, dropout_mask=mask)
    allclose("单层（即最后层）不加 dropout：全 0 掩码不影响输出", out_d, out_p0)
    out_d2, _ = gru(xs, None, [[p0], [p2]], H, num_layers=2, dropout_mask=mask)
    ok("两层时第一层被 dropout 掩码置零 → 输出变化", out_d2 != out_2l)

    # ---- 8. GRUCell 的维数校验 ----
    try:
        check_input_dim([[[1.0, 2.0]]], None)
        ok("3D 输入应当报错", False)
    except ValueError as e:
        ok("3D 输入报 ValueError：%s" % e, "3D" in str(e))
    ok("2D 输入视为 batched", check_input_dim([[1.0, 2.0]], None) is True)
    ok("1D 输入视为 unbatched", check_input_dim([1.0, 2.0], None) is False)
    try:
        check_input_dim([1.0], [[[1.0]]])
        ok("3D hx 应当报错", False)
    except ValueError as e:
        ok("3D hx 报 ValueError", "hidden" in str(e))

    # ---- 9. 激活函数值域 ----
    ok("sigmoid 值域 (0,1)", all(0.0 < sigmoid(v) < 1.0 for v in (-30.0, -1.0, 0.0, 1.0, 30.0)))
    close("sigmoid(0) = 0.5", sigmoid(0.0), 0.5)
    close("sigmoid(-x) = 1 - sigmoid(x)", sigmoid(-2.5) + sigmoid(2.5), 1.0)
    ok("tanh 值域 (-1,1)", all(-1.0 < math.tanh(v) < 1.0 for v in (-15.0, -1.0, 0.0, 1.0, 15.0)))

    print()
    print("断言总数 %d，失败 %d" % (PASSED + len(FAILED), len(FAILED)))
    if FAILED:
        for f in FAILED:
            print("  失败：%s" % f)
        raise SystemExit(1)
    print("GRU 自检全部通过")


if __name__ == "__main__":
    main()
