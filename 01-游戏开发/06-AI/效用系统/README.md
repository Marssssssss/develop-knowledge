# Utility AI：考虑项的评分、合成与选择

> 新子领域 `01-游戏开发/06-AI/效用系统`，同时落地原 `06-AI/README.md` 待研究项 **Utility AI 评分系统**。Utility AI 与行为树的差别是：行为树在每个 tick 走一条**写死的分支**，Utility AI 是给每个候选行为算一个 **0..1 的分数**，再按规则挑一个。本 demo 把 `big-brain`（Bevy 生态的 Utility AI 库，`zkat/big-brain`）的评分链路逐行转写成 Python 与 Go。

## 简介

big-brain 的链路是三层：

```
原始数值 --Evaluator--> 0..1 的 Score --Scorer(合成)--> 0..1 的 Score --Picker--> 一个 Choice --> Action
```

- **Evaluator** 把「血量 37 / 距离 12 米」这类原始量映射成 0..1（线性 / 幂 / S 形）；
- **Scorer** 把若干个 Score 合成一个（求和 / 求积 / 全有或全无 / 取最高 / 加权测度）；
- **Picker** 在所有 Choice 之间做仲裁（第一个过线 / 最高分 / 过线里的最高分）。

三层都有**非对称的阈值判据**，这是最容易写错的地方：

| 位置 | 判据 | 相等时 |
| --- | --- | --- |
| `AllOrNothing` 单个孩子 | `score < threshold` → 归零 | **放行** |
| `SumOfScorers` 总和 | `sum < threshold` → 归零 | 放行 |
| `WinningScorer` 最大值 | `s.get() < threshold` → 0 | 放行 |
| `FirstToScore` | `value >= threshold` | **放行** |
| `HighestToScore` | `score <= threshold` → 跳过 | **不放行** |
| `Highest` | `score <= max \|\| score <= 0.0` → 跳过 | 并列取**第一个**，0 分永不获胜 |

`FirstToScore` 与 `HighestToScore` 对「恰好等于阈值」的处理是**相反**的。

## 原理详解

### 1. Score：值域就是契约

```rust
pub fn set(&mut self, value: f32) {
    if !(0.0..=1.0).contains(&value) { panic!("Score value must be between 0.0 and 1.0"); }
    self.0 = value;
}
```

`Score::set` 越界直接 `panic!`；`set_unchecked` 允许越界，但官方注释明确写了「**Scorer 会显著更难组合，除非没法 rescale 否则不要用**」。所有复合 Scorer 在写入前都会 `clamp` 一次，所以正常路径不会 panic。

### 2. Evaluator：三条曲线

- **LinearEvaluator**：`ya + dy_over_dx * (value - xa)`，然后 `clamp(.., ya, yb)`。`new()` 是恒等；`new_ranged(min,max)` 把 `[min,max]` 映射到 `[0,1]`；**`new_inversed()` 是 `new_ranged(1.0, 0.0)`**（xa=1、xb=0 ⇒ 斜率 -1），不是「1 减去」。
- **PowerEvaluator**：`dy * ((cx-xa)/(xb-xa))^power + ya`，`power` 被 clamp 到 `0..=10000`，默认 2。
- **SigmoidEvaluator**：`k` 被 clamp 到 `(-0.99999, 0.99999)`，**默认 k = -0.5**：

  ```
  two_over_dx = |2 / (xb - ya)|     // 注意分母是 xb - ya
  d           = clamp(x, xa, xb) - x_mean
  numerator   = two_over_dx * d * (1 - k)
  denominator = k * |1 - 2 * (two_over_dx * d)| + 1
  f(x)        = clamp(dy_over_two * (numerator / denominator) + y_mean, ya, yb)
  ```

  实测（默认 k = -0.5）：

  | x | 0 | 0.25 | 0.5 | 0.75 | 1 |
  | --- | --- | --- | --- | --- | --- |
  | f(x) | **1.0** | **0.0** | 0.5 | 0.875 | 1.0 |

  它不是一条 S 形：在 `d = -0.25` 处分母恰好为 0，Rust 的 `f32` 给出 `-inf`，随后被 `clamp` 成 0；两端又因为 `clamp` 回到 1。**默认参数下的 SigmoidEvaluator 是一条被截断的 V 形**。想拿到单调的 S 曲线请把 `k` 取到 0 附近（`k = 0` 时它退化成线性）。

