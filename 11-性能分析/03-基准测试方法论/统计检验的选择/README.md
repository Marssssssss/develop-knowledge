# 统计检验的选择

## 简介

拿到两组基准数据（before/after 各 n 次）之后，"差异是真实的吗"有**两个完全不同的问题**，对应**两类完全不同的检验**：

1. **均值是否不同** → `t` 检验（等方差用 pooled，不等方差用 Welch）；回答"平均而言贵了多少"。
2. **一组是否随机地大于另一组** → `Mann-Whitney U`（秩和检验，非参数，H0 是"两组同分布/无随机优势"）；回答"是不是更常变慢"。

选错检验不会报错，只会给出**看起来合理但回答了另一个问题**的结论。本 demo 用 Python + Go 实现两类检验，然后用 6 个场景把差异实测出来。

关键概念：

- **robustness of validity / efficiency**（Tukey & Mosteller 术语，NIST 手册引）：前者=置信区间**无论底层分布是什么**都有 95% 覆盖率的性质；后者=置信区间**足够窄**。均值在正态下最优但缺乏 validity；中位数有 validity 但缺 efficiency；截尾均值/Winsorized 均值是折中。
- **Welch–Satterthwaite 近似**：不等方差时 t 检验的自由度近似公式。
- **ARE（渐近相对效率）**：U 检验对正态数据的效率约为 t 的 `3/π ≈ 0.955`。
- **`assume=nothing` / `assume=exact`**：benchstat 用前者（非参数）处理有噪声的性能数据，用后者处理二进制大小这类无噪声测量。

## 原理详解

### 1. t 检验：回答"均值差"

```text
等方差(pooled):   t = (Ȳ-Z̄) / sqrt( s_p² (1/N1 + 1/N2) ),  ν = N1+N2-2
不等方差(Welch):  t = (Ȳ-Z̄) / sqrt( s1²/N1 + s2²/N2 ),
                  ν = (s1²/N1 + s2²/N2)² / [ (s1²/N1)²/(N1-1) + (s2²/N2)²/(N2-1) ]
双尾 p 值:        P(|T| > |t|) = I_{ν/(ν+t²)}(ν/2, 1/2)     ← 正则化不完全 Beta 函数
```

NIST 手册 7.3.1 的算例被用作外部锚点（本 demo 自检复现）：旧工艺 `36.0909 ± 4.9082 (N=11)`、新工艺 `32.2222 ± 2.5386 (N=9)` → `t = 2.2694`、`ν ≈ 15.5`，单尾 5% 临界值 1.746，故拒绝原假设、判定新工艺更快。

**前提**：独立性（最重要）、小样本时近似正态、方差是否齐性（不齐用 Welch）。NIST 对分布的原文判断是：**均值缺乏 robustness of validity —— 基于均值的置信区间在非正态下不精确**。

### 2. 位置估计量与重尾：均值什么时候彻底失效

NIST 手册用四个分布的直方图做了对照（各 10 000 个样本）：

| 分布 | 均值 | 中位数 | 结论 |
| --- | --- | --- | --- |
| 正态 | 0.005 | -0.010 | 三者等价，均值最优 |
| 指数（偏态） | 1.001 | 0.684 | 均值被右尾拉高；三者都可解释 |
| **Cauchy（重尾）** | **3.70** | **-0.016** | 均值"高于绝大多数数据"；**取样更多不会更准** |
| 对数正态 | 1.677 | 0.989 | 同指数分布 |

Cauchy 的关键性质（原文）：*"the sampling distribution of the mean is equivalent to the sampling distribution of the original data"* —— 均值作为位置估计量**彻底无用**，而中位数仍有效。本 demo 的 F 场景实测：`n=10 → n=1000` 时均值的散布从 6.31 变成 30.42（**不降反升**），中位数（n=1000）的散布仅 0.05。

折中估计量（NIST 定义）：**mid-mean**（25%~75% 分位内取均值）、**trimmed mean**（两端各截 5%）、**Winsorized mean**（越界值改写成边界值而非删除）。它们"对极端值不敏感"，同时"在正态数据下接近均值"。

### 3. Mann-Whitney U：回答"随机优势"

```text
U1 = R1 - n1(n1+1)/2       R1 = 样本 1 的秩和
σ_U² = n1·n2·[ (N+1) - Σ(tj³-tj)/(N(N-1)) ] / 12
p   = 2·min(Φ(z), 1-Φ(z)),  z = (U1 - n1n2/2 ∓ 0.5)/σ_U
```

