# CI 性能回归门禁

## 简介

把性能纳入 CI 时，最容易犯的错是**把基准结果当成 pass/fail 断言**：`当前值 > 基线值 * 1.1` 就失败。这条路必然失败，原因就两条——**测量噪声**和**单点波动**。

本 demo 实现工业界真正在用的三层机制：

1. **分步拟合（step fitting）**：不比较相邻两次构建，而是在整条结果序列里找**持续位移**（阶跃）。相邻差分在纯噪声上就能产生 10% 级的假阳性；阶跃判据几乎不会。
2. **双判据（幅度 + 显著性）**：回归必须同时满足「相对变化 ≥ 幅度阈值」与「Welch t 检验显著」。只满足前者 = 噪声；只满足后者 = 微小但稳定的变化（记为 acceptable，不阻塞）。
3. **降噪前置 + 噪声地板**：先证明"同一份代码重复跑"的 CoV 足够低，再谈回归检测；并用历史噪声的 3σ 抬高幅度阈值，噪声大的指标自动放宽。

关键概念：

- **WIDTH / THRESHOLD**：阶跃检测的窗口宽度与灵敏度（工业实践里 WIDTH=5、THRESHOLD=25）。
- **`|step/fit|`**：Skia Perf 的原始判据——阶跃高度与"阶跃函数对该轨迹的均方拟合误差"之比。
- **双判据**：幅度阈值 10% + `p < 0.05`；df > 30 时 t 临界值退化为正态 `z = 1.96`。
- **多重比较**：一次比较 200 个 benchmark，α=0.05 下期望出现约 10 个假阳性。
- **改进也是变更**：只看变慢会漏掉"测试坏了/环境换了"这类反向信号。

## 原理详解

### 1. 为什么不能用 N vs N-1

官方文章的原话是"不能仅通过第 N 次和 N-1 次 Build 的结果就定位一个测试回归问题"。Skia Perf 那篇原始描述给出了三个理由：

- **单条测量几乎无意义**："a single benchmark measurement is virtually meaningless"——机器过热降频、其他进程抢 CPU、VM 邻居干扰都会污染单点。
- **手动设置上下界不可扩展**：Skia 每次提交产生约 40000 个测量（曾超过 70000），逐个维护上下界既费时又容易漏。
- **瞬时波动看起来像回归**：需要"多个结果形成的一致趋势"才能判定。

### 2. 分步拟合的判据

```text
对每个候选位置 i，取前后各 WIDTH 个结果:
    before = series[i-WIDTH : i],  after = series[i : i+WIDTH]
    delta  = (mean(after) - mean(before)) / mean(before)
    stderr = sqrt(var(before)/WIDTH + var(after)/WIDTH)
    z      = (mean(after) - mean(before)) / stderr
判定:  |delta| >= THRESHOLD  且  |z| > 2.0
```

两点必须强调：

- **"方差越小，越有信心检出细微回归"**——这正是 `z` 项的作用：抖动小的指标可以用更小的 THRESHOLD。
- 网上流传的伪码把**相对** `delta` 直接除以**绝对** `stderr`，量纲不一致（阈值会随指标量级漂移）。本 demo 用绝对差值除以 stderr。这是实现时最容易写错的一行。

相邻候选要**合并**（间距小于 WIDTH 的只保留第一个），否则一次阶跃会在窗口滑动过程中被报出多次。

### 3. 双判据回归判定

```text
Welch t:  t = (mean_current - mean_baseline) / sqrt(s_b²/n_b + s_c²/n_c)
          df = (v1+v2)² / [ v1²/(n_b-1) + v2²/(n_c-1) ],  v1 = s_b²/n_b, v2 = s_c²/n_c
          临界值: df > 30 -> 1.96;  否则查 t 表并按小数自由度线性插值
          缺标准差时按 5% 变异系数(CoV)兜底估计
分类表(两个条件都满足才判 FAIL):
    不显著                    -> no_significant_change  (测量噪声)
    显著 & |delta| < 10%      -> acceptable_change      (通过)
    显著 & delta <= -10%      -> improvement            (通过, 但要出现在看板上)
    显著 & delta >= +10%      -> REGRESSION             (失败)
```

### 4. 降噪优先于检测

