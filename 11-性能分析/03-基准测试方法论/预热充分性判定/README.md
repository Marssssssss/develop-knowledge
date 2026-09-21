# 预热充分性的程序化判定

> 待研究项落地：`预热充分性的判定：如何程序化确认 JIT / 内联缓存已进入稳态（而不是固定 warmup 次数）`。判据逐行来自 `psf/pyperf` 的 `pyperf/_worker.py`，对照组是 `openjdk/jmh` 的固定暖机次数（`Defaults.java`）。

## 简介

"预热跑几次"最常见的答案是"5 次"（JMH 默认值）或"10 次"（经验值）。这两种都是**没有判据**的：跑满次数就算数，至于有没有真的进入稳态，没人检查。

pyperf 给了一套真正可执行的判据——把暖机阶段采到的值对半分成 sample1 / sample2，比较两段的位置与离散度，五条不等式全过才算"够了"，否则 `nwarmup += 1` 重来。本 demo 把它逐行复刻出来，并拿 JMH 的固定 5 次做对照。

## 原理详解

### 1. 判据：两个半样本 + 五条不等式

```python
half = nwarmup + (len(self.warmups) - nwarmup) // 2
sample1 = self.warmups[nwarmup:half]
sample2 = self.warmups[half:]
```

即"跳掉前 nwarmup 个值之后，把剩下的**对半分**"。比较的五个量：

| 判据 | 定义 | 通过条件 |
| --- | --- | --- |
| 离群 | 首值 vs 其余值的 `q3 + 1.5·iqr` | `first_value <= outlier_max` |
| 均值漂移 | `(mean1 − mean2) / mean2` | **`-0.5 ≤ x ≤ 0.10`** |
| 离散度 | `(mad1 − mad2) / mad2` | `abs ≤ 0.10` |
| 下四分位 | `(q1_1 − q1_2) / q1_2` | `abs ≤ 0.05` |
| 上四分位 | `(q3_1 − q3_2) / q3_2` | `abs ≤ 0.05` |

### 2. 三个容易读错的细节

**离群检验把首值排除在分布之外。** `values = sample1[1:] + sample2`——先算"除首值以外"的 q1/q3/iqr，再拿首值跟 `q3 + 1.5·iqr` 比。后果是当所有值都相等时 `iqr = 0`，**容差为零**：首值哪怕只高 `0.0001` 也会被判离群（自检 E5a）。另外它**只查最大值**——首值比别人低一半不算离群（E5b），因为"变快"不是预热的症状。

**均值漂移的区间是非对称的。** `-0.5 ≤ mean_diff ≤ 0.10`：第一段比第二段**慢**最多只能慢 10%，**快**却可以快到 50%。自检 E6 用同一个基础样本做了四组：

- 慢 11% → `mean_diff = +0.11`，被 mean 这条拦下
- 慢 4% → 五条全过
- 快 40% → `mean_diff = −0.4`，**落在区间内**，mean 这条放行，最终是 `q1_diff` / `q3_diff` / `mad_diff` 三条拦下的
- 快到 60% → 才轮到 mean 这条拦

**离散度用 MAD 不用 stdev。** `stdev` 在源码里只在 `--verbose` 下打印，不参与判定。MAD（中位数绝对偏差）对单点离群免疫：给 20 个数中的某一个加 500，MAD 纹丝不动而 stdev 放大 77 倍（自检 E3）。

### 3. 校准循环：只补算缺的值

```python
total = nwarmup + WARMUP_SAMPLE_SIZE * 2      # 20 * 2
nvalue = total - len(self.warmups)
if nvalue: self._compute_values(self.warmups, nvalue, is_warmup=True, start=start)
if self.test_calibrate_warmups(nwarmup, unit): break
if len(self.warmups) >= MAX_WARMUP_VALUES:    # 300
    sys.exit(1)
nwarmup += 1
```

因此采样总数恒等于 `nwarmup + 40`（自检 E7b），每次失败只多补一个值而不是重跑。`MAX_WARMUP_VALUES = 300` 是硬顶，到顶即报 "failed to calibrate" 退出（自检 E9）。

### 4. JMH 侧：没有判据，只有常量

`Defaults.java` 里 `WARMUP_ITERATIONS = 5`、`WARMUP_ITERATIONS_SINGLESHOT = 0`、`WARMUP_TIME = 10s`、`WARMUP_FORKS = 0`、`WARMUP_MODE = INDI`；`@Warmup` 注解的 `BLANK_ITERATIONS = -1` 只是"未指定"哨兵，最终落到 Defaults。**没有任何一处检查是否真的热了。**

在 demo 的升温曲线（`steady=100`、初始开销 `900`、每轮衰减 0.7、叠加 ±1 的确定性抖动）上：

| 策略 | 暖机次数 | 最后一次相对稳态的偏差 |
| --- | --- | --- |
| JMH 默认固定值 | 5 | **+216%**（还在升温段） |
| pyperf 校准 | 17 | +2.1%（判据通过） |

