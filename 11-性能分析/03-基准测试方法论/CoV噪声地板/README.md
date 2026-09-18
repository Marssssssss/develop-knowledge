# 变异系数、噪声地板与降噪

> 在谈"用什么检验"之前,先问一句:**你的测量噪声有多大?** Dropbox 的 Apogee 团队在能可靠检出回归之前,花了大半精力把变异性从 60% 打到 5%。本 demo 把这个工程顺序变成可计算的量。

## 1. 为什么是 CoV 而不是标准差

标准差带量纲,100 ns 的抖动对 1 µs 的基准是灾难、对 1 s 的基准是噪声。**变异系数 `CoV = s/x̄` 无量纲**,所以能跨基准横向比较 —— Apogee 原文正是用它做主指标:

> To understand where the variability was coming from, we used the **coefficient of variation** of other metrics (network i/o, disk i/o, and memory usage) to correlate it with that of the duration using the t-test.

自检 `[1]` 固化了这个性质:整列数据缩放 7.3 倍,CoV 一字不差。

## 2. 噪声地板:A/A 基线到底有多大

不改动任何代码,连跑两组各 n 次,你**仍然**会看到一个非零的"回归"。它的典型幅度是可算的:

```
E[|Δx̄|]/μ = CoV · √(2/n) · √(2/π)
```

`√(2/π)=0.7979` 是标准正态绝对值 `E|Z|` 的期望。自检 `[5]` 用 μ=100、σ=2(CoV=2%)、n=20 实测得 **0.4965%**,理论 **0.5046%**。

**这个数就是噪声地板**:任何小于它的"改进"或"退化"都没有信息量。把它和你打算告警的阈值相比,立刻能判断这套门禁是否可信。

## 3. 核对 Apogee 那句结论

官方原文:

> With all the work above, we were able to get the variability down **from 60%** in the worst case **to less than 5%** for all of our tests. This means we can confidently detect 5% or greater regressions.

用 [MDE与样本量估算/](../MDE与样本量估算/) 的公式核对(α=0.05、power=0.8):

| 每侧 n | CoV=5% 时的 MDE | 能否检出 5% |
| --- | --- | --- |
| 10(benchstat 下限) | **6.26%** | ✗ |
| 16 | **4.95%** | ✓ |

也就是说:**"CoV<5% ⇒ 能检出 5% 回归"这句话本身依赖每侧至少 16 次采样**,官方没有明写这个前提。自检 `[3]` 把两个数和 `n_from_cov_mde(0.05, 0.05) == 16` 都断言下来 —— 这是口径差异,记录下来,不替它改正。

反向看更惊人:若**不降噪**(CoV=60%)却想检出 5% 回归,每侧需要 **2261 次**(自检 `[8]`)。工程上不可行 —— 这就是"降噪优先于判据"。

## 4. 降噪与加样本可以互换,但成本曲线不同

自检 `[4]` 给出一组对照:把 MDE 从 6.26% 降到 3.13%(减半),

- 路线 A:**样本量 ×4**(每侧 10 → 41 次);
- 路线 B:把 **CoV 从 5% 压到 2.5%**,样本量不变。

数学上等价(样本量与 `CoV²` 成正比、与 `MDE²` 成反比),但现实中路线 B 常常更便宜——改机器配置是一次性的,加 4 倍采样是每次 CI 都要付的。

## 5. 离群点剔除是**有偏**的

Apogee 用了这个手段:

> We were able to improve this further by **rejecting one outlier per five runs** of a test to give us more confidence when alerting on regressions.

注意它剔的是**最差**(最慢)的那次,不是"离中位数最远"的那个。所以这是**单向**操作,必然把均值往好的方向拉。

对正态样本,每 5 个里丢掉最大值:

```
E[trimmed mean] = μ − E[max(Z₁..Z₅)] · σ / 4 = μ − 0.2907σ
```

Python 自检 `[6]` 实测 n=2000 得 −0.2714σ、n=20000 得 −0.2860σ,向理论值收敛。Go 版不抄这个常数,而是用 Simpson 积分现算 `∫ x·5·Φ(x)⁴·φ(x) dx = 1.162964`。