Dropbox 的 Apogee 系统在能可靠检出回归之前，先做了一件事：**证明同一份代码重复跑的结果足够一致**。他们的结论是"测量噪声盖过了代码改动的影响，不先降低变异就什么都检不出来"。落到动作上：

- 统一平台与硬件（他们遇到 macOS/Linux/Windows 的 VM 类型与追踪能力都不同）；
- 用 CoV 量化重复性，把"可检测的最小回归幅度"与噪声绑定；
- 结果入库（时序库）+ 看板（Grafana），每条结果标注 commit hash / 作者 / 日志链接；
- 把性能测试挂到每条提交（或关键路径的提交前）+ 定期跑昂贵测试。

**噪声地板**：`threshold = max(10%, 3 × 历史 CoV)`。稳定指标的阈值就是 10%，抖动指标自动放宽（自检里 5.9% 的 CoV 把阈值抬到 17.7%）。

### 5. 多重比较

一次比较 `m` 个 benchmark，未校正时假阳性期望 `0.05m`。两种校正：

- **Bonferroni**：阈值收紧到 `α/m` —— 控制"至少一个假阳性"的概率，代价是检出率下降。
- **Benjamini-Hochberg**：按 p 值升序找最大的 `k` 使 `p(k) <= α·k/m`，控制错误发现率（FDR），比 Bonferroni 宽松。

自检实测：200 个无差异 benchmark → 未校正 13 个"显著"，Bonferroni 与 BH 均为 0。

## 对比 / 选型

| 方案 | 假阳性 | 检出延迟 | 适用 |
| --- | --- | --- | --- |
| 固定上下界 | 高（单点即触发） | 立即 | 只用来看大事故，不阻塞 CI |
| N vs N-1 + 幅度阈值 | 很高 | 立即 | ❌ 不推荐（纯噪声下 59 次比较里 10 次超 10%） |
| **分步拟合** | 低 | 需 WIDTH 个后续构建 | 每天多次提交、看板趋势 |
| **双判据（幅度+显著性）** | 可控 | 立即 | 提交前阻塞门禁（配合 `dryRunMode` 快速验证） |
| 多判据 + BH 校正 | 最低 | 立即 | 数百个 benchmark 同时把关 |

## 环境准备

- 操作系统：任意
- Python：3.10+；Go：1.21+
- 依赖：无

## 运行方式

### Python

```bash
python3 python/regression_gate.py
```

### Go

```bash
cd go && go run regression_gate.go
```

## 关键代码片段

```python
# step fit: 窗口前后比较(对应「原理详解」第 2 步)
for i in range(width, n - width + 1):
    before, after = series[i - width:i], series[i:i + width]
    mb, ma = statistics.fmean(before), statistics.fmean(after)
    delta = (ma - mb) / mb                                   # 相对变化
    stderr = math.sqrt(statistics.variance(before) / len(before)
                       + statistics.variance(after) / len(after))
    z = (ma - mb) / stderr                                   # 绝对差值 / stderr
    if abs(delta) >= threshold and abs(z) > z_min:           # 两个条件都要满足
        out.append({"index": i, "delta": delta, "z": z})

# 双判据(第 3 步): 只有"显著且幅度超阈值且变慢"才 FAIL
significant = abs(t) > t_critical(df)
if not significant:            verdict = "no_significant_change"
elif abs(delta) < mag_threshold: verdict = "acceptable_change"
elif delta < 0:                verdict = "improvement"
else:                          verdict = "REGRESSION"
```

## 性能与边界

- **检出延迟 = WIDTH 个构建**：阶跃至少需要 WIDTH 个后续构建才能被确认（WIDTH 越大越稳、越迟钝），这是稳定的代价。
- **WIDTH / THRESHOLD 的取舍**：官方用 WIDTH=5、THRESHOLD=25；降低阈值能抓更多回归，也会带来更多误报。
- **采样成本**：每个 benchmark 每次运行都要 `n ≥ 10`（理想 20）次重复，同一次比较 200 个 benchmark = 数千次测量，必须挑关键路径而非全量。
- **门槛值的自适应上限**：噪声地板取 `3σ` 后若超过真实回归幅度，会出现"永远不报警"；此时应该先降噪（锁频、专用机器），而不是继续放宽容差。
- **真实设备 vs 模拟器**：官方明确"在模拟器上跑基准是被强烈不推荐的"，因为结果取决于宿主机的 OS 与硬件能力。

