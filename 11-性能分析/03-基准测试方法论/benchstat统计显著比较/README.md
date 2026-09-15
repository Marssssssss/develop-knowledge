# benchstat 统计显著比较

## 简介

`benchstat` 是 Go 官方工具链里的**基准结果统计与 A/B 对比**工具（`golang.org/x/perf/cmd/benchstat`）。它解决的问题是：`go test -bench` 只给你一串**标量**数字，而两次运行的差异里既可能藏着真实回归，也可能只是噪声——benchstat 用非参数统计把"这个差异是噪声吗"变成一个可判定的问题。

本 demo 用 Python + Go 从零复现它的默认统计内核，不依赖任何第三方库。

关键概念：

- **assume=nothing（默认）**：不做分布假设 → 摘要用**中位数 + 置信区间**，A/B 对比用 **Mann-Whitney U 检验**（秩和检验，非参数）。
- **α = 0.05**：默认显著性阈值为 0.05，即"即使两组毫无差异，也期望有 5% 的对比被判成有差异"。
- **`~`**：benchstat 输出里的 `~ (p=0.446 n=10)` 表示**未检测到统计显著差异**（不是"两者相等"）。
- **geomean**：表格最后一行的几何平均值，其比例变化 = 各 benchmark 比例变化的几何平均。
- **n**：每个 benchmark 至少跑 **10** 次，理想 **20** 次。

历史背景：在 benchstat 之前，Go 团队靠人工比对 `go test -bench` 的原始数字；`-count` 与 benchstat 的组合（2016 年前后）才让"多次运行 + 分布比较"成为默认做法。其 U 检验实现（`internal/stats/utest.go`）明确引用了 Mann & Whitney 1947 与 Klotz 1966 两篇文献。

## 原理详解

### 1. 输入过滤与投影

`benchstat` 先按 `-filter` 表达式过滤（`key:value` / `key:/regexp/` / `key:(a OR b)` / `-x` 取反），默认投影等价于 `-table .config -row .fullname -col .file`。输入参数可写成 `label=path` 覆盖列标签（如 `benchstat O=old.txt N=new.txt`）。

### 2. 摘要统计量：中位数 + 置信区间

单位默认按 `assume=nothing` 处理：**用中位数做摘要**，并给出中位数的置信区间（输出形如 `1.718µ ± 1%`）。

与之相对，二进制大小这类**无噪声**测量应配置成 `assume=exact`：若测量值仍出现波动，benchstat 会告警，并且允许"前后各一次测量"就给出 A/B 对比。

我们的实现给出两种非参数中位数 CI 并交叉验证：

| 方法 | 做法 | 特征 |
| --- | --- | --- |
| 次序统计量法 | 取最大 k 使 `P(Bin(n,0.5) <= k-1) <= α/2`，CI = `[x(k), x(n-k+1)]` | 分布无关、偏保守（实测覆盖率 0.992 > 0.95），有并列秩时必须向外扩张 |
| bootstrap 百分位法 | 有放回重采样 B 次取中位数分布的第 α/2、1-α/2 分位 | 区间更窄（实测 100% 情况更窄），但依赖重采样实现与随机种子 |

### 3. A/B 对比：Mann-Whitney U 检验

步骤（与 `utest.go` 一致）：

1. 合并两组样本升序排序，**并列值取平均秩**；统计各并列组规模 `tj`。
2. 计算样本 1 的秩和 `R1`，得 `U1 = R1 - n1(n1+1)/2`，`U2 = n1*n2 - U1`（`U1 + U2 = n1*n2`）。
3. 检验统计量取 **`Usmall = min(U1, U2)`**。
4. p 值：

