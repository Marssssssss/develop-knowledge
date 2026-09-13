# Batch Normalization · 批归一化(Ioffe & Szegedy 2015)

## 简介

BatchNorm 对每个特征维用**当前 mini-batch 的均值/方差**做标准化,再用可学习的 $\gamma$(缩放)、$\beta$(平移)恢复表达能力;推理时改用训练期 EMA 累积的总体统计量。它把"减少内部协变量偏移(internal covariate shift)"作为动机提出,实际收益是**平滑优化面、允许大学习率、对初始化脱敏 + 天然轻度正则**。本 demo 从零实现 Algorithm 1(训练前向)、Algorithm 2(推理)、完整解析反向,并做数值梯度验证。

- **前向(Algorithm 1,按特征维独立)**:

$$\mu_B = \frac{1}{m}\sum x_i,\quad \sigma_B^2 = \frac{1}{m}\sum (x_i-\mu_B)^2,\quad \hat{x}_i = \frac{x_i-\mu_B}{\sqrt{\sigma_B^2+\epsilon}},\quad y_i = \gamma \hat{x}_i + \beta$$

- **推理**:用 running mean/var(EMA:$\text{new} = \lambda\cdot\text{old} + (1-\lambda)\cdot\text{batch}$,PyTorch momentum=0.9 即 $\lambda$)。

## 原理详解

### 1. 反向传播(Algorithm 2 展开,本 demo 逐项实现)

$$\frac{\partial L}{\partial \gamma} = \sum_i \frac{\partial L}{\partial y_i} \hat{x}_i,\qquad \frac{\partial L}{\partial \beta} = \sum_i \frac{\partial L}{\partial y_i}$$

$$\frac{\partial L}{\partial x_i} = \frac{\partial L}{\partial \hat{x}_i}\frac{1}{\sqrt{\sigma_B^2+\epsilon}} + \frac{\partial L}{\partial \sigma_B^2}\frac{2(x_i-\mu_B)}{m} + \frac{\partial L}{\partial \mu_B}\frac{1}{m}$$

其中 $\partial L/\partial \hat{x}_i = \partial L/\partial y_i \cdot \gamma$,$\partial L/\partial \sigma_B^2$ 与 $\partial L/\partial \mu_B$ 按链式展开(见代码注释)。注意 $x$ 出现在 $\hat{x}$、$\sigma^2$、$\mu$ 三处,三条路径都要算。

### 2. 训练/推理不一致的来源

训练用 batch 统计(带抽样噪声),推理用 EMA 统计(确定性)。两个后果:
- **batch 统计噪声 = 轻度正则**(同一输入在不同 batch 中输出不同,demo 实测);
- **小 batch 时噪声过大 → 性能下降**:batch=512 时 eval 均值偏差 0.05,batch=64 会到 ~0.3。

### 3. 实测结果(本 demo 运行输出)

- 训练态 γ=1,β=0:输出 mean=6e-17、std=1.0000;
- γ=2,β=1:输出 mean=1.0000、std=2.0000(仿射参数完全可控);
- 解析梯度 vs 中心差分:dx 误差 **2.1e-9**,dγ 2e-10,dβ 2.8e-10;
- running stats(200 批 × m=512):eval 输出均值 |max|=0.054(含 EMA 抽样噪声)。

## 对比

| 方法 | 归一化维度 | 依赖 batch | 典型场景 |
| --- | --- | --- | --- |
| BatchNorm | batch 维(每特征) | 是 | CNN |
| LayerNorm | 特征维(每样本) | 否 | Transformer/RNN |
| InstanceNorm | 每样本每通道 | 否 | 风格迁移 |
| GroupNorm | 通道分组 | 否 | 小 batch 检测分割 |

## 环境与运行

- Python 3.10+ / NumPy
- `python batchnorm.py`:5 组断言自测(标准化 / 数值梯度 / 仿射参数 / running stats / batch 噪声)

## 关键代码

```python
def forward(self, x, training):
    if training:
        mu = x.mean(axis=0)
        var = ((x - mu) ** 2).mean(axis=0)      # 有偏估计(论文 Algorithm 1)
        self.running_mean = 0.9 * self.running_mean + 0.1 * mu
        self.running_var  = 0.9 * self.running_var  + 0.1 * var
    else:
        mu, var = self.running_mean, self.running_var
    xhat = (x - mu) / np.sqrt(var + self.eps)
    return self.gamma * xhat + self.beta
```

## 性能边界

- 计算:2 次归约(mean/var)+ 逐元素变换,约为一次 GEMM 的 5-10%,基本免费;
- 显存:每层多存 γ、β、running_mean、running_var(各 num_features);
- 大 batch(≥32)时统计稳定;batch=2 的极端情形噪声大到不可用(训练 GAN 的经典坑)。

## 注意事项与常见坑

1. **方差用有偏估计(÷m 而非 m-1)**:论文 Algorithm 1 明确用 $m^{-1}\sum$;PyTorch 训练态同样用有偏,running_var 无偏修正只在特定版本/框架有差异。
2. **训练/推理行为不同**:忘记 `model.eval()` 会让推理结果依赖同 batch 的其他样本(在线服务单条请求 batch=1 时直接崩溃)。
3. **BN 前一层不要再加 bias**:减均值后 bias 被完全抵消,省参数(PyTorch `bias=False` 惯例)。
4. **放在激活前**(conv → BN → ReLU)是论文原设定;放激活后也有人用但非默认。
5. **数值梯度验证时统计量必须随扰动重算**:mu/var 是 x 的函数,固定它们验证会漏掉 dvar/dmu 两条路径(demo 开发时踩过:误差停在 8.7%)。
6. **EMA momentum 语义**:PyTorch `momentum=0.9` 是"新值占比 0.1"的 $\lambda$,与优化器 momentum 方向相反,迁移实现时最容易搞反。
7. **与 dropout 叠加**:dropout 随机改变方差,BN 统计被污染,顺序与组合需要实验验证。

## 参考资料(实际阅读过的权威来源)

- [Ioffe & Szegedy 2015, Batch Normalization (arXiv:1502.03167)](https://arxiv.org/abs/1502.03167) — 原始论文,Algorithm 1/2 与全部公式(经多源全文核对)
- [SAS Help Center: Batch Normalization](https://documentation.sas.com/doc/en/pgmsascdc/v_075/casdlpg/p1w0a96fvof9l4n1ogup6orq8be1.htm) — 工业实现细节:逐 feature map 统计、noBias=True 配置、scoring 期统计量累计策略
- [AI Computer Institute: Batch Normalization](https://aicomputerinstitute.com/grades/11/chapters/grade11-batch-normalization) — 反向六公式逐项 + PyTorch BatchNorm1d 参考实现(本 demo 对照)
- [sunny525s/Regularization (GitHub)](https://github.com/sunny525s/Regularization) — running stats EMA 公式与动量 λ 语义
