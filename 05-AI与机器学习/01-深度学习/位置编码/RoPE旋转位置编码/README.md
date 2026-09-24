# RoPE 旋转位置编码

transformers `modeling_rope_utils.py` 与 `models/llama/modeling_llama.py` 的逐行转写：把「位置」编码成**旋转矩阵**，使注意力分数只依赖相对位置 `n − m`。

## 一、它解决什么问题

绝对位置编码（正弦 / 学习式）把位置**加**到 token 向量上，注意力分数里会出现 `p_m^T W p_n` 这类绝对项。RoPE 改为对 q、k 做**旋转**：

```
q̃_m = R(m) q ,   k̃_n = R(n) k     ⇒   ⟨q̃_m, k̃_n⟩ = q^T R(n−m) k
```

分数只依赖 `n − m`，且旋转是正交变换，**不改变向量范数**。

## 二、源码里的实现（与教科书的两处差异）

### 2.1 inv_freq 只有 dim/2 个

```python
inv_freq = 1.0 / (base ** (torch.arange(0, dim, 2, dtype=torch.float) / dim))
```

`base` 默认 `10000`，`dim = head_dim`。第 `i` 个通道对的角频率是 `base^(−2i/dim)`，所以 `inv_freq[0] = 1`（波长 `2π ≈ 6.28`），最长波长是 `2π·base^((dim−2)/dim)`。

### 2.2 `emb = cat(freqs, freqs)` 与 `rotate_half` 配套

```python
freqs = (inv_freq_expanded @ position_ids_expanded).transpose(1, 2)
emb   = torch.cat((freqs, freqs), dim=-1)          # cos/sin 长度 = dim
cos, sin = emb.cos() * attention_scaling, emb.sin() * attention_scaling

def rotate_half(x):
    x1 = x[..., : x.shape[-1] // 2]
    x2 = x[..., x.shape[-1] // 2 :]
    return torch.cat((-x2, x1), dim=-1)

q_embed = (q * cos) + (rotate_half(q) * sin)
```

因为 `emb` 是两份 `freqs` 拼起来的，**后半段与前半段逐位相同**（本 demo 实测 `cos[p][:4] == cos[p][4:]`），所以 `rotate_half` 的「前半取负、后半取正」恰好让第 `i` 个通道对用同一个角度 `θ_i · p` 做二维旋转。

> 教科书写法是 `[[cos, −sin], [sin, cos]]` 的 2×2 块对角矩阵；`rotate_half` 是它的等价实现，但**只在 dim 为偶数时** `rotate_half` 两次等于取负。dim 为奇数时两半不等长（本 demo 对 length=5 单独断言），性质不再成立。

## 三、六种 rope_type 的参数化

`ROPE_INIT_FUNCTIONS` 把 `rope_type` 映射到计算 `inv_freq` 的函数：

| rope_type | 核心公式 | 本 demo 的关键断言 |
| --- | --- | --- |
| `default` | `inv_freq = 1 / base^(2i/dim)` | `dim=8` → `[1, 0.1, 0.01, 0.001]` |
| `linear` | `inv_freq /= factor` | 缩 inv_freq ≡ 缩 position_ids（f=2,p=6 ↔ p=3） |
| `dynamic` | `base *= ((factor·L/L₀) − (factor−1))^(dim/(dim−2))` | `L = L₀` 时恒等；`factor=1` 退化为纯 `(L/L₀)^(...)` |
| `yarn` | 低频段外推、高频段内插、中间线性斜坡 | `i=0` 等于 default，`i=31` 等于 `default/factor` |
| `llama3` | 按波长分三档，中频平滑插值 | 中频段下标恰为 `[21,22,23,24]` |
| `proportional` | 分母用 `head_dim`，未旋转段补 0 | `proportion=0.5` 时后两位为 0 |

### 3.1 dynamic NTK 的 `(dim/(dim−2))` 指数

`seq_len` 先被 `max_position_embeddings` 兜住（源码 `max(seq_len, config.max_position_embeddings)`），所以**短序列不会缩 base**。指数是 `dim/(dim−2)` 而不是 1：实测 `max=8192, factor=4` 时 `seq_len=16384 → base≈52664`、`32768 → 141214`、`65536 → 323275`。