```text
小样本(无并列 n<=50, 有并列 n<=25): 用精确 U 分布
    双侧: U1 == U2 时 p = 1      ← 离散分布不能直接 2*CDF, 否则把 Usmall 处的概率质量算两遍
          否则 p = 2 * CDF(Usmall)
大样本: 正态近似 + 并列秩校正 + 连续性校正
    t      = Σ (tj^3 - tj)
    σ_U    = sqrt( n1*n2*((N+1) - t/(N*(N-1))) / 12 ),  N = n1+n2
    numer  = U1 - n1*n2/2, 然后 numer -= sign(numer)*0.5
    p      = 2 * min( Φ(z), 1-Φ(z) ),  z = numer/σ_U
```

**精确 U 分布**的等价刻画（本 demo 的实现方式）：`U` 的分布与"从 `1..N` 中取 `n1` 个元素的**子集和**分布"同形——令 `T1` 为样本 1 的秩和，则 `U1 = T1 - n1(n1+1)/2`，于是只需对所有子集和计数（`dp[k][s]`），总方案数为 `C(N, n1)`。这条等价关系让"精确分布"退化成一个组合计数 DP，自检里用 `Σ counts == C(N,n1)` 做锚点。

U 统计量还有若干等价形式需要注意（`utest.go` 注释明确列出）：Wilcoxon (1945) 的 `W = U + n1(n1+1)/2`；也有用 `2U` 消除半整数步长的；Smid (1956) 用 `n1*n2 - 2U` 再居中。**U 本身是 0.5 的整数倍**（有并列时）。

### 4. geomean 的比例语义

geomean 是"每个 benchmark 的相对变化"的正确聚合方式：

```text
geomean 比值 = exp( mean( ln(after_i / before_i) ) )
若有 n 个 benchmark 中恰有一个变成 2 倍 -> geomean 比值 = 2^(1/n)
```

自检里用 8 个 benchmark 验证了这一点（`2^(1/8) = 1.0905`）。这也解释了为什么"一个大回归 + 一堆小改进"在 geomean 上看起来温和。

### 5. α、多重检验与"统计显著 ≠ 幅度大"

- 默认 α = 0.05，因此**比较大量 benchmark 时，即使没有任何差异，也应预期约 5% 的报告显著变化**。500 个 benchmark 的 CI 里出现约 25 个"显著"是正常的。
- 官方文档明确警告两种错误用法：**反复重跑 benchmark 直到出现显著变化**（multiple testing，几乎必然"找到"差异）；以及把"统计显著"当成"变化很大"——数据噪声足够低时，极小的变化也能被判为显著。

## 环境准备

- 操作系统：任意（本 demo 不依赖平台）
- Python：3.10+（使用 `math.comb`）
- Go：1.21+（仅标准库 `math`/`sort`/`rand`）
- 依赖：无

## 运行方式

### Python

```bash
python3 python/benchstat_lite.py
```

### Go

```bash
cd go && go run benchstat_lite.go
```

## 关键代码片段

```python
# U 统计量 + 精确/近似两条 p 值路径(对应「原理详解」第 3 步)
r1, tie_sizes = _average_ranks(x1, x2)          # 平均秩 + 并列组规模
u1 = r1 - n1 * (n1 + 1) / 2.0                   # U1 = R1 - n1(n1+1)/2
u2 = n1 * n2 - u1                               # 镜像, U1+U2 = n1*n2
u_small = min(u1, u2)                           # 检验统计量取较小者
if 无并列 and n1 <= 50 and n2 <= 50:
    counts = _u_distribution_exact(n1, n2)      # 子集和 DP
    if u1 == u2:
        p = 1.0                                 # 离散分布特例
    else:
        p = 2.0 * sum(c for u, c in counts.items() if u <= u_small) / math.comb(n1 + n2, n1)
else:
    t = sum(ts ** 3 - ts for ts in tie_sizes)   # 并列秩校正 Σ(tj^3 - tj)
    sigma2 = n1 * n2 * ((N + 1) - t / (N * (N - 1))) / 12.0
    numer = (u1 - n1 * n2 / 2.0) - math.copysign(0.5, u1 - n1 * n2 / 2.0)   # 连续性校正
    p = 2.0 * min(_normal_cdf(numer / math.sqrt(sigma2)), 1 - _normal_cdf(numer / math.sqrt(sigma2)))
```

