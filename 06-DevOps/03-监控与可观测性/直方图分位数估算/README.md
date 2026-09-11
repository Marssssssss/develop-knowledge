# 直方分位数估算 —— histogram_quantile 插值算法

## 简介

- Prometheus 的 classic histogram 把延迟等观测值分到**累积桶**（`le` = 桶上界），服务端用 `histogram_quantile(φ, rate(..._bucket[5m]))` 从桶计数**估算**分位数。
- 关键概念：
  - **累积桶**：`le="0.3"` 的计数包含所有 ≤0.3 的观测；`le="+Inf"` 桶 = 总观测数 `_count`
  - **线性插值**：分位数落在桶 (lower, upper] 内时，**假设桶内观测均匀分布**，在桶内线性插值
  - **summary 不可聚合**：预计算分位数做 avg 在统计上无意义（官方文档标 `BAD!`），histogram 可以跨实例 `sum by (le)` 后再取分位数（`GOOD`）
  - **误差 = 桶宽**：最坏情况估计值在桶一端而真值在另一端，最大误差就是整个桶宽
- 本 demo 用三语言实现 Prometheus 的插值算法与全部边界规则，并复现官方文档的误差分析算例。

## 原理详解

### 算法分步（依据 prometheus.io querying/functions 官方语义）

输入：φ（分位数）、按 le 升序的累积桶计数序列。设 `rank = φ × total`（total = +Inf 桶计数）。

1. **越界与退化**：φ<0 → `-Inf`；φ>1 → `+Inf`；φ=NaN → NaN；桶数 <2 或最高桶上界非 `+Inf` → NaN。
2. **单调修复**：浮点误差或坏数据可能使累积计数非单调；官方先忽略"相对差 < 1e-12 × 两桶之和"的微小差异，再把非单调桶**强制抬升为前桶值**（并打 annotation 提示数据有问题）。
3. **定位桶**：找**第一个** `count ≥ rank` 的桶 b。
4. **最高桶特例**：b 是 +Inf 桶 → 直接返回**次高桶的上界**（不做外插）。
5. **最低桶下界**：若 b 是最低桶且其上界 >0，下界取 0；否则下界 = 前一桶上界。
6. **线性插值**：
   `result = lower + (upper − lower) × (rank − prevCount) / (count − prevCount)`

```text
cum
count ┤                      ┌──── b: count ≥ rank 落在此桶
      │                 ┌────┘
      │            ┌────┘  ← prevCount
      │       ┌────┘
      │  ┌────┘
      └──┴─────┴────┴────┴────┴───→ le
        0    0.1   0.2   0.3  +Inf
        lower=0.2  upper=0.3
result = lower + (upper-lower) * (rank-prevCount)/(count-prevCount)
```

### 官方算例复现（practices/histograms 文档）

SLO：95% 请求 ≤300ms。真实延迟集中在 220ms（尖峰），桶配 `{le=0.1, 0.2, 0.3, 0.45}`：

- 全部观测落入 (0.2, 0.3] 桶 → p95 估算 = `0.2 + 0.1 × 0.95 = 0.295`（295ms）
- 真值 220ms，**误差 75ms**，且给出"逼近 SLO"的错觉（实际余量充足）
- 若所有延迟 +100ms（尖峰移到 320ms）→ 落入 (0.3, 0.45] 桶，估算骤变，误差更大——**桶边界附近的估算值不连续**。

### classic vs native histogram 对比（同 220ms 尖峰）

| 方案 | 分辨率 | p95 估算 | 误差 |
| --- | --- | --- | --- |
| classic 桶 {0.1,0.2,0.3,0.45} | 固定边界 | 295ms | 最大 100ms（桶宽） |
| native（factor 1.1） | 指数桶 210~229ms | 228ms | ≤19ms（相对 10%） |

native histogram 用指数桶 + 指数插值（假设提高分辨率后两半桶观测数相当），误差与桶宽成正比、与分辨率成反比。

## 环境准备

