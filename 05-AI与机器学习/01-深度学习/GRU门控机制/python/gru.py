"""GRU 门控机制 —— pytorch/pytorch torch/nn/modules/rnn.py 逐行转写（GRU / GRUCell / RNNBase）。

纯标准库实现，权重语义与 PyTorch 一致：
  weight_ih : (3H, input_size)，行序 (W_ir | W_iz | W_in)
  weight_hh : (3H, H)         ，行序 (W_hr | W_hz | W_hn)
  b_ih/b_hh : (3H,)
序列张量统一用 [T][N][X] 表示（batch_first=False）。
"""

import math

# ---------------------------------------------------------------- 基础算子


def sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def tanh(x):
    return math.tanh(x)


def matvec(W, x):
    """W 为 [out][in]，x 为 [in]。"""
    return [sum(w * v for w, v in zip(row, x)) for row in W]


def add(a, b):
    return [p + q for p, q in zip(a, b)]


def mul(a, b):
    return [p * q for p, q in zip(a, b)]


def linear(W, x, b):
    if b is None:
        return matvec(W, x)
    return add(matvec(W, x), b)


# ---------------------------------------------------------------- 参数初始化


def gate_size(kind, hidden_size):
    """RNNBase.__init__ 里 LSTM→4H、GRU→3H、RNN→H。"""
    return {"lstm": 4, "gru": 3, "rnn": 1}[kind] * hidden_size


def stdv_for(hidden_size):
    """reset_parameters：stdv = 1/sqrt(hidden_size)，hidden_size<=0 时为 0。"""
    return 1.0 / math.sqrt(hidden_size) if hidden_size > 0 else 0.0


class LCG:
    """确定性均匀源，替代 torch 的 RNG，保证自检可复现。"""

    def __init__(self, seed):
        self.s = seed & 0xFFFFFFFF

    def uniform(self, lo, hi):
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return lo + (hi - lo) * (self.s / 0x7FFFFFFF)


def init_params(input_size, hidden_size, seed=20260924, layer_input=None):
    """按 RNNBase 的形状约定造一份参数（uniform(-stdv, stdv)）。"""
    lin = input_size if layer_input is None else layer_input
    stdv = stdv_for(hidden_size)
    rnd = LCG(seed)
    g = gate_size("gru", hidden_size)
    w_ih = [[rnd.uniform(-stdv, stdv) for _ in range(lin)] for _ in range(g)]
    w_hh = [[rnd.uniform(-stdv, stdv) for _ in range(hidden_size)] for _ in range(g)]
    b_ih = [rnd.uniform(-stdv, stdv) for _ in range(g)]
    b_hh = [rnd.uniform(-stdv, stdv) for _ in range(g)]
    return {"w_ih": w_ih, "w_hh": w_hh, "b_ih": b_ih, "b_hh": b_hh}


# ---------------------------------------------------------------- 单元


def split_gates(v, hidden_size):
    """(W_ir|W_iz|W_in) 的三段切分，顺序即源码注释里的门序。"""
    return v[0:hidden_size], v[hidden_size : 2 * hidden_size], v[2 * hidden_size : 3 * hidden_size]


def gru_cell(x, h, p, hidden_size, paper_variant=False):
    """GRUCell.forward 的四式。返回 (h', r, z, n)。

    paper_variant=True 走 Cho 2014 原式：n = tanh(W_in x + b_in + W_hn (r ⊙ h) + b_hn)。
    """
    w_ih, w_hh, b_ih, b_hh = p["w_ih"], p["w_hh"], p["b_ih"], p["b_hh"]
    r = [sigmoid(v) for v in add(linear(split_gates(w_ih, hidden_size)[0], x, split_gates(b_ih, hidden_size)[0]),
                                 linear(split_gates(w_hh, hidden_size)[0], h, split_gates(b_hh, hidden_size)[0]))]
    z = [sigmoid(v) for v in add(linear(split_gates(w_ih, hidden_size)[1], x, split_gates(b_ih, hidden_size)[1]),
                                 linear(split_gates(w_hh, hidden_size)[1], h, split_gates(b_hh, hidden_size)[1]))]
    w_in, w_hn = split_gates(w_ih, hidden_size)[2], split_gates(w_hh, hidden_size)[2]
    b_in, b_hn = split_gates(b_ih, hidden_size)[2], split_gates(b_hh, hidden_size)[2]
    if paper_variant:
        # Cho 2014 原式：n = tanh(W_in x + b_in + W_hn (r ⊙ h) + b_hn)
        inside = add(matvec(w_hn, mul(r, h)), b_hn)
    else:
        inside = mul(r, linear(w_hn, h, b_hn))
    n = [tanh(v) for v in add(linear(w_in, x, b_in), inside)]
    hp = [(1 - z[i]) * n[i] + z[i] * h[i] for i in range(hidden_size)]
    return hp, r, z, n