H0 是"两组无随机优势"，等价于 `P(X>Y) = 0.5`——注意这**不是**"中位数相等"，也**不是**"均值相等"。

### 4. 六个场景的实测结果（本 demo 自检输出）

| 场景 | 构造 | t 检验 | U 检验 | 说明 |
| --- | --- | --- | --- | --- |
| A 整组平移 | 第二组整体 +3 | p=1.7e-05 显著 | p=8.8e-05 显著 | 真实均值差异，两者都检出 |
| B 局部抬高 | 40 个样本里 8 个被抬到 140 | **p=0.0036 显著** | **p=0.091 不显著** | 均值被拉动，秩几乎不变 |
| C 重尾 | 对数正态 σ=1.5 + 1 个 1000 倍离群点 | 均值散布 233.3 | 中位数散布 0.603 | 次序 `median < trimmed5% < winsorized5% << mean` |
| D 效率 | 正态 + 效应量 | n=10 功效 0.238 | n=10 功效 0.213（比值 0.894）；n=150 比值 0.972 | 逼近 ARE `3/π ≈ 0.955`，但**是渐近量** |
| E 覆盖率 | 对数正态 σ=1.5, n=10 | 均值 t 区间覆盖 **0.737** | 中位数区间覆盖 **0.980** | 名义 0.95 下 t 区间严重失真 |
| F Cauchy | `N(0,1)/N(0,1)` | n=10→n=1000 散布 6.31→30.42 | 中位数散布 0.05 | 加样本量救不了均值 |

场景 B 是整份 demo 的核心：**同一批数据，两种检验给出相反结论**，两者都没错——它们回答的是不同问题。

### 5. 决策规则

| 你要回答的问题 | 用什么 | 理由 |
| --- | --- | --- |
| "平均耗时变化了多少" | Welch t + 均值 CI | 直接对应均值；但要确认分布不重尾 |
| "是不是更常变慢 / 更常变快" | U 检验（或 `P(X>Y)` 估计） | 秩稳健，不需要分布假设 |
| "典型（大多数）请求多快" | 中位数 + 分位数（p50/p90/p99） | 均值被尾部拖动，中位数不 |
| "最坏情况多慢" | 高分位数（p99/p99.9） | 只有分位数回答"最坏" |
| "二进制大小 / 指令数等无噪声指标" | `assume=exact` 类比较 | 无噪声时方差为 0，参数/非参数都退化 |

## 对比 / 选型

| 维度 | t 检验（Welch） | Mann-Whitney U |
| --- | --- | --- |
| 零假设 | 均值相等 | `P(X>Y) = 0.5`（同分布/无随机优势） |
| 分布假设 | 小样本需近似正态 | 无（只需独立） |
| 对极端值 | 敏感（均值被拉动） | 稳健（只影响一个秩） |
| 正态下效率 | 100% | ≈ 95.5%（ARE = 3/π） |
| 重尾下有效性 | 区间覆盖率会失真（实测 0.737） | 保持一致 |
| 典型误用 | 用重尾原始延迟直接做 t 检验 | 把"U 显著"解释成"均值不同" |

## 环境准备

- 操作系统：任意
- Python：3.10+；Go：1.21+
- 依赖：无（t 分布用不完全 Beta 函数自行实现）

## 运行方式

### Python

```bash
cd python && python3 test_selection.py
```

### Go

```bash
cd go && go run test_selection.go
```

## 关键代码片段

```python
# 双尾 p 值靠不完全 Beta 函数(对应「原理详解」第 1 步)
def t_two_sided_p(t, df):
    return betainc_reg(df / 2.0, 0.5, df / (df + t * t))

def welch_t(x, y):                       # Welch-Satterthwaite 自由度
    n1, n2 = len(x), len(y)
    v1, v2 = statistics.variance(x) / n1, statistics.variance(y) / n2
    t = (statistics.fmean(x) - statistics.fmean(y)) / math.sqrt(v1 + v2)
    df = (v1 + v2) ** 2 / (v1 ** 2 / (n1 - 1) + v2 ** 2 / (n2 - 1))
    return t, df, t_two_sided_p(t, df)

# U 检验的"并列秩校正 + 连续性校正"(对应第 3 步)
tie_corr = sum(v ** 3 - v for v in ties)
sigma2 = n1 * n2 * ((n + 1) - tie_corr / (n * (n - 1))) / 12
numer = u1 - n1 * n2 / 2
numer -= math.copysign(0.5, numer)       # 连续性校正
```

