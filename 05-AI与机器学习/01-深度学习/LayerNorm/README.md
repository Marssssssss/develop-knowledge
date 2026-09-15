# LayerNorm 与 RMSNorm(逐样本归一化)

## 简介

LayerNorm(LN)把归一化的统计量从「一个 mini-batch 内、同一神经元的跨样本集合」改成「**单个样本内、一层所有神经元**」,因此前向/推理完全一致,也不受 batch 组成和 batch size 影响。RMSNorm 进一步发现 LN 的**减均值(re-centering)那一步可以去掉**,只按均方根做 re-scaling,在几乎不损失效果的前提下把归一化的开销降下来。

关键概念:

- **归一化维度**:LN/RMSNorm 在「特征维 H」上算统计量(per-sample),BatchNorm 在「batch 维 N」上算(per-channel)。
- **re-centering / re-scaling 不变性**:LN 对输入的平移与缩放都不敏感;RMSNorm 只对缩放不敏感。
- **Pre-LN / Post-LN**:LN 放在残差块**内部**(pre)还是**残差相加之后**(post),直接决定初始化时的梯度剖面,进而决定要不要 lr warmup。
- **ε 的角色**:它既是数值保护,也是「缩放不变性」在浮点下不再逐位成立的原因。

历史:Ba/Kiros/Hinton 2016 提出 LN,动机是 BatchNorm 依赖 batch size、且难以用于 RNN;Zhang & Sennrich 2019 提出 RMSNorm,动机是 LN 的 re-centering 可能是不必要的开销。

## 原理详解

### 1. LayerNorm 前向

```
μ     = (1/H) Σ_j x_j
σ²    = (1/H) Σ_j (x_j − μ)²
x̂     = (x − μ) / sqrt(σ² + ε)
y     = γ ⊙ x̂ + β                 # γ, β 是每神经元一对,形状 H
```

统计量只在最后一维(H)上求和,`μ`、`σ` 的形状是 `(N, 1)` —— **每个样本一套**,样本之间没有信息交换。直接后果:同一个样本单独跑、还是混在任意 batch 里跑,输出**逐位相同**(本目录实测 `max|Δy| = 0.0`)。

### 2. RMSNorm 前向

```
rms = sqrt( (1/H) Σ_j x_j² + ε )
y   = γ ⊙ x / rms                 # 不减均值,原论文也没有 β
```

去掉 `μ` 后仍保留 re-scaling 不变性(x → c·x 时 y 不变),但丢掉 re-centering:x → x + c·1 时输出会变(实测 `max|Δy| = 3.404`,而 LN 是 `7.2e-16`)。

当输入本身零均值时,两者**数值等价**(实测差 `4.4e-16`,纯浮点噪声)。

### 3. 两种归一化的反向

LN 的反向有三条路(第三、四条是归一化引入的耦合,不能省):

```
dx̂ = dy ⊙ γ
dvar = Σ_j (dx̂_j (x_j−μ)) · (−1/2)(σ²+ε)^(−3/2)
dμ   = Σ_j (−dx̂_j · rstd) + dvar · (−2/H) Σ_j (x_j−μ)
dx_j = dx̂_j · rstd + dvar · 2(x_j−μ)/H + dμ/H
dγ   = Σ_N dy ⊙ x̂ ,  dβ = Σ_N dy
```

RMSNorm 少一路(没有 `μ` 的耦合),但要小心 `γ` 的位置:

```
∂y_i/∂x_j = γ_i δ_ij / r − γ_i x_i x_j /(H r³)
dx_j      = γ_j dy_j / r − x_j · Σ_i(γ_i dy_i x_i) /(H r³)
                                ^^^^^^^^^^^^^^^^^^^ γ 在**分子求和里**,不乘在 x_j 上
```

写成 `γ_j x_j · Σ(dy_i x_i)` 是本 demo 开发期真实踩到的坑:相对误差 `8.7e-2`(不是 typo 级,会真的训不动)。

### 4. Pre-LN vs Post-LN 的梯度剖面

```
Pre-LN :  h_{l+1} = h_l + F_l( LN(h_l) )         # 残差流是干净的恒等通道
Post-LN:  h_{l+1} = LN( h_l + F_l(h_l) )         # 每块都把残差流重新归一化
```

用 depth=32、H=64 的残差 MLP,在初始化点算 `‖∂L/∂W1‖`(loss = ½‖h_L‖²,7 个 seed 取中位数):

| depth | Pre-LN 首块 / 末块 / 比值 | Post-LN 首块 / 末块 / 比值 |
| --- | --- | --- |
| 4 | 1.782e+02 / 1.121e+02 / **1.52** | 3.484e-04 / 4.536e-04 / **0.71** |
| 8 | 2.808e+02 / 1.391e+02 / **1.77** | 2.808e-04 / 4.728e-04 / **0.62** |
| 16 | 4.491e+02 / 2.006e+02 / **2.24** | 2.770e-04 / 5.722e-04 / **0.46** |
| 32 | 9.864e+02 / 2.758e+02 / **3.58** | 3.296e-04 / 5.726e-04 / **0.67** |

两个稳健结论(7 个 seed 全部同号):

1. **绝对量级**:Post-LN 的梯度比 Pre-LN 小 **3.9e+06 倍**。原因是 Post-LN 每块尾的 LN 把残差流重新归一化,信号不会沿恒等通道累积;Pre-LN 的残差流范数随深度增长,梯度同量级放大。
2. **剖面形状**:Pre-LN 的首/末块比恒 **>1**(2.61~4.41),Post-LN 恒 **<1**(0.28~0.89)。比值 <1 即「靠近输出层的块梯度更大」,这正是 Post-LN 必须配 lr warmup 的原因 —— 输出侧的大梯度 × 大学习率会直接把训练推发散。

