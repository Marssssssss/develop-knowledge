# 学习率调度与梯度裁剪

## 简介

学习率不是常数:训练初期参数随机、梯度量级失衡,大 lr 会直接把权重推飞;训练后期又需要小 lr 才能落进窄极小值。因此现代训练配方里 lr 是一条**曲线**,再配一道**梯度裁剪**兜底。本 demo 把这条链拆成四个可验证的部分。

关键概念:

- **线性 warmup**:前若干步把 lr 从 0 线性抬到峰值。Transformer 原文用 `warmup_steps = 4000`。
- **inverse-sqrt 衰减**:`min(step^-0.5, step·warmup^-1.5)` 的两个分支恰在 `step = warmup` 相交,之后按 `step^-0.5` 下降。
- **余弦退火 / warm restart**(SGDR):`η(t) = η_min + ½(η_max−η_min)(1+cos(π·T_cur/T_i))`,每个周期结束把 lr 拉回 `η_max`,周期长度按 `T_mult` 倍增。
- **解耦权重衰减(AdamW)**:L2 正则与权重衰减在 SGD 下等价,在 Adam 下**不等价**。
- **按范数裁剪**:`g ← g·τ/‖g‖` 保持方向、只压长度;按元素裁剪会改变方向。

## 原理详解

### 1. Transformer 的 inverse-sqrt warmup

原文公式 (3)(`d_model=512`、`warmup_steps=4000`、Adam `β1=0.9, β2=0.98, ε=1e-9`):

```
lrate = d_model^(-0.5) · min( step_num^(-0.5),  step_num · warmup_steps^(-1.5) )
```

两个分支都是 `step` 的单调函数:前者递减、后者递增,所以 `min` 天然形成一个"先升后降"的拐点。解出交点 `step = warmup`,峰值

```
lrate_peak = d_model^(-0.5) · warmup^(-0.5)
```

实测(d_model=512, warmup=4000):

| step | lr | 说明 |
| --- | --- | --- |
| 1 | 1.746928e-07 | 起点(step=0 被保护为 1) |
| 100 | 1.746928e-05 | 线性上升段(∝ step) |
| 1000 | 1.746928e-04 | 同上 |
| **4000** | **6.987712e-04** | 两分支交点 = 峰值(解析式吻合到 1e-12) |
| 4001 | 6.986839e-04 | 刚过拐点 |
| 10000 | 4.419417e-04 | 衰减段 |
| 100000 | 1.397542e-04 | `lr(100000)/lr(4000) = 0.2000` = `(25)^-0.5` |

为什么要 warmup:Post-LN Transformer 在初始化时**靠近输出层的参数期望梯度很大**,一开始就用峰值 lr 会不稳定;Pre-LN 的梯度则表现良好,可以去掉 warmup(见 `../LayerNorm/` 的梯度剖面实验)。

### 2. SGDR:余弦退火 + warm restart

```
η_t = η_min + ½(η_max − η_min)(1 + cos(π · T_cur / T_i))
```

`T_cur` 是当前周期内已走的步数,`T_i` 是本周期长度,每个周期结束把 `T_cur` 归零 —— 这就是 "warm restart"。实测(`η_max=0.1`, `T_0=10`, `T_mult=2`):

| 周期 | 长度 | 重启点 η | 中点 η | 末步 η |
| --- | --- | --- | --- | --- |
| 0 | 10 | **0.100000** | 0.050000 | 0.002447 |
| 1 | 20 | **0.100000** | 0.050000 | 0.000616 |
| 2 | 40 | **0.100000** | 0.050000 | 0.000154 |
| 3 | 80 | **0.100000** | 0.050000 | 0.000038 |

重启点恰为 `0/10/30/70`,周期长度按几何级数 10→20→40→80 增长,4 个周期共 150 步。注意**不能用 `step % cycle_len` 实现** —— 周期长度本身在变,必须先把周期起点累加出来。

论文在 CIFAR-10/100 上报告 3.14% / 16.21% 的错误率。

### 3. LLM 配方:线性 warmup + 余弦衰减

```
step < warmup :  lr = peak · (step+1)/warmup
否则          :  lr = peak · [min_ratio + (1−min_ratio)·½(1+cos(π·prog))]
```

实测(`peak=3e-4`, `warmup=2000`, `total=10000`, `min_ratio=0.1`):step 0 → 1.5e-07、step 1999 → 3.000000e-04(峰值)、step 5000 → 2.166623e-04、step 10000 → 3.000000e-05(即 `peak·min_ratio`)。