**关键:这个偏差是 O(σ),不是 O(σ/√n)**。样本量从 2000 翻到 20000,偏差没变小(0.2714σ → 0.2860σ,反而更接近理论值);而均值的标准误此时只有 0.007σ。自检断言 `|bias| / (σ/√2000) > 10` —— **加样本量救不了它**。

推论:如果 A/B 两侧的离群结构不同(比如修复了一个偶发的长尾),剔除规则本身就会贡献一部分"变快了"。

## 6. 交错运行:一个可以精确到 n 倍的结论

benchstat Tips 原文:

> It's also important that noise is evenly distributed across benchmark runs. The best way to do this is to **interleave** before and after runs, rather than running, say, 10 iterations of the before benchmark, and then 10 iterations of the after benchmark.

在有线性环境漂移(升温、降频、邻居抢 CPU)时,自检 `[2]` 把这个建议算成了精确倍数:

- 顺序跑(A 在时刻 0..n−1,B 在 n..2n−1):漂移贡献 **drift × n**;
- 交错跑(A 在 2i、B 在 2i+1):漂移贡献 **drift × 1**;
- 比值**恰好是 n**,对 n=5/10/20 逐一断言到 1e-9。

所以"先跑 10 次旧的再跑 10 次新的"会把 10 个时间单位的漂移整体误判成回归。

## 7. 运行与自检

```bash
cd python && python cov_noise.py    # 8 组断言,全部实跑通过
cd ../go    && go run cov_noise.go  # 全确定性镜像(Simpson 积分算次序统计期望)
```

## 8. 注意事项与常见坑

1. **CoV 用样本标准差**(n−1),不要写成总体标准差;n<2 时无定义。
2. **别把噪声地板当"误差条"**。它描述的是"零效应时你会看到多大的差异",不是一个置信区间。
3. **剔除规则要先写死再跑**。事后挑"看起来离谱的"样本剔除,等于把 p 值变成可调的。
4. **LLVM 的降噪清单与 Apogee 的数字口径不同**,不要混用:Apogee 是端到端同步测试(60%→5%),LLVM 说的是**微基准在关掉 ASLR/Turbo/超线程对并用 cpuset 隔离之后,perf 的波动可以小于 0.1%**。
5. **低噪声不等于无偏**。LLVM 文档开头就警告:"low noise is required, but not sufficient. It does not exclude measurement bias."(引用 Mytkowicz et al.,ASPLOS 2009)。

## 参考资料(实际阅读过的来源)

- [Keeping sync fast with automated performance regression detection — Dropbox Tech Blog (Rishabh Jain & David Aeschlimann, 2020-08-19)](https://dropbox.tech/infrastructure/keeping-sync-fast-with-automated-performance-regression-detectio) — "from 60% ... to less than 5%"、"confidently detect 5% or greater regressions"、"rejecting one outlier per five runs"、用 CoV 做相关分析的方法论、RAM disk / headless / 静态分片 / 终止 Spotlight 等降噪手段
- [Benchmarking tips — LLVM Documentation](https://llvm.org/docs/Benchmarking.html) — 关 ASLR、`scaling_governor=performance`、`cset shield` 隔离核、关闭 SMT 对、tmpfs、"With these in place you can expect perf variations of less than 0.1%"、以及 "low noise is required, but not sufficient" 与对 Mytkowicz et al. (ASPLOS 2009) 的引用
- [`pkg.go.dev/golang.org/x/perf/cmd/benchstat`(Tips 节)](https://pkg.go.dev/golang.org/x/perf/cmd/benchstat) — 交错运行的原文建议、闲置机器 / 电池 / 热降频、"Reducing noise and/or increasing the number of benchmark runs will enable benchstat to discern smaller changes"
- [NIST/SEMATECH e-Handbook §7.2.2.2](https://www.itl.nist.gov/div898/handbook/prc/section2/prc222.htm) — CoV 与样本量的关系所依赖的样本量公式
