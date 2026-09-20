# 461 · wav2Vec 2.0 对比学习与乘积量化

## 简介

wav2vec 2.0 的核心思路是：**先把连续语音离散化成一组「伪音素」，再用 BERT 式的掩码预测去学上下文表示**。三步：

1. 卷积特征编码器把波形压成 49 Hz 的潜在表示 `z`；
2. **量化模块**把 `z` 离散成 `q`（Gumbel softmax + 乘积量化）；
3. 掩码掉一部分 `z` 送进 Transformer，让上下文输出 `c` 在一堆候选里**认出真正的 `q`**（对比任务 `Lm`），再加一个多样性损失 `Ld` 防止码本塌缩。

本 demo 按论文原文公式与 fairseq 官方源码实现这三步，48 条断言全部实跑。掩码统计实测 **49.45 % / 平均跨度 14.72**，与论文 §4.2 报告的「约 49 % / 14.7」吻合。

## 原理详解

### 1. 掩码：跨度可重叠（§3.1）

```
不放回地采样 p 比例的时间步作为起点，从每个起点起掩码后续 M 步；跨度可以重叠。
```

官方取值 `p = 0.065`、`M = 10`。注意 `p` 是**起点比例**而不是被掩码比例，两者差很多：

| 量 | 值 | 来源 |
| --- | --- | --- |
| 起点比例 `p` | 0.065 | 论文 §4.2 |
| 跨度长度 `M` | 10 | 论文 §4.2 |
| 实际被掩码比例 | ≈ 49 %（理论 `1−(1−p)^M = 48.5 %`，实测 49.45 %） | 本 demo `A1`/`A3` |
| 平均跨度长度 | 14.7（实测 14.72） | 论文 §4.2 / 本 demo `A2` |
| 平均跨度时长 | 299 ms | 论文 §4.2 |

跨度因为**可以重叠**而变长：单独一段是 10 步，但相邻起点挨得近时两段会连成一整段，平均下来是 14.7。fairseq 里 `mask_prob` 的默认值是 **0.65**（含义与论文的 `p` 不同口径），本 demo 按论文的 `p = 0.065` 实现。

### 2. 对比损失（§3.2 式 3）

```
Lm = −log  exp(sim(ct, qt)/κ) / Σ_{q̃ ∈ Qt} exp(sim(ct, q̃)/κ)
```

- `Qt` 包含真实量化表示 `qt` 与 **K = 100 个干扰项**；
- 干扰项「从同一条语音的其他掩码时间步**均匀采样**」（论文 §3.2），fairseq 的 `num_negatives = 100`；
- `sim` 是**余弦相似度**，不是点积；
- `κ = 0.1`（论文 §4.2，fairseq 的 `logit_temp`）—— 余弦相似度值域只有 `[-1,1]`，不除以 0.1 的话 softmax 几乎拉不开差距。

两个可算的解析值（本 demo `B1`/`B2`）：

- 所有候选相似度相同 → `Lm = ln(K+1) = ln 101`；
- 完美上下文（正样本 `cos=1`、100 个干扰项 `cos=0`）→ `Lm = ln(1 + 100·e^{−10}) ≈ 4.53×10⁻³`。注意**精确值是 `log1p(100·e^{−10})`**，一阶近似 `100·e^{−10}` 差了约 1e-5 —— 一开始写成后者导致断言失败。

`κ` 越大分布越平、loss 越高（`B3`）。

### 3. 干扰项采样里的 `+1` 技巧

fairseq `sample_negatives`：

```python
neg_idxs = torch.randint(low=0, high=high - 1, size=(bsz, self.n_negatives * num))
neg_idxs[neg_idxs >= tszs] += 1
```

`tszs` 是每个采样位置自身的时间下标。**凡是采到「≥ 自身位置」的下标就 +1**，这样就把自身位置从候选里排除掉了（`high−1` 则给 +1 留出空间）。本 demo 断言：60 个位置 × 100 个负样本，**没有任何一个负样本等于自身位置**（`C2`）。

还有一个 `neg_is_pos` 兜底：即便某个负样本与正样本**数值上完全相同**，也会把它的 logit 置成 `-inf`（`C4`/`C5`），避免正样本被算成自己的负样本。

### 4. 乘积量化与 Gumbel softmax（§2）

