# LSTM · 长短期记忆网络(Hochreiter & Schmidhuber 1997)

## 简介

vanilla RNN 反向传播每步乘 $W_{hh}^T$,梯度指数消失/爆炸,学不会长程依赖(Hochreiter 1991、Bengio 1994)。LSTM 引入**细胞状态** $C_t$ —— 一条只做加性更新的"传送带",配三道门(遗忘/输入/输出)控制读写擦除;反向穿过细胞路径只逐元素乘 $f_t$,**遗忘门≈1 时梯度无损** —— 与 ResNet 恒等捷径同构。本 demo 从零实现前向 5 式 + 解析 BPTT,数值梯度验证,并实测 50 步长程梯度对比。

- **细胞方程**(colah/CS231n 记号,$\sigma$ = sigmoid,$\odot$ = 逐元素乘):

$$f_t = \sigma(W_f[h_{t-1},x_t]+b_f),\quad i_t = \sigma(W_i[\cdot]),\quad \tilde{C}_t = \tanh(W_C[\cdot]+b_C),\quad o_t = \sigma(W_o[\cdot])$$

$$C_t = f_t \odot C_{t-1} + i_t \odot \tilde{C}_t,\qquad h_t = o_t \odot \tanh(C_t)$$

- 实现采用 CS231n 的**合并权重形式**:一个大矩阵 $W \in \mathbb{R}^{4h \times (h+d)}$ 一次算出 $[i,f,o,g]$,再分块过 $\sigma,\sigma,\sigma,\tanh$ —— 与 PyTorch `nn.LSTM` 内部一致。

## 原理详解

### 1. 为什么细胞状态能救梯度

vanilla RNN:$h_t = \tanh(W_{hh} h_{t-1} + \cdots)$,反向 $\partial h_T / \partial h_0 \approx \prod_t W_{hh}^T$,矩阵连乘 → 谱半径 ≠ 1 就指数失控。

LSTM:$C_t = f_t \odot C_{t-1} + (\text{新信息})$,反向 $\partial C_T / \partial C_t = \prod_{k>t} \text{diag}(f_k)$ —— **没有矩阵乘法**,只有逐元素乘 $f_k \in (0,1)$。$f \approx 1$(遗忘门偏置初始化为正即可)时梯度基本无损;且 $f$ 可学习,网络自己决定记多久。

### 2. 反向传播(BPTT,本 demo 逐项实现)

每步梯度汇流:$\partial C_t$ 有两条来路 —— $t+1$ 的细胞路径($f_{t+1} \odot$)与 $t+1$ 的隐状态路径($\partial C_{t+1}/\partial h_t$ 经 $o_{t+1}, \tilde{C}_{t+1}$ 两条门回路)。完整推导见 `LSTMCell.backward`:

$$dC_t^{\text{total}} = dC_{t+1\text{-cell}} + dh_{t+1} \cdot o_{t+1}(1-\tanh^2 C_{t+1})$$

四段门的 $dz$:各乘自己激活的导数 —— $i(1\!-\!i)$、$f(1\!-\!f)$、$o(1\!-\!o)$、$(1\!-\!g^2)$。

### 3. 实测结果(本 demo 运行输出)

- 手算 1 步单维 LSTM:C=0.581269、h=0.174895,与逐步手算逐位一致;
- BPTT 解析梯度 vs 中心差分:dW/db/dx 相对误差 **< 1e-6**;
- **T=50 长程梯度** $|\partial C_T/\partial C_0|$ 均值:LSTM(mean f=0.982,遗忘偏置+4)**≈ 0.409** vs vanilla RNN(谱半径 0.8,已属良好初始化)**≈ 4.3e-6**;
- 遗忘偏置 +2 时 mean f≈0.88,乘积 ≈1.6e-3 —— 仍比随机 $W_{hh}$(同尺度,~1e-22)好约 19 个数量级,且门值可学习。

## 对比

| | vanilla RNN | LSTM | GRU |
| --- | --- | --- | --- |
| 状态 | $h_t$ 单状态 | $C_t + h_t$ 分离 | 合并单状态 |
| 门 | 无 | 遗忘/输入/输出 3 门 | 更新/重置 2 门(遗忘+输入耦合) |
| 长程梯度 | $\prod W_{hh}^T$ 指数失控 | $\prod f_t$ 可控 | $\prod (1-z_t)$ 同理 |
| 参数 | $h(d+h)$ | $4h(d+h)$ | $3h(d+h)$ |
| 出处 | 1980s | Hochreiter & Schmidhuber 1997 | Cho et al. 2014 |