## 注意事项与常见坑

- **只比较相邻两次构建**：纯噪声下 59 次比较就有 10 次超过 10%（自检实测），这类门禁很快会被团队忽略。
- **把"显著"当"回归"**：显著性只说明"差异不是噪声"，还需要幅度阈值（+0.4% 稳定变化是 acceptable 而非回归）。
- **忽略改进方向**：改进与回归同样要上报警——它可能意味着测试坏了、环境换了，或者比较的是不同版本的被测软件。
- **不做多重比较校正**：几百个 benchmark 一起看时，未校正的"显著"里大约 5% 是假的。
- **测量环境不锁**：微基准需要锁 CPU 频率（官方提供 `lockClocks` 脚本，仅对 root/userdebug 设备适用）；宏基准要在真机 + 可比设备池上跑。
- **基线存储方式**：只存均值会导致时没有方差可用（本 demo 用 5% CoV 兜底估计，但这只是补丁）；应存中位数与 p90/p99 等分位数。
- **一个可见的回报**：把结果存成时序 + 看板，能"让工程师快速检查是哪一段构建区间引入了阶跃"——单点断言给不出这个上下文。

## 参考资料（实际阅读过的权威来源）

- [Detecting Benchmark Regression — Joe Gregorio（Skia Perf 的算法作者）](https://bitworking.org/news/2014/11/detecting-benchmark-regressions/) — "单条基准测量几乎无意义"；手动上下界的三个缺陷；轨迹归一化（均值 0、标准差 1，且标准差过小必须设下限否则归一化只放大噪声）；k-means 聚类 + 最佳阶跃函数拟合；判据 `|step/fit|`（阶跃高度 / 均方拟合误差）；"无论方向，阶跃式变化都要找"（下降也可能是测试坏掉）
- [在 CI 中使用 Benchmark 进行回归分析 — Google Android Developers 官方博客（中文版）](https://blog.csdn.net/googledevs/article/details/107218035) — "不能仅通过第 N 次和第 N-1 次 Build 结果定位回归"；Jetpack CI 采用 Skia Perf 的分步拟合；WIDTH=5 与 THRESHOLD=25 的实测取值与二者对灵敏度/误报的取舍；回归与改进都要告警；CI 落地四步（写真机基准、收集 JSON 指标、宽度翻倍复核、发出告警）
- [Benchmark in Continuous Integration — Android Developers 官方文档](https://developer.android.com/studio/profile/benchmarking-in-ci) — 基准结果是"模糊的"而非 pass/fail、图形化才能看趋势；强烈建议真机（不建议模拟器）；拆分为 assemble / install / `am instrument` 阶段；`lockClocks` 锁频与 `dryRunMode` 用于 PR 验证；结果 JSON 与 trace 的存放位置
- [Regression Detection — Picasso 性能测试框架解读（Welch t 检验 + 双判据实现）](https://deepwiki.com/onecoolx/picasso/7.5-regression-detection) — 两个互相独立的判据（幅度 10% + 显著性 0.05）、Welch t 统计量与 Welch-Satterthwaite 自由度公式、df > 30 用正态临界值、简化临界值表、缺失标准差时按 5% CoV 兜底、四类分类表（新基线 / 无显著变化 / 可接受 / 改进 / 回归）
- [Keeping sync fast with automated performance regression detection — Dropbox Tech Blog（Apogee）](https://dropbox.tech/infrastructure/keeping-sync-fast-with-automated-performance-regression-detectio) — 先证明同码重复跑的一致性（CoV）再谈回归检测；跨平台/VM/磁盘/网络的变异来源；"优先人工可复现性而非真实世界保真度"的取舍；Kafka + InfluxDB + Grafana 的入库与看板管线；结果标注 commit hash / 作者 / 日志
- [How We Learned to Stop Guessing and Love Low P-Values — YugabyteDB](https://www.yugabyte.com/blog/how-we-learned-to-stop-guessing-and-love-low-p-values) — 用 Student's t 检验与中心极限定理构造两个样本的差异置信区间；"不应预测哪些改动会影响性能，而应定期检测"；每晚测量 + 关键路径提交前测量的组合；自评改动者的天然偏见