### 3.2 YaRN 的分段逻辑

```
pos_freqs              = base ** (arange(0,dim,2)/dim)
inv_freq_extrapolation = 1 / pos_freqs              # 不缩放
inv_freq_interpolation = 1 / (factor * pos_freqs)   # 内插
low, high = find_correction_range(beta_fast=32, beta_slow=1, ...)
ramp      = clamp((arange − low)/(high − low), 0, 1)   # 长度 dim/2
f         = 1 − ramp
inv_freq  = interp * (1 − f) + extrap * f
```

`find_correction_dim` 的 `num_rotations` 越小 → 校正维度越大，因此 `beta_fast=32` 给 `low`、`beta_slow=1` 给 `high`（`dim=64, base=10000, L₀=8192` 时 truncate 后是 `(12, 25)`）。于是**低频通道（下标小）外推、高频通道（下标大）内插**，中间 12~25 线性过渡——与 YaRN 论文「低频外推、高频内插」一致。

`attention_factor` 默认取 `get_mscale(factor) = 0.1·ln(factor) + 1`（`factor ≤ 1` 时恒为 1），它会被乘到 `cos`/`sin` 上。

### 3.3 llama3 的三档判据

```
wavelen = 2π / inv_freq
low_freq_wavelen  = old_context / low_freq_factor    # 8192 / 1
high_freq_wavelen = old_context / high_freq_factor   # 8192 / 4
inv_freq_llama = where(wavelen > low_freq_wavelen, inv_freq / factor, inv_freq)
smooth         = (old_context / wavelen − low_freq_factor) / (high_freq_factor − low_freq_factor)
is_medium      = ~(wavelen < high_freq_wavelen) & ~(wavelen > low_freq_wavelen)
```

**边界是开区间**：`wavelen` 正好等于 `8192` 时不算低频（判据是 `>`），正好等于 `2048` 时算中频（`~(<)`）。`base=10000, dim=64` 下中频通道恰好是下标 `[21,22,23,24]`。中频的 `smoothed` 是对「已分档后的值」再除一次 `factor` 后线性混合——注意是 `inv_freq_llama / factor`，不是原始的 `inv_freq / factor`。

## 四、实测结论

- **相对性**：随机 16 维 q/k，`(m,n) = (0,4)/(5,9)/(100,104)` 三组的分数完全相同（`+1.030313044`），而 `(0,5)` 给出 `+0.508189646` —— 负控成立。
- **保范数**：旋转前后 `‖q‖` 不变（实测 `4.080441153`）。
- **position 0 是恒等**：`cos ≡ 1, sin ≡ 0`，该位置原样返回。

## 五、代码结构

| 文件 | 说明 |
| --- | --- |
| `python/rope.py` | 六种 `inv_freq` + `rotary_emb` + `rotate_half` + `apply_rotary_pos_emb` |
| `python/selfcheck_rope.py` | 62 条断言（实跑全绿） |
| `python/main.py` | 演示入口：inv_freq / 相对位置 / 外推 / YaRN / llama3 |
| `go/rope.go` + `go/main.go` | Go 同题实现（无本机工具链，走机械核查） |

运行：

```bash
cd python && python selfcheck_rope.py && python main.py
```

## 六、参考资料（实际读过）

- `huggingface/transformers@main` — `src/transformers/modeling_rope_utils.py`（61.7 KB，含 `_compute_linear_scaling_rope_parameters` / `_compute_dynamic_ntk_parameters` / `_compute_yarn_parameters` / `_compute_llama3_parameters` / `_compute_proportional_rope_parameters` / `ROPE_INIT_FUNCTIONS`）
- `huggingface/transformers@main` — `src/transformers/models/llama/modeling_llama.py`（`LlamaRotaryEmbedding.compute_default_rope_parameters` / `forward`、`rotate_half`、`apply_rotary_pos_emb`）
- Su et al., *RoFormer: Enhanced Transformer with Rotary Position Embedding*, arXiv:2104.09864
- Peng et al., *YaRN: Efficient Context Window Extension*, arXiv:2309.00071（源码注释里给出的原始论文链接）