## 性能与边界

- **t 分布实现**：连分式迭代上限 200、收敛阈值 3e-16；实测 df=10 的临界值 2.228139 与教科书值一致（误差 < 1e-5）。
- **蒙特卡洛成本**：场景 D 的 2×2000~3000 次双边检验 + 场景 E 的 2×600 次区间估计在单机上约数秒量级，是本 demo 的主要耗时。
- **ARE 的适用边界**：`3/π` 是**渐近**相对效率，不是有限样本的功效比（实测 n=10 时比值 0.894，n=150 时 0.972）。
- **U 检验的并列边界**：并列比例极高时秩信息被压缩（极端情形全部相等 → σ_U=0，检验无意义），此时应改用分位数比较或直接看分布。
- **分位数估计的样本量**：p99 需要至少上百个样本才有意义；p99.9 需要上千。样本量不足时分位数本身抖得比均值还厉害。

## 注意事项与常见坑

- **把"U 显著"读成"均值不同"**：U 检验的 H0 是随机优势而非均值相等（场景 B 就是反例）；反过来，U 不显著也不能推出"两组一样"。
- **重尾数据上直接用 t 检验**：均值 CI 的名义 95% 在 σ=1.5、n=10 的对数正态下实测只有 **0.737**（场景 E），结论会系统性失真。
- **把 ARE 当有限样本功效比**：`3/π` 只在 n→∞ 成立；小样本下偏离明显。
- **重尾下报告均值**：NIST 原文的措辞已足够强烈（Cauchy 下"collecting more data does not provide a more accurate estimate of the mean"），此时"均值 ± 标准差"是**误导性**摘要。
- **trimmed / Winsorized 的参数易混**：两者都取两端相同比例，但一个是**删除**、一个是**改写为边界值**；比例定义（5% 是"每端 5% 的观察数"还是"分位数")不同实现会有差异，跨工具比较前需确认口径。
- **多重比较**：一次比较几十个 benchmark 时，α=0.05 意味着约 5% 的假阳性（见 `../benchstat统计显著比较/`）；要么校正，要么把结论定位在"趋势"而非单个显著。
- **独立性与交错采样**：两组样本必须独立；同一台机器上"先跑完 before 再跑 after"会让机器状态漂移混进组间差异，应交错运行。
- **小样本不要挑检验**：先决定要回答的问题，再选检验；"试几个检验挑显著的"就是 p-hacking。

## 参考资料（实际阅读过的权威来源）

- [NIST/SEMATECH e-Handbook — 1.3.5.1 Measures of Location](https://www.itl.nist.gov/div898/handbook/eda/section3/eda351.htm) — 均值/中位数/众数定义；正态/指数/Cauchy/对数正态的均值-中位数对照数值；Cauchy 下"取样更多不改善均值"的原文结论；Tukey & Mosteller 的 robustness of validity / efficiency 定义；mid-mean / trimmed mean / Winsorized mean / mid-range 四种替代估计量
- [NIST/SEMATECH e-Handbook — 7.3.1 Do two processes have the same mean?](https://www.itl.nist.gov/div898/handbook/prc/section3/prc31.htm) — 等方差与不等方差两种 t 统计量形式、Welch-Satterthwaite 自由度公式、单尾检验的完整算例（t=2.2694、ν≈15.5、临界值 1.746）
- [NIST/SEMATECH e-Handbook — 7.3.5 Do two arbitrary processes have the same central tendency?](https://www.itl.nist.gov/div898/handbook/prc/section3/prc35.htm) — U 检验的秩和流程（并列取平均秩、U1+U2=n1n2）、大样本正态近似 `z=(U-E(U))/σ` 与 `E(U)=n1n2/2`、以及"不做正态假设"的定位
- [benchstat 官方文档](https://pkg.go.dev/golang.org/x/perf/cmd/benchstat) — `assume=nothing` 默认用中位数 + Mann-Whitney U（非参数）、`assume=exact` 用于无噪声测量、"统计显著 ≠ 幅度大"与多重检验的 5% 假阳性说明
- [golang/perf — internal/stats/utest.go](https://raw.githubusercontent.com/golang/perf/master/internal/stats/utest.go) — "It has **very slightly lower efficiency** than the t-test on normal distributions." 以及精确分布阈值（无并列 50 / 有并列 25）、并列秩校正与连续性校正的实现
