# GRU 门控机制

`pytorch/pytorch` → `torch/nn/modules/rnn.py`（`GRU` 1206 行、`GRUCell` 1771 行、`RNNBase` 48 行）的逐行转写。重点不是「再实现一个 GRU」，而是把**源码注释里那句「与原论文不同」**钉死。

## 一、四式与门序

```
r_t = σ(W_ir x_t + b_ir + W_hr h_{(t-1)} + b_hr)      # reset 门
z_t = σ(W_iz x_t + b_iz + W_hz h_{(t-1)} + b_hz)      # update 门
n_t = tanh(W_in x_t + b_in + r_t ⊙ (W_hn h_{(t-1)} + b_hn))
h_t = (1 − z_t) ⊙ n_t + z_t ⊙ h_{(t-1)}
```

`RNNBase.__init__` 里 `gate_size` 按类型取值：LSTM `4H`、**GRU `3H`**、RNN `H`。权重是**按门纵向拼接**的：

```
weight_ih : (3H, input_size)   行序 (W_ir | W_iz | W_in)
weight_hh : (3H, H)            行序 (W_hr | W_hz | W_hn)
```

本 demo 直接断言 `w_ih[0]` 是 `W_ir` 的第 0 行、`w_ih[H]` 是 `W_iz`、`w_ih[2H]` 是 `W_in`（用 `is` 判同一对象，防止切分错位）。

初始化：`stdv = 1/√hidden_size`，所有 weight 与 bias 都取 `U(−stdv, stdv)`（`hidden_size ≤ 0` 时短接到 0）。**bias 也在这个范围内**，不是常见的初始化为 0 或 1。

## 二、源码自承的「与原论文不同」

`rnn.py` 的 `GRU` docstring 末尾有一段 Note：

> The calculation of new gate `n_t` **subtly differs** from the original paper and other frameworks. In the original implementation, the Hadamard product between `r_t` and the previous hidden state is done **before** the multiplication with the weight matrix `W` and addition of bias:
> `n_t = tanh(W_in x_t + b_in + W_hn (r_t ⊙ h_{(t-1)}) + b_hn)`

即：

| 写法 | n_t |
| --- | --- |
| PyTorch | `tanh(W_in x + b_in + r ⊙ (W_hn h + b_hn))` |
| Cho 2014 | `tanh(W_in x + b_in + W_hn (r ⊙ h) + b_hn)` |

差别在于 **reset 门乘在「线性层之后」还是「之前」**，以及 `b_hn` 是否被 `r` 缩放。本 demo 用一组非对角 `W_hn`（`n_0` 只依赖 `h_1`）+ `b_hn = 1` 的参数把两者分开：

```
r = σ(0) = 0.5,  h_1 = −0.7
PyTorch : tanh(0.5 · (−0.7 + 1)) = tanh(0.15) = 0.148885034
原论文  : tanh(0.5 · (−0.7) + 1) = tanh(0.65) = 0.571669966
```

成对负控：`b_hn = 0` 且 `W_hn` 非对角时两者**又相等**（都退化为 `tanh(0.5·h_1)`）；`W_hn` 为对角阵时 `r ⊙ (Wh) ≡ W(r ⊙ h)`，两者也相等。所以这个差异只在「`W_hn` 非对角 **且** `b_hn ≠ 0`」时才暴露——这也是它容易被忽略的原因。

## 三、两个门各自的语义

- **`z → 1`：状态直通**。`h_t = h_{t−1}`，`n_t` 完全被忽略（实测 `h' == h` 逐位相等）。
- **`z → 0`：完全重写**。`h_t = n_t`。
- **`z = 0.5`：等权混合**（`b = 0` → `σ(0) = 0.5`）。
- **`r → 0`：切断历史**。`n_t` 里的 `W_hn h + b_hn` 被整体抹掉，换任意 `h` 结果都不变（实测两组完全不同的 `h` 给出同一个 `n`）。