- 操作系统：任意（纯计算，无系统依赖）
- 语言版本：C99 / Python 3.8+ / Go 1.20+
- 依赖：无

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra -o quantile_demo c/quantile_demo.c && ./quantile_demo
```

### Python

```bash
python3 python/quantile_demo.py
```

### Go

```bash
cd go && go run quantile_demo.go
```

## 关键代码片段（Python 版）

```python
def histogram_quantile(phi, les, counts):
    """Prometheus 语义: 累积桶 + 桶内线性插值 + 官方边界规则。"""
    if math.isnan(phi): return math.nan
    if phi < 0: return -math.inf
    if phi > 1: return math.inf
    if len(les) < 2 or les[-1] != math.inf: return math.nan  # 桶数<2 或缺 +Inf
    counts = repair_monotonic(counts)                         # 浮点误差修复
    rank = phi * counts[-1]
    for i, c in enumerate(counts):
        if c >= rank:                                          # 第一个容纳 rank 的桶
            if math.isinf(les[i]): return les[i - 1]          # 最高桶 → 次高上界
            lower = 0.0 if i == 0 and les[i] > 0 else (les[i - 1] if i > 0 else les[i])
            prev = counts[i - 1] if i > 0 else 0.0
            if c == prev: return lower                          # 空桶防除零
            return lower + (les[i] - lower) * (rank - prev) / (c - prev)
    return math.nan
```

## 性能与边界

- 查询侧复杂度 O(桶数)（桶已按 le 排序时线性扫描；二分可到 O(log n)），桶数典型 ≤ 15。
- 存储侧：classic histogram 每个桶是一条独立时间序列——**桶数 × 标签基数** 是主要成本；native histogram 把整桶存进单一样本。
- `histogram_quantile(0, v)` / `histogram_quantile(1, v)` 分别得估算最小/最大值（官方文档明示用法）。
- 聚合必须 `sum by (le)`（le 是分组键）；native histogram 聚合无需 by 子句。

## 注意事项与常见坑

1. **avg(分位数) 是统计错误**：`avg(http_request_duration_seconds{quantile="0.95"})` 官方文档直接标 `BAD!`；正确做法 `histogram_quantile(0.95, sum by (le) (rate(..._bucket[5m])))`。
2. **桶边界选择比算法重要**：误差上限 = 桶宽；围绕 SLO 阈值（如 300ms）密集布桶，而不是均匀布桶。
3. **估算值在桶边界处不连续**：分布尖峰跨过桶边界时，估算值会跳变（官方 320ms 例子：估算从 295ms 跳到约 438ms）。
4. **+Inf 桶缺失 → NaN**：数据不完整时 `histogram_quantile` 直接返回 NaN，这是查数问题不是算法 bug。
5. **`rate()` 先于聚合**：必须先对每个桶序列算 rate（counter 语义），再 `sum by (le)`，再 quantile；顺序错了会把计数器当瞬时值。
6. **非单调累积计数**：官方阈值 1e-12（相对两桶之和），修复后仍非单调会被强制抬升并打 annotation——见到该 annotation 应排查数据源。
7. **最低桶下界假设为 0**（上界 >0 时）：若真有负观测（如温度），该假设引入偏差；native histogram 对含零/负值的 NHCB 最低桶假定下界为 -Inf。

## 参考资料（实际阅读过的权威来源）

- [Query functions — prometheus.io 官方文档](https://prometheus.io/docs/querying/functions/) — histogram_quantile 全部规则（插值假设、φ 越界、<2 桶 NaN、+Inf 必需、最高桶取次高上界、最低桶下界 0、单调修复 1e-12、sum by (le) 用法）
- [Histograms and summaries — prometheus.io practices](https://prometheus.io/docs/practices/histograms) — 220ms 尖峰误差分析算例、classic vs native 误差对比、summary 不可聚合（BAD!/GOOD 示例）
- [Native Histograms — prometheus.io specs](https://prometheus.io/docs/specs/native_histograms) — 线性 vs 指数插值、零桶插值特例、bucket 边界计算与最大误差分析
- [Exposition formats — prometheus.io](https://prometheus.io/docs/instrumenting/exposition_formats/) — `_bucket{le=...}` 累积桶文本展开约定（与 demo 051 衔接）