### 3. Measure：四种合成分量

| Measure | 公式 | 备注 |
| --- | --- | --- |
| `WeightedSum` | `Σ score_i * w_i` | 可以超过 1，靠上层 clamp |
| `WeightedProduct` | `fold(0.0, acc * s * w)` | **初值是 0.0 ⇒ 结果恒为 0** |
| `ChebyshevDistance` | `max(s_i * w_i)` | 初值 0 ⇒ 负分与负权重不会赢 |
| `WeightedMeasure`（默认） | `sqrt(Σ (w_i / Σw) * s_i²)`，权重和为 0 时返回 0 | 二次平均，恒 ≥ 算术平均 |

`WeightedProduct` 恒为 0 是**源码里的既有行为**（`scores.iter().fold(0f32, ...)` 的初值写成了 0 而不是 1）：本 demo 原样记录并在自检里断言，不修改它。

### 4. 复合 Scorer 的阈值语义

- `AllOrNothing(t)`：逐个检查，**只要有一个孩子 `< t` 就立即 `sum = 0; break`**；否则求和并 clamp 到 1。它是「AND」语义，但输出是「和」而不是布尔。
- `SumOfScorers(t)`：**先求和再比阈值**（`sum < t → 0`），所以它比 `AllOrNothing` 宽松：`[0.3, 0.3]` 在 `t = 0.8` 下被归零，而 `[0.5, 0.4]` 保留 0.9。
- `ProductOfScorers(t, use_compensation)`：
  ```
  mod_factor = 1 - 1/n
  makeup     = (1 - product) * mod_factor
  product   += makeup * product        // 仅当 product < 1.0
  ```
  补偿让「n 个中等分数」不至于被连乘压死：`[0.5, 0.5]` 从 0.25 抬到 **0.34375**。注意补偿条件是 `product < 1.0`，`[1.0, 1.0]` 不触发。
- `WinningScorer(t)`：取最大值，最大值 `< t` 时给 0。
- `MeasuredScorer`：把 `(Score, weight)` 列表交给 Measure，低于阈值给 0，**否则 clamp 到 1**（所以 `WeightedSum` 的 1.5 会变成 1.0）。

### 5. Thinker：每 tick 重新仲裁

`thinker_system` 在 `ActionState::Executing` 分支里**每个 tick 都调一次 `picker.pick()`**，注释写得很直白："We do this every tick, because we might change our mind."。抓不到任何 Choice 时依次退到 `scheduled_actions` 队列，再退到 `otherwise`（默认动作）。

## 对比：Utility AI vs 行为树 vs GOAP

| 维度 | 行为树 | GOAP | Utility AI |
| --- | --- | --- | --- |
| 决策依据 | 节点状态路由 | 搜索出的动作序列 | 连续分数 |
| 行为切换 | 抢占（halt 兄弟） | 重规划 | 分数反超即切换（ hysteresis 要自己做） |
| 可调参数 | 树结构 | 前提/效果/代价 | 曲线 + 权重 + 阈值 |
| 抖动风险 | 低 | 中 | **高**（分数接近时会来回横跳） |

## 环境

- Python 3.12+（仅标准库）
- Go 1.21+（无第三方依赖）；本机无 Go 工具链时走人工审查 + `bracket_check.py` / `go_sanity.py`

## 运行方式