## 四、长程梯度

`h_t = (1 − z_t) ⊙ n_t + z_t ⊙ h_{t−1}` 对 `h_{t−1}` 求导，在 `r = 0`（`n` 与 `h` 无关）**且** `W_hz = 0`（`z` 与 `h` 无关）时精确等于 `z_t`：

```
∂h_T / ∂h_0 = ∏_{t=1..T} z_t
```

本 demo 用有限差分实测（`H=1, T=5`）：

| z | 数值导数 | z⁵ |
| --- | --- | --- |
| σ(1.2) = 0.768525 | 0.268095415 | 0.268095415 |
| σ(2.4) = 0.916827 | 0.647795149 | 0.647795149 |
| σ(5.0) = 0.993307 | 0.966980700 | 0.966980700 |
| σ(40) ≈ 1 | 1.000000000 | 1.000000000 |

数值导数与 `z⁵` 逐位吻合（差 < 1e-5）。**乘性衰减**：`z = 0.7685` 时 5 步只剩 26.8%，`z = 0.9168` 时剩 64.8%。GRU 没有 LSTM 那样的加性细胞状态通路（`c_t = f⊙c_{t−1} + i⊙g` 里 `f → 1` 时梯度可以恒等直传），长程依赖只能靠 `z → 1` 撑住——这也是实践中 GRU 的 update gate bias 常被初始化为较大正值的原因（本 demo 只记录源码语义，不推断初始化策略的实现细节）。

## 五、序列级语义

| 性质 | 实测结论 |
| --- | --- |
| `h_n` 与 output | 单层单向时 `h_n == output[T−1]` |
| `batch_first` | 转置后结果逐位相同 |
| 双向 | `output[..., :H]` 是正向、`[..., H:]` 是反向；`h_n[0]` 是正向 `h_T`，`h_n[1]` 是反向层（倒序喂入）的 `h_T` |
| 多层 | 第 `l` 层的输入 = 第 `l−1` 层的输出 |
| dropout | **只加在除最后一层之外**的层输出上（源码 `dropout` 参数说明），所以单层 GRU 的 dropout 掩码恒不起作用 |
| `GRUCell` 维数 | input 必须 1D/2D，否则 `ValueError: Expected input to be 1D or 2D, got 3D instead` |

> **关于 peephole**：本轮实读的 `rnn.py` 中，GRU 只有 `r / z / n` 三个门，`RNNBase` 与 `GRUCell` 的参数列表里都不存在任何 peephole 权重。因此本 demo **不实现 peephole 变体**——源码里没有的东西不臆造。

## 六、代码结构

| 文件 | 说明 |
| --- | --- |
| `python/gru.py` | `gate_size` / `stdv` / `gru_cell` / `gru_layer` / 多层双向 `gru` |
| `python/selfcheck_gru.py` | 56 条断言（实跑全绿） |
| `python/main.py` | 演示入口 |
| `go/gru.go` + `go/main.go` | Go 同题实现（无本机工具链，走机械核查） |

运行：

```bash
cd python && python selfcheck_gru.py && python main.py
```

## 七、参考资料（实际读过）

- `pytorch/pytorch@main` — `torch/nn/modules/rnn.py`（76.1 KB）：`RNNBase.__init__` 的 `gate_size` 三分支（159–165 行）、`reset_parameters` 的 `stdv`（308–311 行）、`GRU` docstring 的四式与「与原论文不同」Note（1213–1315 行）、`GRUCell` 四式与维数校验（1771–1860 行）
- Cho et al., *Learning Phrase Representations using RNN Encoder–Decoder for Statistical Machine Translation*, arXiv:1406.1078（原式 `n_t` 的出处，经 PyTorch 注释指认）
- 本仓库 `05-AI与机器学习/01-深度学习/LSTM/`（ID 141）：LSTM 的 `∏f_t` 长程梯度与本 demo 的 `∏z_t` 对照