### 4. AdamW:权重衰减为什么必须解耦

对 SGD,`g + λθ` 与"先按梯度走、再乘 `(1−ηλ)`"是同一个更新(相差 `O(η²)`);对 Adam 则**不是**,因为 `g + λθ` 会被 `1/(√v̂+ε)` 逐参数缩放 —— 梯度历史不同的参数,实际衰减强度就不同,`λ` 的含义随之模糊。

实测(两参数初值同为 1.0,梯度 0.01 与 1.0,u = 1e-3, wd = 0.1, 500 步):

| 实现 | θ_small/θ0 | θ_large/θ0 | 两者之比 |
| --- | --- | --- | --- |
| Adam + L2(耦合) | 0.554550 | 0.505010 | **1.098098** |
| AdamW(解耦) | 0.463547 | 0.463546 | **1.000001** |

解耦版的两个参数**严格等强度**(差 4.8e-07,纯浮点);耦合版差 **9.8%** —— 同样的 `weight_decay`,作用到不同参数上强度不同。PyTorch 的 `AdamW` 默认 `weight_decay=0.01`,LLM 配方常用 0.1。

### 5. 梯度裁剪:按范数 vs 按元素

```
按范数:  if ‖g‖ > τ:  g ← g · τ/‖g‖        # 全局等比缩放
按元素:  g_j ← clip(g_j, −τ, +τ)           # 逐元素硬截断
```

实测(`g = [10, 0.1, −0.2, 0.05]`,`τ = 1.0`):

| 方式 | 结果 | cos(原, 裁) | 夹角变化 |
| --- | --- | --- | --- |
| 按范数 | `[0.999738, 0.009997, −0.019995, 0.004999]`,‖g‖ = 1.000000 | **1.0000000000** | 0.00° |
| 按元素 | `[1.0, 0.1, −0.2, 0.05]`,‖g‖ = 1.025914 | 0.9796006331 | **11.59°** |

按范数裁剪**严格保持方向**(cos 精确为 1),这是它成为默认选择的根本原因;按元素裁剪会把梯度方向拧偏 11.59°,而方向才是 SGD 真正传递的信息。`clip_grad_norm_` 还会返回**裁剪前**的范数,便于打日志观察。

### 6. warmup 的必要性(可复现的发散对照)

构造一个"输出层权重放大 N 倍"的两层网络(复现论文说的"靠近输出层的期望梯度很大"),同峰值 lr、同种子、300 步,只改有没有 warmup:

| 配置 | ‖g_0‖ | 无 warmup | warmup 150 步 |
| --- | --- | --- | --- |
| 放大 10× / peak=1e-3 | 3.68e+02 | 2.6854 | **2.4571** |
| 放大 30× / peak=2e-3 | 3.30e+03 | **发散(nan)** | **2.8187** |

失衡越严重,不用 warmup 越会直接发散;加上 warmup 就能收敛。这两行就是"warmup 不是玄学"的最小证据。

## 对比 / 选型

| 调度 | 形状 | 超参 | 适用 |
| --- | --- | --- | --- |
| 常数 | 平 | 1 | 小模型 / 调试 |
| Step decay | 阶梯 | 里程碑 + γ | 传统 CNN |
| inverse-sqrt + warmup | 先线性升后幂降 | warmup | 原始 Transformer |
| 余弦退火 | 平滑到 η_min | T_max | 现代默认 |
| SGDR(warm restart) | 余弦 + 周期重启 | T_0, T_mult | 需要"多次跳出"时 |
| 线性 warmup + 余弦 | 升-降 | warmup, peak, min_ratio | LLM 预训练主流 |

## 环境准备

- 操作系统:任意(实测 Windows 11 + Git Bash)
- 语言版本:Python 3.13 + NumPy 2.5;Go 1.21+(本机未装工具链,走人工代码审查)
- 依赖:仅 NumPy

## 运行方式

```bash
cd python && python schedule.py && python schedule_check.py   # 报告 + 30 项断言
cd go && go run .                                             # 11 项断言
```

## 关键代码片段