Greff et al. 2015 对比主流变体:表现差不多;Jozefowicz et al. 2015 搜了一万种架构,特定任务仍有更优者。

## 环境与运行

- Python 3.10+ / NumPy
- `python lstm.py`:3 组自测(手算对照 / BPTT 数值梯度 / 长程梯度流)

## 关键代码

```python
z = self.W @ np.concatenate([h_prev, x]) + self.b   # (4h,) 一次算四门
i, f, o, g = sigmoid(z[0:h]), sigmoid(z[h:2*h]), sigmoid(z[2*h:3*h]), tanh(z[3*h:4*h])
c = f * c_prev + i * g          # 细胞:加性更新,梯度只乘 f
h_out = o * np.tanh(c)
...
dc_total = dc + dh * o * (1.0 - tanh_c ** 2)   # 两条路径汇入细胞梯度
dz_o = do * o * (1.0 - o)                       # 输出门也要乘 sigmoid 导数!
```

## 性能边界

- 参数量 $4h(h+d)$ ≈ 同宽度 RNN 的 4 倍;前向每步一次 $(4h)\times(h+d)$ GEMM;
- **无法并行化时间维**:必须逐步递推,长序列训练慢 —— Transformer 用注意力换取全序列并行,是 LSTM 在 NLP 主场失守的工程原因(而非建模能力);
- 仍有上限:$\prod f_t$ 若长期 <1 依旧衰减,LSTM 缓解而非根除长程问题。

## 注意事项与常见坑

1. **输出门的 dz 忘乘 sigmoid 导数**:本 demo 开发时实测踩坑 —— `dz_o = do` 会让 o 门块权重梯度错 ~5 倍,且通过 $dh_{prev}$ 污染所有上游梯度;数值梯度检查是唯一可靠的兜底。
2. **门分块顺序**:PyTorch `nn.LSTM` 权重按 $[i, f, g, o]$ 排布(注意 g 在第三块!),自定义实现常用 $[i,f,o,g]$;两边对接时极易错位。
3. **遗忘门偏置初始化为正**(常用 +1 ~ +4):零初始化时初始 $f≈0.5$,开局就把记忆腰斩,长程任务收敛显著变慢。
4. **梯度裁剪仍是标配**:细胞路径稳了,但 $i,g$ 等门回路仍走 $W$ 连乘,BPTT 爆炸并未绝迹,LSTM 训练惯例 `clip_grad_norm_`。
5. **深度堆叠/双向**:colah 原文未覆盖;Stacked LSTM 下层 $h_t$ 喂上层同时间步,BiLSTM 两方向拼接 —— 工程标配但注意双向不能用于因果预测。
6. **peephole 变体**:让门直接看 $C$(Gers & Schmidhuber 2000),论文间配置不一,精度收益有限。

## 参考资料(实际阅读过的权威来源)

- [Hochreiter & Schmidhuber 1997, Long Short-Term Memory, Neural Computation 9(8)](https://direct.mit.edu/neco/article/9/8/1735/5467) — 原始论文:常数误差传送带(CEC)与门控设计
- [colah's blog: Understanding LSTM Networks (2015)](https://colah.github.io/posts/2015-08-Understanding-LSTMs/) — **本轮 WebFetch 全文阅读**:细胞状态"传送带"、三门的 0/1 语义、语言模型示例(France→French)、peephole/GRU 变体、Greff 2015 与 Jozefowicz 2015 结论
- [Steven Gong's Notes: LSTM (CS231n 2024 Lec 7)](https://stevengong.co/notes/Long-Short-Term-Memory) — $4h\times2h$ 合并权重实现形式 + $\prod f_t$ 梯度流分析 + ResNet/Highway 类比(本 demo 公式排布依据)
- [ScienceDirect: NNAN LSTM disaggregation (2025)](https://www.sciencedirect.com/science/article/pii/S2666827025000507) — LSTM 五式完整编号引用(式 4-9),工程应用中的参数量/可解释性论述
- [ScienceDirect: iCEEMDAN + Bayesian optimized LSTM (2021)](https://www.sciencedirect.com/science/article/pii/S2352484721009215) — LSTM 结构逐门公式 + 时间序列场景论证(交叉验证)
