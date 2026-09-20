# 457 · CTC 损失与解码（空白 token / 折叠规则 / 前向-后向）

## 简介

语音识别里音频是**连续的帧序列**，标注是**短得多的标签序列**，两者之间没有已知的对齐关系。CTC（Connectionist Temporal Classification，Graves et al. ICML 2006）的做法是：让网络在**每一帧**都输出一个「标签 + 空白」上的分布，再定义一个**多对一**映射 `B` 把任意帧级路径折叠成标签串，最后把**所有能折叠出目标串的路径概率加起来**当作 `p(l|x)`。

这样做的好处（论文 §1、§6）：

- 不需要预先切分（pre-segmented）训练数据，也不需要在推理后做后处理；
- 目标函数只依赖**标签序列本身**，与每个标签**持续多久**无关，所以不需要 HMM 那套加权误差之类的启发式；
- 与之对照，framewise 分类网络必须对齐人工切分，边界划错就吃误差，即使音素预测正确（论文 Fig.1 里 `dh` 的例子）。

本 demo 完整实现论文 §3.1 的折叠规则、§4.1 的前向-后向与缩放、式(14)(15)(16) 的概率与梯度，以及 §3.2 的两种解码，并用**穷举全部路径**与**有限差分**做对照验证。

## 原理详解

### 1. 输出层：|L| + 1 个单元

softmax 输出层比标签集 `L` **多一个单元**，多出来的那个就是 **blank**（论文 §3.1：`"one more unit than there are labels in L"`）。论文 TIMIT 实验里 61 个音素 + 1 个 blank = **62** 个 softmax 单元，总权重 114,662。

对长度 `T` 的输入，网络输出定义了 `L'^T`（`L' = L ∪ {blank}`）上的分布：

```
p(π|x) = Π_{t=1..T} y^t_{π_t}          （式 2）
```

隐含假设：**给定网络内部状态后各帧输出条件独立** —— 论文明确指出这靠「输出层不能反馈到自己或网络」来保证。

### 2. 折叠映射 B：先合并相邻重复，再去 blank

```
B(a-ab-) = B(-aa--abb) = aab           （论文 §3.1 原例）
```

实现方式有讲究，顺序反了结果就错：

```python
def collapse(path):
    merged = []
    for c in path:
        if not merged or merged[-1] != c:   # ① 先合并相邻重复
            merged.append(c)
    return tuple(c for c in merged if c != BLANK)   # ② 再去掉 blank
```

关键后果：**blank 是分隔符**。`B("a-a") = "aa"`（两个 a），而 `B("aa") = "a"`（一个 a）。这也解释了为什么 CTC 要输出「尖峰」——同一个音素连着出现两次时，中间必须插一个 blank。

### 3. 扩展序列 l'：长度 2|l| + 1

为了允许 blank 出现在任意位置，把目标 `l` 扩展成 `l'`：首尾各加一个 blank，每两个标签之间也插一个。所以 `|l'| = 2|l| + 1`。特殊情形：`l` 为空时 `|l'| = 1`，此时 `p(l|x) = α_T(1)`，**没有 `α_T(|l'|-1)` 这一项**（代码里必须单独处理，否则会拿到 `α_T(-1)` 这种越界下标）。

### 4. 前向变量 α（式 5-7）

`α_t(s)` = 到时刻 `t` 为止、折叠出 `l'` 前 `s` 个符号的所有路径概率之和。初始化：

```
α_1(1) = y^1_b      α_1(2) = y^1_{l1}      α_1(s) = 0, ∀s > 2
```

递推（`ᾱ_t(s) = α_{t-1}(s) + α_{t-1}(s-1)`）：

```
α_t(s) = ᾱ_t(s) · y^t_{l'_s}                       若 l'_s = b 或 l'_{s-2} = l'_s
α_t(s) = (ᾱ_t(s) + α_{t-1}(s-2)) · y^t_{l'_s}      否则
```

「否则」分支里的 `α_{t-1}(s-2)` 就是**跳过中间那个 blank、从相同标签跳到相同标签之外的另一个标签**的那一步；当 `l'_s` 是 blank 或 `l'_{s-2} = l'_s` 时这一步非法（会折叠掉一个标签），所以不加。

终值（式 8）：

```
p(l|x) = α_T(|l'|) + α_T(|l'|-1)
```

两项分别对应路径最后一帧是 **blank** 还是 **最后一个标签**，是互不相交的两类路径，不存在重复计数。

### 5. 零区：是剪枝规则，不是自动成立的恒等式

论文 §4.1 给出两条零区：

```
α_t(s) = 0, ∀ s < |l'| − 2(T − t) − 1
β_t(s) = 0, ∀ s > 2t
```