## 性能与边界

| 项 | 数值 / 边界 |
| --- | --- |
| 精确分布代价 | 子集和 DP 为 `O(N * n1 * n1*N)`；`n1=n2=50` 时组合数 `C(100,50) ≈ 1e29`，故官方在**无并列时 n ≤ 50、有并列时 n ≤ 25** 处切到正态近似 |
| 官方精确分布的耗时参考 | 两个 50 值样本"几毫秒"（2014 年笔记本）；**有并列**的两个 25 值样本约 10 ms —— 所以有并列的阈值调低 |
| 渐进相对效率（ARE） | U 检验对正态分布数据的效率约为 t 检验的 `3/π ≈ 0.955`，即"略低一点"而非低很多 |
| 中位数 CI | 次序统计量法覆盖率**保守 ≥ 1-α**（本机实测 n=21 指数分布下 0.992） |
| 最小样本量 | `n ≥ 10`，理想 `n = 20`；`n` 越大能分辨的差异越小 |

## 注意事项与常见坑

- **先 A 后 B 是错的采样顺序**：官方建议**交错运行**（A/B/A/B/…）而不是"跑 10 次 before 再跑 10 次 after"，否则机器状态漂移会混进组间差异。
- **不要重跑到显著**：α=0.05 下反复重跑必然"找到"显著差异，属于多重检验错误。
- **`~` 不是"相等"**：它只是"在当前噪声水平与样本量下，没证据说明它们不同"。要缩小可分辨的最小差异，唯一正确做法是**降噪 + 加样本**。
- **并列秩必须取平均**：不做平均会让 U 统计量偏移；正态近似路径还必须带上 `Σ(tj^3-tj)` 校正，否则 σ 偏小、p 值偏小（更容易"显著"）。
- **双侧离散特例**：`U1 == U2` 时若直接写 `2*CDF(Usmall)` 会得到 p > 1 或重复计数，官方实现显式返回 `p = 1`。
- **全等样本是错误而非"无差异"**：`utest.go` 在 `Σσ_U == 0`（所有值相等）时返回 `ErrSamplesEqual`，而不是 p=1。
- **单位归一化**：输出会把 `ns` 归一化成 `sec`、`MB` 归一化成 `B`，避免出现 `µns/op` 这类无意义单位。
- **环境**：官方建议在空闲机器上跑、不依赖电池供电、避免热降频（并给出 LLVM 基准测试文档作为参考）；`go test -c` 预编译可减少构建干扰。

## 参考资料（实际阅读过的权威来源）

- [benchstat — pkg.go.dev 官方文档](https://pkg.go.dev/golang.org/x/perf/cmd/benchstat) — 默认 `assume=nothing` 使用中位数 + Mann-Whitney U；α=0.05 与"5% 假阳性"说明；geomean 的比例语义；n≥10/理想 20；`-filter` 语法与投影 flag；"统计显著 ≠ 变化大"；交错运行与"不要重跑到显著"的警告
- [golang/perf — internal/stats/utest.go 源码](https://raw.githubusercontent.com/golang/perf/master/internal/stats/utest.go) — U 统计量定义（含并列按 0.5 计）、`U1+U2=n1*n2`、精确分布阈值 `MannWhitneyExactLimit=50` / `MannWhitneyTiesExactLimit=25`、并列秩校正与连续性校正、`U1==U2 → p=1` 的离散特例、以及 Mann & Whitney 1947 / Klotz 1966 两篇原始文献引用
- [Go benchproc 过滤器语法](https://pkg.go.dev/golang.org/x/perf/benchproc/syntax) — `-filter` 的键集合（`.name`/`.fullname`/`.file`/`.unit`）与布尔组合语法