## 对比 / 选型

| 维度 | BatchNorm | LayerNorm | RMSNorm |
| --- | --- | --- | --- |
| 统计量所在维 | batch(跨样本) | 特征(单样本) | 特征(单样本) |
| 训练/推理一致 | 否(用 running stats) | 是 | 是 |
| 依赖 batch size | 是(小 batch 退化) | 否 | 否 |
| 平移不变 | 否 | 是 | **否** |
| 缩放不变 | 是 | 是 | 是 |
| 可用在 RNN | 困难 | 是(每步归一化) | 是 |
| 每元素额外开销 | 均值+方差+sqrt+除 | 均值+方差+sqrt+除 | 均方+sqrt+除 |
| 适用场景 | CNN / 大 batch | Transformer、RNN | Transformer(现代 LLM 主流) |

本机实测前向耗时(2048×4096 float64,NumPy):LayerNorm **116~192 ms** vs RMSNorm **109~125 ms**。这个比值随机器负载波动(空载时 1.9×、并发跑多个自检时掉到 1.07×),所以自检里**不作断言**;硬判据用确定性的「归约次数」:LN 需要 `μ` 与 `σ²` 两次归约,RMSNorm 只算均方、一次,理论加速比 2×。论文在不同模型上报告的是 7%~64% 的运行时下降 —— 差距来自这里只测了单算子的内存带宽,而论文测的是整模型。

## 环境准备

- 操作系统:任意(实测 Windows 11 + Git Bash)
- 语言版本:Python 3.13 + NumPy 2.5;Go 1.21+(本机未装工具链,走人工代码审查)
- 依赖:仅 NumPy

## 运行方式

```bash
# Python:实验报告 + 22 项断言自检
cd python && python layernorm.py && python layernorm_check.py

# Go:同题的最小实现 + 中心差分交叉验证(6 项断言)
cd go && go run .
```

## 关键代码片段

```python
def layernorm_forward(x, gamma, beta, eps=1e-5):
    mu = x.mean(axis=-1, keepdims=True)                    # 只在 H 维求均值 -> per-sample
    var = ((x - mu) ** 2).mean(axis=-1, keepdims=True)
    rstd = 1.0 / np.sqrt(var + eps)
    xhat = (x - mu) * rstd                                 # re-centering 就在这里
    return gamma * xhat + beta, (x, mu, rstd, xhat, gamma)

def rmsnorm_forward(x, gamma, eps=1e-5):
    rms = np.sqrt((x ** 2).mean(axis=-1, keepdims=True) + eps)
    return gamma * x / rms, (x, rms, gamma)                # 不减均值,也无 beta
```

## 性能与边界

- 时间复杂度:`O(N·H)`,与 MLP 的 `O(N·H²)` 相比可忽略,**但**在小 H 或 memory-bound 场景下归一化会占相当比例的带宽。
- 反向比前向贵:LN 需要额外保存 `μ`、`rstd`、`x̂`(推理可只存 `rstd` 与 `μ⁻` 合并量)。
- `ε` 不能设成 0:方差为 0 的常量特征会除零。同时 `ε > 0` 会让「缩放不变」从逐位相等退化成 `O(ε/var)`,本目录实测 `x → 1000x` 时残差 `2.13e-5`。
- Post-LN 深网络的梯度量级随 H、depth、初始化尺度强烈变化,本目录的结论是**方向性**的,不能当作收敛阈值的定量预测。

## 注意事项与常见坑

1. **归一化维度写错**:在 `axis=0`(batch 维)上算统计量就变成了 BatchNorm,但不会报错 —— 只会让 batch=1 时输出全零。
2. **RMSNorm 反向的 γ 位置**(上文第 3 节):错法相对误差 `8.7e-2`,梯度方向被系统性拉偏。
3. **RMSNorm 不是平移不变**:输入存在大偏置时,`rms` 被偏置抬高,归一化后的有效幅度被压低。所以用 RMSNorm 时前置的 bias 要谨慎(原论文也因此去掉了 `β`)。
4. **把 LN 的 `β` 当成「必须项」**:RMSNorm 原论文不含 `β`,后面的线性层自带 bias 时代入 `β` 是冗余参数。
5. **eps 写在 sqrt 里还是外面**:`sqrt(var+ε)` 与 `sqrt(var)+ε` 在 var→0 时数值行为完全不同,前者才是标准写法。
6. **训练/推理不一致只发生在 BN**:迁移到 LN/RMSNorm 后可以删掉 running mean/var 及相关 buffer,但序列化旧 checkpoint 时要留意键名变化。

## 参考资料(实际阅读过的权威来源)

- [Layer Normalization (Ba, Kiros, Hinton 2016, arXiv:1607.06450)](https://arxiv.org/abs/1607.06450) — LN 的定义:统计量取自「单个训练样本上某层所有神经元的求和输入」,每神经元独立的 adaptive bias/gain 加在归一化之后、非线性之前;训练与测试时刻计算完全相同;可直接用于 RNN(每步单独算统计量)。
- [Root Mean Square Layer Normalization (Zhang & Sennrich 2019, arXiv:1910.07467)](https://arxiv.org/abs/1910.07467) — 提出「re-centering 不变性是可省的」,RMSNorm 保留 re-scaling 不变性与隐式学习率自适应;报告运行时下降 7%~64%(不同模型)。
- [On Layer Normalization in the Transformer Architecture (Xiong et al. 2020, arXiv:2002.04745)](https://arxiv.org/abs/2002.04745) — 用平均场理论证明 Post-LN 在初始化时「靠近输出层的参数期望梯度很大」,大学习率会不稳定,这就是 warmup 的必要性来源;Pre-LN 的梯度则表现良好,可去掉 warmup。