def check_input_dim(x, hx, name="GRUCell"):
    """GRUCell.forward 开头的维数校验：input 必须 1D/2D，hx 若给则 1D/2D。"""

    def dim_of(v):
        d = 0
        while isinstance(v, list):
            d += 1
            v = v[0] if v else None
        return d

    if dim_of(x) not in (1, 2):
        raise ValueError("%s: Expected input to be 1D or 2D, got %dD instead" % (name, dim_of(x)))
    if hx is not None and dim_of(hx) not in (1, 2):
        raise ValueError("%s: Expected hidden to be 1D or 2D, got %dD instead" % (name, dim_of(hx)))
    return dim_of(x) == 2


# ---------------------------------------------------------------- 序列


def gru_layer(x, h0, p, hidden_size, dropout_mask=None, paper_variant=False):
    """单层单向；x 为 [T][N][X]，返回 (outputs[T][N][H], h_T[N][H], zs[T][N][H])。"""
    T = len(x)
    N = len(x[0])
    h = [list(row) for row in h0] if h0 else [[0.0] * hidden_size for _ in range(N)]
    outs, zs = [], []
    for t in range(T):
        row_h, row_z = [], []
        for b in range(N):
            hp, _r, z, _n = gru_cell(x[t][b], h[b], p, hidden_size, paper_variant)
            h[b] = hp
            row_h.append(list(hp))
            row_z.append(list(z))
        if dropout_mask is not None:
            m = dropout_mask[t]
            h = [[h[b][i] * m[b][i] for i in range(hidden_size)] for b in range(N)]
        outs.append(row_h)
        zs.append(row_z)
    return outs, [list(r) for r in h], zs


def gru(x, h0, params, hidden_size, num_layers=1, bidirectional=False, dropout_mask=None,
        batch_first=False, paper_variant=False):
    """多层 / 双向的 nn.GRU。返回 (output, h_n)。

    output : [T][N][D*H]（最末层）；h_n : [D*num_layers][N][H]
    """
    if batch_first:
        x = [[x[n][t] for n in range(len(x))] for t in range(len(x[0]))]
    D = 2 if bidirectional else 1
    T, N = len(x), len(x[0])
    h_n = []
    layer_in = x
    for l in range(num_layers):
        dirs = []
        for d in range(D):
            # 反向层按 time 倒序喂入，输出再翻转回来
            seq = layer_in if d == 0 else [[layer_in[T - 1 - t][n] for n in range(N)] for t in range(T)]
            h0l = None if h0 is None else h0[l * D + d]
            drop = dropout_mask if l < num_layers - 1 else None
            outs, hT, _ = gru_layer(seq, h0l, params[l][d], hidden_size, drop, paper_variant)
            if d == 1:
                outs = [outs[T - 1 - t] for t in range(T)]
            dirs.append(outs)
            h_n.append(hT)
        layer_in = [
            [dirs[0][t][n] + (dirs[1][t][n] if D == 2 else []) for n in range(N)] for t in range(T)
        ]
    return layer_in, h_n


def to_batch_first(x):
    T, N = len(x), len(x[0])
    return [[x[t][n] for t in range(T)] for n in range(N)]


def jacobian_diag_step(p, hidden_size, h, x):
    """在 reset gate r=0 时 ∂h_t/∂h_{t-1} 的对角线恰为 z_t（数值验证用）。"""
    _hp, _r, z, _n = gru_cell(x, h, p, hidden_size)
    return z