```
G 个码本（组），每组 V 个条目 e ∈ R^(V×d/G)；
每组各选一个条目，拼接后线性变换 R^d → R^f 得到 q。
```

选择概率用 Gumbel softmax：

```
p_g,v = exp(l_g,v + n_v) / Σ_k exp(l_g,k + n_k),   n = −log(−log(u)), u ~ U(0,1)
```

前向用 `argmax` 直接取码字，反向用 Gumbel softmax 的真梯度（straight-through）。官方取值 `G = 2`、`V = 320`，于是**理论上界 320² = 102,400 个码字**（论文写作 102.4k），条目维度 `d/G = 128`（BASE）/ `384`（LARGE）。

**码本下标的编号约定**（fairseq `to_codebook_index`）：

```
res += indices[..., i] * num_vars ** (groups − i − 1)
```

即**第 0 组是高位**：`to_codebook_index([5, 7]) = 5×320 + 7 = 1607`。另外 `get_codebook_indices` 在 `combine_groups=False` 时会给第 `b` 组整体加上 `V·b` 的偏移。

**温度退火**：`latent_temp = (2, 0.5, 0.999995)`，每步更新

```
curr_temp = max(max_temp * decay ** num_updates, min_temp)
```

从 2 开始按 0.999995 衰减，下限 0.5（BASE）/ 0.1（LARGE）。降到下限需要 `ln(0.25)/ln(0.999995) ≈ 27.7 万` 步（本 demo `E6`），与 BASE 训练 400k 步、LARGE 250k 步的规模相称。温度高时选择更「软」，温度降下来后逐渐逼近 one-hot。

### 5. 两个 perplexity 与多样性损失

fairseq 报告两个 perplexity（都在**组维度上求和**）：

| 指标 | 用什么分布 | 含义 |
| --- | --- | --- |
| `code_perplexity` | **hard** one-hot 分配的批次平均 | 实际用到了多少个码字 |
| `prob_perplexity` | **softmax** 概率（无 Gumbel 噪声、无温度）的批次平均 | 分布本身有多平 |

理论上限是 `V`（每组），所以两个指标满值是 **2V = 640**。全部撞到同一个条目时降到 **2**。注意 fairseq 的熵里带了 `+1e-7` 平滑项，所以「全部相同」得到的是 `2 − 2e-7` 而不是严格 2（本 demo `F4` 按 1e-5 容差断言）。

**多样性损失**（§3.2 式 4）：

```
Ld = (1/GV) Σ_g Σ_v p̄_g,v log p̄_g,v
```

均匀分布时 `Ld = −ln(V)/V`（本 demo `F6`）。论文脚注 2 说明**实现里改成最大化 perplexity**：

```
(GV − Σ_g exp(−Σ_v p_g,v log p_g,v)) / GV
```

这个形式在均匀分布时取 **0**（已最优），集中分布时为正（本 demo `F7`/`F8`）—— 与式(4) 只是符号与基准的平移，但方向相反，读源码时容易看反。总目标 `L = Lm + α·Ld`，`α = 0.1`。

### 6. 特征编码器（§4.2）

```
7 个块，每块 512 通道
strides      = (5, 2, 2, 2, 2, 2, 2)
kernel widths= (10, 3, 3, 3, 3, 2, 2)
→ 输出频率 49 Hz，每帧约 20 ms，感受野 400 样本（25 ms）
```

逐层套 `floor((L − k)/s + 1)`：16000 → 3199 → 1599 → 799 → 399 → 199 → 99 → **49**（本 demo `G3`，实测正好 49，即 49 Hz）。卷积位置嵌入 kernel size 128、16 组。

## 对比：wav2vec 2.0 vs BERT 式 MLM vs CPC

| 维度 | BERT MLM | CPC / wav2vec 1.0 | wav2vec 2.0 |
| --- | --- | --- | --- |
| 预测目标 | 词表上的分布（softmax） | 未来的连续表示（回归 / 噪声对比） | **量化后的离散码字**（分类） |
| 目标从哪来 | 人工 tokenizer | 无 | **模型自己学出来的码本** |
| 负样本 | 无（词表即负样本） | 需额外构造 | 同句其他掩码步（`K=100`） |
| 额外正则 | 无 | 无 | 多样性损失 `Ld` 防码本塌缩 |

## 环境