理由是从状态 `s` 出发，剩下的 `T − t` 步每步最多推进 2 格，走不到终点就该算 0。

**实现时要注意**：这两条**不会**被递推式自动满足。本 demo 实测（T=7、|l'|=5）在 α 零区里有非零值存在（`D2` 断言），因为递推只向前看 2 步，它并不知道「后面还够不够时间」。所以它们必须**显式置零**。置零与否对最终 `p(l)` 没有影响（零区里的状态永远流不到 `α_T(|l'|)` 与 `α_T(|l'|-1)`），但对**缩放恒等式**有影响，见下。

### 6. 缩放与 ln p = Σ ln C_t（式 8 之后的段落）

`C_t = Σ_s α_t(s)`，`α̂_t = α_t / C_t`，把 `α̂` 代回递推式右侧即可防止下溢。此时：

```
ln p(l|x) = Σ_{t=1..T} ln C_t
```

**这个恒等式以「零区已置零」为前提**。因为 `Σ_s α̂_T(s) = 1`，只有把到不了终点的状态清零后，这 1 才全部落在 `α̂_T(|l'|) + α̂_T(|l'|-1)` 上；不置零时 `Π C_t` 会大于 `p(l)`。本 demo 两条都断言了（`C1` / `C2`）。

### 7. 后向变量 β 与式 (14)

`β_t(s)` 与 α 对称，从 `t = T` 反向递推。`α_t(s)` 与 `β_t(s)` **各自都包含** `y^t_{l'_s}` 这一因子（时间 `t` 被算了两次），所以：

```
p(l|x) = Σ_s α_t(s) β_t(s) / y^t_{l'_s}        （式 14）
```

对**任意** `t` 都成立，本 demo 对每个 `t` 都算一遍并断言它们同值（`E1`，实测 spread < 1e-9）。

### 8. 梯度（式 15、16）

由于 `αβ` 对 `y^t_k` 是 2 次的：

```
∂p(l|x) / ∂y^t_k = (1 / (y^t_k)^2) · Σ_{s∈lab(l,k)} α_t(s) β_t(s)      （式 15）
```

其中 `lab(l,k) = {s : l'_s = k}`，**可以为空**（某个标签没在目标里出现时）。配合 softmax 的 `∂y_j/∂u_k = y_j(δ_jk − y_k)`，以及 `Σ_k y^t_k ∂p/∂y^t_k = p`（`p` 对单帧输出是一次齐次的），得到回传到未归一化输出 `u` 的误差信号：

```
∂O/∂u^t_k = y^t_k − (1 / (y^t_k Z_t)) · Σ_{s∈lab(l,k)} α̂_t(s) β̂_t(s)   （式 16）
Z_t = Σ_s α̂_t(s) β̂_t(s) / y^t_{l'_s}
```

本 demo 用**中心差分**逐项核对式(16)（`F1`，最大偏差 < 1e-6），并断言 `Σ_k ∂O/∂u^t_k = 0`（softmax 平移不变性，`F2`）。

### 9. 解码

| 方法 | 论文位置 | 做法 | 是否最优 |
| --- | --- | --- | --- |
| best path decoding | §3.2 式(4) | `π*` = 每帧最大激活的拼接，输出 `B(π*)` | **不保证**，论文原话 *"it is not guaranteed to find the most probable labelling"* |
| prefix search decoding | §3.2 | 改造前向-后向，逐前缀扩展 | 时间足够时最优，但待扩展前缀数随 `T` 指数增长 |

best path 之所以不最优：**折叠后相同的多条路径会各自贡献概率**，最大单条路径所在的 labelling 未必是总概率最大的 labelling。本 demo 扫描 400 组随机分布，统计出「best path ≠ 穷举 argmax」的用例（`G1`），并断言该用例下 best path 的概率确实更低（`G2`）。

论文 §5.2 在 TIMIT 上的结果（LER，越低越好）：

| 系统 | LER |
| --- | --- |
| Context-independent HMM | 38.85 % |
| Context-dependent HMM | 35.21 % |
| BLSTM/HMM | 33.84 ± 0.06 % |
| Weighted error BLSTM/HMM | 31.57 ± 0.06 % |
| CTC (best path) | 31.47 ± 0.21 % |
| CTC (prefix search) | 30.51 ± 0.19 % |

论文还提到一个工程细节：为了让 prefix search 可行，用 blank 概率阈值把输出切成若干段，阈值设为 **99.99 %**。

## 对比：CTC vs framewise vs HMM 混合

| 维度 | framewise | HMM / HMM-RNN 混合 | CTC |
| --- | --- | --- | --- |
| 训练数据 | 必须预切分 | HMM 自动切分 | 不需要对齐 |
| 目标函数 | 逐帧独立分类 | 生成式 + 判别式混合 | 直接最大化目标串概率 |
| 输出后处理 | 需要 | HMM 负责 | 不需要（解码即最终串） |
| 标签间依赖 | 无 | 显式马尔可夫假设 | 无显式建模（靠双尖峰隐式表达，见 §6） |
| 需要任务先验 | 低 | 高（状态拓扑、插入惩罚等） | 低 |

## 环境

- Python 3.13（仅用标准库 `math` / `itertools` / `random`）
- Go 1.20+（仅用标准库）

## 运行方式

```bash
cd 05-AI与机器学习/06-语音与多模态/01-CTC损失与解码

# Python：34 条断言实跑
python selfcheck_ctc.py

# Go：折叠规则 + 式(14) + 穷举对照 + best path 解码
go run ctc.go selfcheck_ctc.go
```

## 关键代码

`ctc.py`：

```python
def forward_log(log_y, ext, prune=False):
    ...
    for t in range(1, T):
        for s in range(S):
            terms = [prev[s]]
            if s - 1 >= 0:
                terms.append(prev[s - 1])
            # l'_s = b 或 l'_{s-2} = l'_s 时不引入 s-2 项
            if not (ext[s] == BLANK or (s >= 2 and ext[s - 2] == ext[s])):
                if s - 2 >= 0:
                    terms.append(prev[s - 2])
            cur[s] = logsumexp(terms) + log_y[t][ext[s]]
        if prune:                      # 论文 §4.1 的零区必须显式置零
            for s in range(S):
                if s + 1 < alpha_zero_bound(ext, T, t + 1):
                    cur[s] = NEG
```

`ctc.go` 的实现与之一一对应，用 `Yale.Probs[t]` 的**最后一个位置**表示 blank（`At(t, BLANK)` 做映射）。

## 性能边界与数值注意

- **必须走 log 空间**。路径数是 `|L'|^T`，TIMIT 那种 62 类、几百帧的序列直接用概率相乘必然下溢到 0。本 demo 的 `forward_log` 全用 log-sum-exp。
- **缩放版依赖零区置零**（见 §6）。不置零时 `Π C_t` 会偏大，本 demo 实测确实不相等。
- 朴素穷举只在 `|L'|^T` 很小时可用；本 demo 用它做 `T ≤ 6` 的对照，共 1559 个用例全部一致。
- 复杂度：前向 + 后向是 `O(T · |l'|)`，而 `|l'| = 2|l| + 1`，所以是 `O(T · |l|)`；prefix search 的最坏情况随 `T` 指数增长。
- 论文 §5.1 的特征口径：TIMIT 用 10 ms 帧、5 ms 重叠、**12 个 MFCC**（来自 26 个滤波器组通道），加 log-energy 与一阶差分，共 26 维/帧，逐维归一化到均值 0 方差 1。

## 注意事项与常见坑

1. **折叠顺序**：先合并相邻重复，再删 blank。反过来的话 `B("a-a")` 会得到 `"a"` 而不是 `"aa"`。
2. **`|l'| = 1` 的边界**：空标签串没有 `α_T(|l'|-1)` 这一项，直接索引会得到 `α_T(-1)`（Python 里是最后一个元素，静默算错）。
3. **零区不是自动成立的**：递推式向前只看 2 步，不会自己把「来不及走完」的状态清零。
4. **`αβ` 对 `y^t_k` 是 2 次的**，这正是式(15) 里出现 `(y^t_k)^2` 的原因；按 1 次推导会得到错误的梯度。
5. **best path 不等于最优 labelling**，做解码评估时别把两者混为一谈。
6. **`lab(l, k)` 可能为空**：目标串里没出现的标签，其梯度累加为空集，不要假设非空。
7. 论文 §4.1 明确要求**输出层不能有反馈连接**，否则各帧条件独立的假设不成立，式(2) 失效。

## 参考资料

- A. Graves, S. Fernández, F. Gomez, J. Schmidhuber. *Connectionist Temporal Classification: Labelling Unsegmented Sequence Data with Recurrent Neural Networks*. ICML 2006.（本 demo 全部公式与数值均出自该文，PDF 全文已本地抽取后逐节回读）<https://www.cs.toronto.edu/~graves/icml_2006.pdf>
- 论文引用的 baseline 方法：Rabiner, L. R. *A tutorial on hidden Markov models and selected applications in speech recognition*. Proc. IEEE 1989（前向-后向与缩放技巧的出处）。
- A. Graves. *Supervised Sequence Labelling with Recurrent Neural Networks*. PhD thesis / Springer 2012（CTC 章节给出与本文一致的更完整推导）。
