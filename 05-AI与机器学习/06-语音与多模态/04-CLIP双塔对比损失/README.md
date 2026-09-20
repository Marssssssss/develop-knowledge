# 460 · CLIP 双塔对比损失与 logit scale

## 简介

CLIP 把图像和文本各编码成一个向量，投到同一个多模态嵌入空间，然后在一批 `N` 个**图文对**上做对比学习：最大化 `N` 个真实配对的余弦相似度，同时最小化 `N² − N` 个错误配对的相似度。损失函数是**对称交叉熵**——论文 Figure 3 给了不到十行的 numpy 伪代码，本 demo 把它与官方 `clip/model.py` 的实现逐行对齐，并用 35 条断言验证。

## 原理详解

### 1. 官方伪代码（论文 Figure 3）

```python
# I[n, h, w, c] - minibatch of aligned images
# T[n, l]       - minibatch of aligned texts
# W_i[d_i, d_e] - learned proj of image to embed
# W_t[d_t, d_e] - learned proj of text to embed
# t             - learned temperature parameter
I_f = image_encoder(I)      # [n, d_i]
T_f = text_encoder(T)       # [n, d_t]
I_e = l2_normalize(np.dot(I_f, W_i), axis=1)
T_e = l2_normalize(np.dot(T_f, W_t), axis=1)
logits = np.dot(I_e, T_e.T) * np.exp(t)     # [n, n] 缩放后的两两余弦相似度
labels = np.arange(n)
loss_i = cross_entropy_loss(logits, labels, axis=0)
loss_t = cross_entropy_loss(logits, labels, axis=1)
loss   = (loss_i + loss_t) / 2
```

四个要点：

1. **两路都做 L2 归一化**，所以矩阵乘法直接就是**余弦相似度**，值域 `[-1, 1]`；
2. **只有一个线性投影** `W_i` / `W_t`。论文 §2.3 明确说去掉了 Zhang et al. (2020) 的非线性 projection ——「我们没有注意到两种版本的训练效率差异」，而自监督图像方法里常用的非线性头被认为可能与「仅图像」的细节共适应；
3. **没有额外的温度超参数**：`t` 是**可学习**的，用 log 参数化的乘性标量直接优化，这样就不必当超参调（论文原话：*"directly optimized during training as a log-parameterized multiplicative scalar to avoid turning as a hyper-parameter"*）；
4. **`logits[i][j]` 的语义**：第 `i` 张图与第 `j` 条文本的相似度，正样本在对角线。

### 2. 对称交叉熵：axis 不是装饰

`axis=0` 与 `axis=1` 是两个**不同**的 softmax 方向：

| 调用 | softmax 方向 | 物理含义 |
| --- | --- | --- |
| `axis=0` | 沿**行**归一化（对每一列） | 给定文本 `j`，在 `n` 张图里挑出正确的那张 |
| `axis=1` | 沿**列**归一化（对每一行） | 给定图片 `i`，在 `n` 条文本里挑出正确的那条 |

两者**一般不相等**（本 demo `C4` 断言），最终 loss 取两者平均。这不是「为了对称好看」：两个方向的负样本集合不同（一个是 `n−1` 张图，一个是 `n−1` 条文本），梯度也不同。

官方 `model.py` 只返回 `logits_per_image` 与 `logits_per_text = logits_per_image.t()`，**损失计算不在开源推理代码里**，所以本 demo 的 loss 部分以论文伪代码为准。

### 3. logit scale：初始化 0.07，clip 到 100

`clip/model.py`：

```python
self.logit_scale = nn.Parameter(torch.ones([]) * np.log(1 / 0.07))
...
logit_scale = self.logit_scale.exp()
logits_per_image = logit_scale * image_features @ text_features.t()
logits_per_text = logits_per_image.t()
```

- 初始化成 `ln(1/0.07)`，于是 `exp(t) ≈ 14.2857`。0.07 这个数来自 Wu et al. 2018；
- 论文 §2.3：*"clipped to prevent scaling the logits by more than 100, which we found necessary to prevent training instability"* —— **上限 100 是训练稳定性的硬需求**，不是随手写的；
- 注意 `model.py` 的 `forward` **没有**做 clip（开源版是推理代码），clip 只在训练循环里。

### 4. 温度对 loss 的影响不是单调的

这点很容易想反。直觉上「scale 越大 → 分布越尖 → loss 越低」，但**只有在正样本确实是最大值时才成立**：

- **对齐的批**（正样本就是每行每列的最大）：scale 从 1 提到 80，loss **单调下降**（本 demo `D3`）；
- **随机的批**（正样本未必最大）：scale 越大越放大错误的排序，loss **反而上升**（本 demo `D4`，实测 2.07 → 24.3）。

所以训练初期特征还很随机时，一个过大的 `logit_scale` 会把梯度推向错误方向 —— 这正是要 clip 的原因之一。

### 5. 两个可算的解析值

- **完美对齐**：`loss = −log(e^s / (e^s + n − 1)) ≈ (n−1)·e^{−s}`。取 `n=8`、`s≈14.2857` 时约 `4.37e−6`，本 demo 按这个公式精确断言（`C5`）；
- **scale → 0**：所有 logits 都变成 0，两个方向的交叉熵都恰好等于 `ln(n)`（`C1`、`C2`）。

