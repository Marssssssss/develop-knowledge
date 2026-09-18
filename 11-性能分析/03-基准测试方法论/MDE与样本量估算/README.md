# 最小可检测效应(MDE)与样本量估算

> "`-count` 该填多少?"不是玄学,而是一个可解的方程。本 demo 把它解出来,并顺带说明为什么 benchstat 官方建议的"至少 10 次、理想 20 次"在噪声大的机器上**根本不够**。

## 1. 权威公式:NIST e-Handbook §7.2.2.2

σ **已知**(用正态临界值):

```
N = (z_{1-α/2} + z_{1-β})² (σ/δ)²     双侧
N = (z_{1-α}   + z_{1-β})² (σ/δ)²     单侧
```

σ **未知**(用样本标准差 `s`,走 t 分布)——原文强调必须**迭代**:

> The drawback is that critical values of the t distribution depend on known degrees of freedom, which in turn depend upon the sample size which we are trying to estimate. **Iterate on the initial estimate** using critical values from the t table.

自检 `[1][2]` 复现了原文的两个算例:

| 情形 | 原文 | 本 demo |
| --- | --- | --- |
| 单侧 α=0.05、β=0.10、δ=σ、σ 已知 | `(1.645+1.282)² = 8.567 ≈ 9` | `8.5638 → 9` |
| 同上但 σ 未知,以 df=8 迭代一次 | `(1.860+1.397)² = 10.6 ≈ 11` | `10.6039 → 11`(再迭代仍为 11) |

原文那句 "in practice one iteration is usually sufficient" 也被验证了:第二次迭代(df=10)给出 10.14,取整仍是 11。

## 2. 从单样本到两组:×2 是推导出来的,不是背的

NIST §7.2.2.2 给的是**单样本**(拿样本均值去比一个已知常数)公式。基准场景是**两组**(改前 vs 改后)。两组的均值差标准误是 `σ√(2/n)`,单样本是 `σ/√n`;要让检验的非中心参数 `δ/SE` 相等,必须 `√(2/n_two) = √(1/n_one)`,于是

```
n_two(每侧) = 2 · n_one
```

自检 `[3]` 没有停在代数上:它用**解析功效公式**验证 `n=16` 时功效 = 0.8074,正好落在目标 0.80 附近(精确解 15.70 向上取整)。Python 版还额外跑了一次 4000 次的蒙特卡洛,得到 0.784,与解析值同量级(差值是 Welch 用估计 σ 而非已知 σ 的代价)。

## 3. 把公式翻过来:给定噪声,能检出多大的回归?

令 `CoV = σ/μ`、`MDE_rel = δ/μ`,则 `σ/δ = CoV/MDE_rel`。代入并解出 MDE:

```
MDE_rel = (z_{1-α/2} + z_{1-β}) · CoV · √(2/n)
n       = 2 (z_{1-α/2} + z_{1-β})² (CoV / MDE_rel)²
```

**这是本 demo 最有工程价值的一条式子**。它直接告诉你:

| 每侧样本量 n | CoV=1% 时的 MDE | 说明 |
| --- | --- | --- |
| 10 | **1.253%** | benchstat 的"至少 10 次" |
| 20 | **0.886%** | benchstat 的"理想 20 次" |
| 63 | 0.499% | 想在 1% 噪声下检出 0.5% 回归 |

自检 `[6][7]` 断言了这三个数,并验证 `MDE(10)/MDE(20) = √2`——**样本量翻 4 倍,MDE 才减半**(`[5]`)。

推论:benchstat 的 20 次建议对应 `MDE ≈ 0.886 × CoV`。**想可靠检出 1% 的回归,就得先把 CoV 压到 1.1% 以下**——这正是[CoV噪声地板/](../CoV噪声地板/)里 Apogee 先把变异性从 60% 打到 5% 的原因。

## 4. 一个容易写错的断言:取整的方向

`n` 必须**向上取整**,所以实际达到的 MDE 只会**优于**目标,不可能恰好相等。自检 `[4]` 里第一版写了 `assert back == mde`,直接失败(精确解 62.79 → 63 → 反解 0.004992)。改成断言方向后通过:

```python
assert back <= mde + 1e-12          # 取整后必然优于目标
assert back > mde * (1 - 1/n_need)  # 但不会偏离超过一次样本的量
```

## 5. 非参数检验要多付一点样本量

benchstat 默认 `assume=nothing`,用的是**中位数 + Mann-Whitney U** 而不是 t 检验。自检 `[9]` 在 n=16 上实测:Welch t 功效 0.784,U 检验 0.755。所以上面公式给出的 n 应视为**下界**,实际应略微上浮。

## 6. 运行与自检

```bash
cd python && python mde_sample.py     # 9 组断言,全部实跑通过
cd ../go    && go run mde_sample.go   # Go 镜像(功效改走解析式,不依赖 RNG 实现)
```

`mde_stats.py` 提供底层分布:正态分位数(标准库 `NormalDist`)、不完全 Beta 连分式、t 分布 CDF/分位数(二分迭代)、Welch t(Welch-Satterthwaite 自由度,公式取自 NIST §1.3.5.3)、带并列秩与连续性校正的 Mann-Whitney U。**无第三方依赖**。

## 7. 注意事项与常见坑

1. **`n=1` 不是"不精确",是"无解"**。每侧 1 个样本时 Welch 自由度为 0,t 分位数根本不存在(自检 `[8]`)。`-count=1` 得到的 `ns/op` 只能当单次观测量,不能做检验。
2. **别用观测到的效应做事后功效分析(post-hoc power)**。那是用结果论证过程,循环论证。MDE 必须在实验**之前**给定。
3. **α 与 β 的选择会显著改变 n**。从 power=0.80 提到 0.95,`z_{1-β}` 从 0.842 涨到 1.645,n 变成约 2.1 倍。
4. **公式假设独立同分布的正态(或至少轻尾)样本**。基准数据常见重尾/多峰,此时正态公式会**乐观**,真实所需 n 更大(见[统计检验的选择/](../统计检验的选择/))。
5. **公式给的是"每侧"n**。benchstat 的 `-count=10` 同时作用于 old 与 new,所以两侧各 10 —— 与公式的 "per group" 一致。

## 参考资料(实际阅读过的来源)

- [NIST/SEMATECH e-Handbook §7.2.2.2 — Sample sizes required](https://www.itl.nist.gov/div898/handbook/prc/section2/prc222.htm) — σ 已知/未知的单双侧样本量公式原文、δ 以标准差为单位的表述、"iterate on the initial estimate" 的要求,以及 8.567→9 与 10.6→11 两个算例
- [NIST/SEMATECH e-Handbook §1.3.5.3 — Two-Sample t-Test for Equal Means](https://www.itl.nist.gov/div898/handbook/eda/section3/eda353.htm) — 检验统计量、Welch-Satterthwaite 自由度公式原文
- [`pkg.go.dev/golang.org/x/perf/cmd/benchstat`](https://pkg.go.dev/golang.org/x/perf/cmd/benchstat) — "Each benchmark should be run at least 10 times"、"at least 10, ideally 20"、默认 `assume=nothing` 走非参数统计、`assume=exact` 分支
- [`pkg.go.dev/testing`](https://pkg.go.dev/testing) — `-count` 对应的测试二进制标志与重复运行语义
