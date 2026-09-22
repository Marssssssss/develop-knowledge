# HMM 三算法：前向-后向 / Viterbi / Baum-Welch

## 一、简介

隐马尔可夫模型（HMM）是最简单的**时序**概率图模型：一串隐状态 `q_1..q_T`（一阶马尔可夫）
每步发出一个观测 `o_t`（只依赖当前状态）。围绕它有三个必考问题，各对应一个动态规划：

| 问题 | 算法 | 复杂度 | 本 demo 的函数 |
|---|---|---|---|
| 求值 `P(O｜λ)` | 前向（或后向） | `O(T·N²)` | `forward` / `backward` |
| 解码 `argmax_Q P(Q｜O)` | Viterbi | `O(T·N²)` | `viterbi` |
| 学习 `argmax_λ P(O｜λ)` | Baum-Welch（EM） | 每轮 `O(T·N²)` | `baum_welch` |

`N^T` 条路径的直接枚举是 `O(T·N^T)`，前向算法把它压成 `O(T·N²)` —— 这靠的是
「`α_t(j)` 已经把 `o_1..o_{t−1}` 的所有历史折叠进一个数」这一条，
也就是 HMM 满足的 d-分离性质。

## 二、原理

### 2.1 两条基础递推

设 `A[i][j] = P(q_{t+1}=j｜q_t=i)`，`B[j][v] = P(o=v｜q=j)`，`π` 为初始分布。

```
α_1(j) = π_j · b_j(o_1)
α_t(j) = [ Σ_i α_{t−1}(i) · a_ij ] · b_j(o_t)          # 前向，Eq A.11
P(O)   = Σ_i α_T(i)

β_T(i) = 1
β_t(i) = Σ_j a_ij · b_j(o_{t+1}) · β_{t+1}(j)          # 后向
```

两者在任意时刻 `t` 都满足不变量 **`Σ_i α_t(i)β_t(i) = P(O)`**（不是只在 `t=T` 成立），
这既是自检最强的一条性质，也是 γ/ξ 的来源：

```
γ_t(i)   = P(q_t = i｜O)            = α_t(i)β_t(i) / P(O)
ξ_t(i,j) = P(q_t = i, q_{t+1}=j｜O) = α_t(i) a_ij b_j(o_{t+1}) β_{t+1}(j) / P(O)
```

并且恒有 `γ_t(i) = Σ_j ξ_t(i,j)`、`γ_{t+1}(j) = Σ_i ξ_t(i,j)` —— 两个方向的边缘化
必须同时成立，只验一个方向会漏掉下标写反。

### 2.2 为什么必须缩放

`T` 一大，`α_t` 就按 `≈ P(O)` 的指数速度趋零。`T = 1000` 时 `float64` 直接下溢成 0。
做法是每步除掉 `c_t = Σ_j α_t(j)`，得到 `α̂_t`；再让后向**用同一组 `c_t`** 缩放：

```
记 P_t = ∏_{s≤t} c_s，则 α̂_t = α_t / P_t
⇒ β̂_{T−1} = P(O)（因为 β_{T−1} = 1）
   β̂_t     = [Σ_j a_ij b_j(o_{t+1}) β̂_{t+1}(j)] / c_{t+1}
```

于是 `log P(O) = Σ_t log c_t`，且 `α̂β̂` 逐行归一化后**与未缩放的 γ 完全相同**
（自检实测误差 < 1e-12）。

> 符号坑：hmmlearn 的 `scaling_factors` 存的是**倒数** `1/c_t`，所以它写
> `log P = −Σ log c_t`；本 demo 的 `c_t` 是除数，写 `+Σ log c_t`。
> 两边都对，只是记法相反 —— 抄公式时不看清 `c_t` 的定义，符号一定写反。

### 2.3 Viterbi 与「后验解码」不是一回事

```
v_t(j) = max_i [ v_{t−1}(i) · a_ij ] · b_j(o_t)      # 取 max，不是 Σ
```

它最大化的是**整条路径的联合概率** `P(Q, O)`。而 hmmlearn 的 `_decode_map`
（即 `predict(algorithm="map")`）是逐时刻取 `argmax_i γ_t(i)` 后拼起来 ——
每一步各自最优，**拼出来的序列未必合法**（可能经过 `a_ij = 0` 的转移），
也未必是联合概率最大的那条。自检里专门构造了一个两者不同的例子，
并额外断言「Viterbi 路径的联合概率 > 后验解码路径的联合概率」，
避免只断言「两者不同」这种弱命题。

Viterbi 在对数域做：`log` 之后不能再乘回概率空间，只能比大小，
但换来了长序列不下溢。

### 2.4 Baum-Welch（EM）

E 步算 γ/ξ，M 步按期望计数重估（下标范围很关键）：

```
π̂_i     = γ_1(i)
â_ij     = Σ_{t=0}^{T−2} ξ_t(i,j)  /  Σ_{t=0}^{T−2} γ_t(i)
b̂_j(v)  = Σ_{t: o_t = v} γ_t(j)    /  Σ_{t=0}^{T−1} γ_t(j)
```

注意 `â` 的分子分母都**只累加到 `T−2`**（`ξ` 只有 `T−1` 个），而 `b̂` 用全部 `T` 项。
混用下标是最常见的静默错误：模型仍能跑，似然也会涨，只是收敛到错的极值点。