```bash
cd python && python selfcheck_util.py    # 58 条断言，输出 PASS = 58
cd go     && go run .                     # 打印三条曲线与四类合成的结果
```

## 关键代码

Python：Sigmoid 的除零是**必须显式模拟**的语言差异（Rust `f32` 给 `inf`，Python 会抛异常）

```python
def _fdiv(num, den):
    if den == 0.0:
        if num == 0.0:
            return float("nan")
        return math.copysign(float("inf"), num) * math.copysign(1.0, den)
    return num / den
```

Go：同一处靠「变量参与运算」拿到 ±Inf（Go 不允许常量除零）

```go
denominator := s.k*math.Abs(1.0-2.0*(s.twoOverDx*d)) + 1.0
return clamp(s.dyOverTwo*(numerator/denominator)+s.yMean, s.ya, s.yb)
```

## 性能边界

- 每个 Scorer 都是一个 Bevy system，一个实体一棵 Scorer 树 ⇒ 开销随「Choice 数 × 考虑项数」线性增长，且每 tick 全量重算（没有脏标记）。
- `Highest` / `HighestToScore` 都是 O(n) 单次扫描，但如果 `Choice::calculate` 里做了重活，它会被**同一个 tick 内的多个 Picker 反复调用**（`FirstToScore` 逐项算、`Highest` 又重算一遍）。
- `ProductOfScorers` 的连乘在考虑项很多时会趋近 0，补偿因子 `1 - 1/n` 只能部分抵消；超过 5~6 个考虑项时建议换 `WeightedMeasure`。

## 注意事项与常见坑

1. **默认 `SigmoidEvaluator` 不是 S 形**：`k = -0.5` 下 `f(0) = 1`、`f(0.25) = 0`（除零后 clamp）、`f(0.5) = 0.5`。要么换 `k`，要么换 `LinearEvaluator`。
2. **`two_over_dx` 的公式用了 `ya` 而不是 `xa`**（`(2.0 / (xb - ya)).abs()`）。公开构造 `new` / `new_ranged` 都传 `ya = 0`，所以暂时不可达；一旦自己调 `new_full` 传非零 `ya`，曲线会整体变形。
3. **`WeightedProduct` 恒返回 0**，别指望它做「连乘型考虑项」，用 `ProductOfScorers` 代替。
4. **阈值判据不对称**：`FirstToScore` 用 `>=`、`HighestToScore` 用 `>`，同一个 0.8 的分数在两个 Picker 下命运不同。
5. **`Highest` 的并列取第一个且 0 分永不获胜**：全 0 的 Choice 列表会返回 `None` 并落到 `otherwise`，而不是随便挑一个。
6. **求和型合成会被 clamp 吃掉信息**：`[0.9, 0.6]` 的 `WeightedSum` 是 1.5，写回 `Score` 时被夹成 1.0，于是「很渴」和「渴到极点」在下游看来是一样的。
7. `Score::set` 越界 panic：自写 Scorer 时务必先 clamp，别指望 `set_unchecked` 之后下游还能正确组合。

## 参考资料

实际读过并逐行对照的源码（zkat/big-brain，main 分支）：

- `src/scorers.rs` — `Score`（`set` 的 panic 与 `set_unchecked` 的警告）、`all_or_nothing_system`、`sum_of_scorers_system`、`product_of_scorers_system`（含补偿公式与 GDC vault 引用）、`winning_scorer_system`、`measured_scorers_system`
- `src/measures.rs` — `WeightedSum` / `WeightedProduct` / `ChebyshevDistance` / `WeightedMeasure`
- `src/evaluators.rs` — `LinearEvaluator` / `PowerEvaluator` / `SigmoidEvaluator` / `clamp`
- `src/pickers.rs` — `FirstToScore` / `Highest` / `HighestToScore`
- `src/choices.rs` — `Choice::calculate`
- `src/thinker.rs` — `thinker_system` 中「every tick, because we might change our mind」与 `otherwise` 兜底