反过来，**随机特征的 loss 可以超过 `ln(n)`**（本 demo 实测 4.77 vs `ln 8 = 2.08`），因为负样本的余弦相似度完全可能压过正样本。一开始写成「随机特征 loss < ln(n)」全部失败。

### 6. 梯度

对 logits 的梯度就是两个方向 `(softmax − onehot)` 的平均：

```
∂L/∂logits[i][j] = ½ · ( (p_col_j[i] − δ_ij)/n + (p_row_i[j] − δ_ij)/n )
```

两个分量各自「按列求和为 0」「按行求和为 0」，但**合成后逐行/逐列求和不再是 0**（本 demo `F3`/`F4` 分别断言两个分量，`F5` 断言合成关系）。对标量 `t` 的梯度由链式法则得 `∂L/∂t = Σ (∂L/∂logits · cos) · e^t`，本 demo 用中心差分核对（`F1`，误差 < 1e-6）。

## 对比：CLIP 式对比 vs 其他对比目标

| 目标 | 负样本来源 | CLIP 的取舍 |
| --- | --- | --- |
| 多类 N-pair loss（Sohn 2016） | 批内其他样本 | CLIP 直接采用（论文自述出处） |
| InfoNCE（Oord 2018） | 批内 + 记忆库 | CLIP 不用记忆库，靠 32768 的超大批 |
| 生成式（预测图像/文本） | — | 论文 §2.3 实测对比目标效率高 4 倍（Figure 2） |
| 词袋对比（BoW） | 同 | 论文作为 baseline，效率介于两者之间 |

## 环境

Python 3.13（标准库 `math` / `random`）；Go 1.20+（标准库）。

## 运行方式

```bash
cd 05-AI与机器学习/06-语音与多模态/04-CLIP双塔对比损失
python selfcheck_clip.py     # 35 条断言实跑
go run clip.go
```

## 关键代码

```python
def cross_entropy_loss(logits, labels, axis):
    if axis == 0:                                  # 每个文本在所有图片上
        for j in range(n):
            col = [logits[i][j] for i in range(n)]
            p = softmax_row(col)
            total += -log(p[labels[j]])
    else:                                          # 每个图片在所有文本上
        for i in range(n):
            p = softmax_row(logits[i])
            total += -log(p[labels[i]])
    return total / n
```

## 性能边界

- 相似度矩阵是 `n × n`，官方 batch `n = 32768` ⇒ **约 10.7 亿**个配对（`N² − N`），正样本只有 32768 个。这个量级必须靠多 GPU 分片算相似度（论文 §2.5 提到的实现要点）。
- 矩阵乘法本身是 `O(n²d)`；真正压内存的是 `n²` 的 logits（32768² × 2 bytes ≈ 2 GiB，半精度）。
- 论文 §2.4 的训练规模：RN50x64 在 592 张 V100 上训 18 天，最大的 ViT 在 256 张 V100 上训 12 天。
- 本 demo 用 `n = 8`、`d = 16` 的随机单位向量做验证，纯 Python 直接算；真实规模必须走向量化与分布式。

## 注意事项与常见坑

1. **两路都要 L2 归一化**，否则点积不是余弦相似度，量纲会主导 logits。
2. **`axis=0` 与 `axis=1` 不能只算一个**，两个方向的负样本集合不同。
3. **`logit_scale` 要 clip 到 100**，且 clip 只在训练侧；开源 `model.py` 的 `forward` 里没有。
4. **增大 scale 不总是降低 loss**：只在正样本已经排第一时成立。
5. **随机特征的 loss 可以 > `ln(n)`**，不要把它当 bug。
6. **`labels = arange(n)`** 假设第 `i` 张图与第 `i` 条文本配对，批内顺序不能打乱。
7. 官方没有非线性 projection head；自己加 MLP 会偏离 CLIP 的原始配方。
8. 合成梯度**逐行求和不为 0**，只有拆成两个单向分量才各自为 0。

## 参考资料

- A. Radford et al. *Learning Transferable Visual Models From Natural Language Supervision*. arXiv:2103.00020（Figure 3 伪代码、§2.3 的温度初始化 0.07 与 clip 到 100、§2.4 训练规模、§2.5 batch 32,768）：<https://arxiv.org/abs/2103.00020>（本轮下载原 PDF 48 页后逐节抽取回读）
- OpenAI CLIP 官方实现 `clip/model.py`（`logit_scale = nn.Parameter(torch.ones([]) * np.log(1 / 0.07))`、`forward` 里的 L2 归一与 `logits_per_text = logits_per_image.t()`）：<https://github.com/openai/CLIP/blob/main/clip/model.py>（本轮实际读取走 jsDelivr：`cdn.jsdelivr.net/gh/openai/CLIP@main/clip/model.py`）
- K. Sohn. *Improved Deep Metric Learning with Multi-class N-pair Loss Objective*（N-pair loss，论文自述的对比目标出处）。
- A. van den Oord, Y. Li, O. Vinyals. *Representation Learning with Contrastive Predictive Coding*（InfoNCE）。
- Z. Wu et al. *Unsupervised Feature Learning via Non-parametric Instance Discrimination*（温度 0.07 的出处）。