EM 保证对数似然**单调不降**，自检把它当作断言（容差 `1e-12`）。
注意「不降」不等于「能学到真参数」—— Baum-Welch 只保证到局部极大，
本 demo 只断言似然上升 + 参数各行归一化，**不断言恢复出真值**。

## 三、对比：三种口径

| 口径 | 输出 | 何时用 |
|---|---|---|
| 前向未缩放 | `P(O)` | 短序列、教学演示 |
| 前向缩放 | `log P(O)` + `α̂` | 工程实现（hmmlearn 默认），长序列唯一可行 |
| 对数域（`logsumexp`） | `log P(O)` | 更稳但慢；本 demo 未实现，Viterbi 单独走对数域 |

## 四、环境

- Python 3（仅标准库 `math` / `itertools`）
- Go 1.20+（无第三方依赖；本机无 Go 工具链时按 `_docs` 静态检查流程验收）

## 五、运行

```bash
cd python && python selfcheck_hmm.py       # → PASS = 63
cd ../go   && go run .                     # 打印各算法在冰激凌 HMM 上的输出
```

## 六、关键代码

```python
alpha, prob = forward(hmm, obs)            # O(T·N²)，prob = P(O|λ)
ahat, cs, logp = forward_scaled(hmm, obs)  # logp = Σ log c_t
bhat = backward_scaled(hmm, obs, cs)       # 必须复用同一组 cs
g = gamma_scaled(ahat, bhat)               # 逐行归一化后才等于 γ
score, path = viterbi(hmm, obs)            # 对数域 + 回溯指针
pd = posterior_decode(g)                   # 逐点 argmax，≠ viterbi
logs, model = baum_welch(hmm, obs, 15)     # logs 单调不降
```

## 七、性能边界

- 前向/后向/Viterbi：`O(T·N²)` 时间、`O(T·N)` 空间（Viterbi 要存 `T·N` 个回溯指针）。
- Baum-Welch 每轮要同时跑前向+后向并留 `ξ`，空间 `O(T·N²)`，`N` 大时这才是瓶颈。
- `brute_force` 是 `O(T·N^T)`，**只用于 `T ≤ 4` 的对照**，别拿去跑长序列。
- 缩放版本仍可能在某些病态参数下让 `c_t = 0`（某时刻所有状态的发射概率都是 0），
  此时 `log` 得 `-Inf`；生产实现要在此处抛错而不是继续算。

## 八、坑

1. **教材插图有歧义**：SLP3 Fig A.2 的 `A` 矩阵数字排布可读成两种。本 demo 取
   `[[.6,.4],[.5,.5]]`，但**所有定量断言都由暴力枚举独立验证**，不依赖这一处读数。
   凡是从图上抄的数，都要另找一条独立路径验证。
2. **缩放后的 `α̂β̂` 不是 γ**：它差了一个逐时刻常数，必须**逐行归一化**才等于 γ。
   只做 `α̂·β̂` 且不做归一化的实现，在 `T=1` 时恰好正确，之后全错 —— 极难发现。
3. **前后向要用同一组 `c_t`**：在 `backward_scaled` 里重新算一套缩放因子，
   得到的 β̂ 与 α̂ 不在同一尺度，`Σ_i α̂β̂` 不再恒定。
4. **`ξ` 比 `γ` 少一个**：下标到 `T−2` 为止，`â_ij` 的分子分母都要跟着截断到 `T−2`。
5. **长度为 1 的序列不更新 `A`**：hmmlearn 在 `_accumulate_sufficient_statistics_scaling`
   里明确 `if n_samples == 1: return`。这不是「没写」而是「没有转移可统计」。
6. **map ≠ Viterbi**：见 §2.3。把 `predict(algorithm="map")` 当 Viterbi 用，
   会得到一条可能非法的路径。
7. **`log` 里别直接传 0**：本 demo 用 `max(x, 1e-300)` 兜底，
   代价是极端小概率被截断成同一个 `-690`，比大小失效但不崩。

## 九、参考（本轮实读）

1. Jurafsky & Martin, *Speech and Language Processing* (3rd ed.) 附录 A
   [Hidden Markov Models](https://web.stanford.edu/~jurafsky/slp3/A.pdf)
   —— Eq A.7 `P(3 1 3｜hot hot cold) = .4 × .2 × .1 = 0.008`；Fig A.2 冰激凌 HMM；
   前向/Viterbi 递推。
2. hmmlearn@main [`src/hmmlearn/base.py`](https://raw.githubusercontent.com/hmmlearn/hmmlearn/main/src/hmmlearn/base.py)
   —— `_compute_posteriors_scaling`（`fwd*bwd` 后逐行归一化）、`_decode_map`
   （`argmax(posteriors)`）、`_accumulate_sufficient_statistics_scaling`
   （长度为 1 直接 `return`）、`_score_scaling` 的 `−Σ log c_t` 记法。
3. hmmlearn@main [`src/hmmlearn/stats.py`](https://raw.githubusercontent.com/hmmlearn/hmmlearn/main/src/hmmlearn/stats.py)
   —— `log_multivariate_normal_density` 等发射概率实现（本 demo 只处理离散发射，未用到）。

> 未采用：Rabiner 1989 经典教程（PDF 文本层提取失败，仅 501 字符可用），
> 故本 README 不引用其数值。