同一条曲线，"跑满 5 次"和"校准出 17 次"差了两个数量级的偏差。反过来，如果代码本来就不需要预热（无升温、无噪声），校准只给 1 次，而 JMH 仍固定跑 5 次——**固定次数的代价两头都有**。

### 5. 一个真实的副作用：纯噪声也会把 nwarmup 推大

把升温项去掉、只留 ±1 的噪声时，校准给出的不是 1 而是 **24**（自检 E8c）。原因是 `mad_diff` 的 10% 容差在每侧 20 个样本上本身就不稳——MAD 的抽样误差远大于 10%。所以"校准出的 nwarmup 很大"并不总意味着真的需要那么久预热，也可能是判据在噪声上抖动。这是使用该判据时值得记住的边界。

### 6. 官方代码里的一处未处理分支

```python
mad_diff = (mad1 - mad2) / float(mad2)   # FIXME: handle division by zero
```

样本完全恒定时 `mad2 = 0`，官方会抛 `ZeroDivisionError`。demo 里按 `0.0` 处理（视为"离散度一致"）并在自检中明确标注，避免把这条已知缺陷当成"通过"。

## 环境依赖

- Python ≥ 3.9（仅标准库）；Go ≥ 1.21（`go run .`）

## 运行方式

```bash
cd 11-性能分析/03-基准测试方法论/预热充分性判定
python python/main.py                # 冒烟：校准结果 vs JMH 固定 5 次
python python/selfcheck_warmup.py    # 35 条断言，全绿
cd go && go run .                    # Go 版同判据
```

## 关键代码

| 文件 | 职责 |
| --- | --- |
| `python/main.py` | `percentile` / `median_abs_dev` / `warmup_diagnostics`（五条判据摊平） / `test_calibrate_warmups` / `calibrate_warmups` / `jmh_warmup_plan` / 确定性 LCG 与 `jit_curve` |
| `python/selfcheck_warmup.py` | 35 条断言：插值、MAD 免疫性、离群方向、非对称区间、长度恒等式、300 上限、JMH 常量 |
| `go/warmup.go` | 同判据的 Go 版（255 行），含 `Diagnostics.Failed` 原因列表 |

## 性能边界与注意事项

- **判据保证的是"这两段看起来一致"，不是"JIT 已经编译完"**。它测不到代码路径分支预测、内联缓存的长期演化。
- **不要只看校准出的 nwarmup 大小**：纯噪声场景会给出 24（见 §5）。
- **`--calibrate-warmups` 必须先给 `--loops=N`**，源码里 `loops < 1` 直接报 CLIError；`--recalibrate-warmups` 还要求 `--warmups=N`。
- **pyperf 默认 warmups = 1**（非 JIT 且不在 worker 里），`--debug-single-value` 直接置 0；默认值与校准值是两回事。
- **JMH 的 `WARMUP_FORKS = 0`** 意味着默认不做专门的暖机 fork，暖机与测量在同一个 fork 内完成。

## 参考资料（实际阅读过的来源）

- [`psf/pyperf` — `pyperf/_worker.py`](https://github.com/psf/pyperf/blob/main/pyperf/_worker.py) — `MAX_WARMUP_VALUES=300`、`WARMUP_SAMPLE_SIZE=20`、`test_calibrate_warmups` 的五条不等式与"只查最大值"的离群检验、`calibrate_warmups` 的递增循环与 300 上限、`mad_diff` 的除零 FIXME
- [`psf/pyperf` — `pyperf/_utils.py`](https://github.com/psf/pyperf/blob/main/pyperf/_utils.py) — `percentile` 的线性插值写法、`median_abs_dev` 的定义
- [`psf/pyperf` — `pyperf/_runner.py`](https://github.com/psf/pyperf/blob/main/pyperf/_runner.py) — 默认 `warmups = 1`、`--debug-single-value` 置 0、`--calibrate-warmups` 与 `--recalibrate-warmups` 的前置条件
- [`openjdk/jmh` — `jmh-core/src/main/java/org/openjdk/jmh/runner/Defaults.java`](https://github.com/openjdk/jmh/blob/master/jmh-core/src/main/java/org/openjdk/jmh/runner/Defaults.java) — `WARMUP_ITERATIONS=5`、`WARMUP_ITERATIONS_SINGLESHOT=0`、`WARMUP_TIME=10s`、`WARMUP_FORKS=0`、`WARMUP_MODE=INDI`
- [`openjdk/jmh` — `jmh-core/src/main/java/org/openjdk/jmh/annotations/Warmup.java`](https://github.com/openjdk/jmh/blob/master/jmh-core/src/main/java/org/openjdk/jmh/annotations/Warmup.java) — `BLANK_ITERATIONS/BLANK_TIME/BLANK_BATCHSIZE = -1` 哨兵语义