Python 3.13（标准库 `math` / `random`）；Go 1.20+（标准库）。

## 运行方式

```bash
cd 05-AI与机器学习/06-语音与多模态/05-Wav2Vec2对比学习与量化
python selfcheck_wav2vec2.py     # 48 条断言实跑
go run wav2vec2.go selfcheck_wav2vec2.go
```

## 关键代码

```python
def contrastive_loss(ct, qt, negatives, kappa=0.1):
    sims = [cosine(ct, q) / kappa for q in [qt] + negatives]
    m = max(sims)
    z = sum(math.exp(s - m) for s in sims)   # 先减最大值防溢出
    return -(sims[0] - m - math.log(z))
```

## 性能边界

- 掩码比例 49 % 意味着**近一半**的 Transformer 位置要做对比预测，每步要算 `K+1 = 101` 次余弦相似度 —— 这是预训练的主要算力开销。
- 对比损失必须走 log-sum-exp：直接算 `exp(sim/κ)`，在 `κ=0.1` 下最大可达 `e^10 ≈ 22026`，累加上百项仍在 float32 范围内，但长序列要防溢出。
- 码本 102,400 个码字只是**理论上界**；实际用到多少看 `code_perplexity`。论文靠 `Ld` 把它推高。
- 论文 §4.2 的训练规模：BASE 在 64 张 V100 上 1.6 天（batch 1.6 小时音频），LARGE 在 128 张 V100 上 2.3 天（Librispeech）/ 5.2 天（LibriVox）。
- 本 demo 用 `d = 8` 的小向量、`T = 20000` 的掩码做验证；真实维度是 768/1024。

## 注意事项与常见坑

1. **论文的 `p = 0.065` 是起点比例**，不是被掩码比例；被掩码比例是 `1−(1−p)^M ≈ 49 %`。fairseq 的 `mask_prob = 0.65` 是另一套口径，别混。
2. **跨度可重叠**，所以平均跨度（14.7）大于 `M`（10）。
3. **干扰项来自「同一条语音的其他掩码步」**，不是整批随机 —— 跨句采样会让任务变简单（说话人/信道差异成了捷径）。
4. **`κ = 0.1` 不能省**：余弦相似度值域窄，不缩放则 softmax 几乎均匀。
5. **`neg_is_pos` 必须处理**，尤其码本塌缩时大量负样本会与正样本相同。
6. **码本下标是高位在前**（`V^(G−i−1)`），写反会让 `forward_idx` 与码本错配。
7. **多样性损失的两个形式方向相反**：式(4) 越小越好，脚注 2 的实现形式越大越好。
8. `code_perplexity` / `prob_perplexity` 都是**按组求和**，满值是 `G·V` 而不是 `V`。

## 参考资料

- A. Baevski, Y. Zhou, A. Mohamed, M. Auli. *wav2vec 2.0: A Framework for Self-Supervised Learning of Speech Representations*. NeurIPS 2020, arXiv:2006.11477（§3.1 掩码、§3.2 式(3)(4) 与脚注 2、§4.2 全部超参）：<https://arxiv.org/abs/2006.11477>（本轮下载原 PDF 19 页后逐节抽取回读）
- Facebook fairseq 官方实现 `fairseq/models/wav2vec/wav2vec2.py`（`logit_temp=0.1`、`latent_vars=320`、`latent_groups=2`、`mask_length=10`、`mask_prob=0.65`、`num_negatives=100`、`latent_temp=(2,0.5,0.999995)`、`compute_preds`、`sample_negatives`）：<https://github.com/facebookresearch/fairseq/blob/main/fairseq/models/wav2vec/wav2vec2.py>
- 同上 `fairseq/modules/gumbel_vector_quantizer.py`（`code_perplexity` / `prob_perplexity` / `set_num_updates` / `to_codebook_index` / `get_codebook_indices`）：<https://github.com/facebookresearch/fairseq/blob/main/fairseq/modules/gumbel_vector_quantizer.py>
- 本轮实际读取走 jsDelivr：`cdn.jsdelivr.net/gh/facebookresearch/fairseq@main/fairseq/{models/wav2vec/wav2vec2.py,modules/gumbel_vector_quantizer.py}`
- D. S. Park et al. *SpecAugment*（论文 §3.3 微调阶段用的改进版时频掩码）。