```python
def transformer_lr(step, d_model=512, warmup=4000):
    s = max(1, step)                       # 原文实现里对 step<=0 的保护
    return d_model ** -0.5 * min(s ** -0.5, s * warmup ** -1.5)   # 两分支在 warmup 处相交

def sgdr_lr(step, t0=10, mult=2, eta_max=0.1):
    start, T = 0, t0                       # 不能用 step % T:周期长度本身在变
    while step >= start + T:
        start += T; T *= mult
    return 0.5 * eta_max * (1 + math.cos(math.pi * (step - start) / T))

def clip_by_norm(g, max_norm):
    n = np.linalg.norm(g)
    return (g * (max_norm / n) if n > max_norm else g), n   # 返回裁剪前范数用于日志
```

## 性能与边界

- 三条调度都是 `O(1)` 求值,不构成开销;SGDR 的周期查找是 `O(log_周期数)`,可实现为摊销 `O(1)`。
- 裁剪本身是 `O(参数数)` 的归约。**在数据并行下裁剪必须作用在"全局聚合后的梯度"上**,逐 worker 各自裁剪会把全局范数低估最多 `√W` 倍(W = worker 数)。
- 裁剪只抑制**单步**的大梯度,不解决系统性发散(如 lr 过大、数据损坏)。
- 本 demo 的收敛/发散对照只在 16 宽 2 层网络上验证过,不能外推到真实模型的最优 lr。

## 注意事项与常见坑

1. **`step % cycle_len` 实现 SGDR 是错的**:周期长度倍增时取模结果与真实周期起点不符。
2. **warmup 的峰值要与调度终点对齐**:`warmup_cosine_lr` 里 `prog` 必须相对 `total − warmup` 归一,否则衰减会比预期快。
3. **AdamW ≠ Adam + L2**:实测同 `weight_decay` 下,两参数的衰减强度差 9.8%(AdamW 为 0)。迁移旧代码时如果沿用 `Adam(weight_decay=...)`,要重新标定 `λ`。
4. **裁剪要放在 `backward()` 之后、`step()` 之前**:放错位置等于没裁(梯度还没算出来)或裁了又被覆盖。
5. **优先按范数裁剪**:按元素裁剪会把方向拧偏(实测 11.59°);只有在需要逐元素硬上限时才用 `clip_grad_value_`。
6. **不同框架的默认值不一致**:PyTorch `clip_grad_norm_` 的 `max_norm` 默认是 1.0,`AdamW` 的 `weight_decay` 默认是 0.01;LLM 配方常把 `weight_decay` 提到 0.1、`β2` 从 0.999 降到 0.95。换配方时这三项要一起看。

## 参考资料(实际阅读过的权威来源)

- [Attention Is All You Need (Vaswani et al. 2017, arXiv:1706.03762)](https://arxiv.org/abs/1706.03762) — §5.3 原文公式 (3) 与 `warmup_steps = 4000`;官方措辞:"increasing the learning rate linearly for the first warmup_steps training steps, and decreasing it thereafter proportionally to the inverse square root of the step number";Adam 超参 `β1=0.9, β2=0.98, ε=10⁻⁹`。
- [SGDR: Stochastic Gradient Descent with Warm Restarts (Loshchilov & Hutter, arXiv:1608.03983)](https://arxiv.org/abs/1608.03983) — 余弦退火 + warm restart 的出处;CIFAR-10 3.14% / CIFAR-100 16.21%。
- [Decoupled Weight Decay Regularization (Loshchilov & Hutter, arXiv:1711.05101)](https://arxiv.org/abs/1711.05101) — "L2 regularization and weight decay regularization are equivalent for standard SGD ... but not the case for adaptive gradient algorithms, such as Adam";解耦后 `weight_decay` 与 lr 的最优取值彼此独立,并显著改善 Adam 的泛化。
- [On the difficulty of training Recurrent Neural Networks (Pascanu et al., arXiv:1211.5063)](https://arxiv.org/abs/1211.5063) — 提出梯度范数裁剪应对梯度爆炸;论文建议阈值取稳定训练时平均梯度范数的 0.5~10 倍。
- [PyTorch 文档:性能/优化器默认值(经检索核对)](https://pytorch.org/docs/stable/generated/torch.nn.utils.clip_grad_norm_.html) — `clip_grad_norm_(parameters, max_norm, norm_type=2.0)` 默认 `max_norm=1.0`,返回裁剪前总范数;`AdamW` 默认 `weight_decay=0.01`。GPT-3、Llama 2 等公开配方均用全局 L2 范数阈值 1.0。
