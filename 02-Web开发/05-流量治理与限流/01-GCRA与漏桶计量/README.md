# GCRA 与漏桶计量

限流计量器（meter）的三种形态：时间桶、虚拟调度 GCRA、连续状态漏桶。核心结论是 **GCRA 与连续状态漏桶在数学上完全等价**，而 GCRA 只需一个标量 + 一只时钟，不需要任何后台「漏水」过程。

## 一、ITU-T I.371 的两种等价描述

GCRA（Generic Cell Rate Algorithm）由 ATM Forum 的 UNI 规范与 ITU-T 建议书 I.371《Traffic control and congestion control in B-ISDN》给出，原文同时给出两种等价描述。

**虚拟调度（virtual scheduling）**：维护「理论到达时间」TAT——假定信元以发射间隔 `T = 1/Λ` 等间距发送时的标称到达时刻。

- 一致判据：实际到达时刻不「过早」，即 `ta > TAT − τ`（I.371 原文用**严格大于**）。
- 非一致信元：**TAT 保持不变**（关键——拒绝不惩罚、不推迟）。
- 一致且 `ta < TAT`（桶还没空）：`TAT ← TAT + T`。
- 一致但 `ta ≥ TAT`（桶已漏空，中间有空档）：`TAT ← ta + T`，防止空档期累积信用。

后两条合并即工程实现里常见的 `TAT ← max(TAT, ta) + T`。

**连续状态漏桶（continuous-state leaky bucket）**：

- 桶内以 **1 单位水量/单位时间** 恒定漏水，故桶内水量与容限都以**时间**为单位。
- 一致信元使水量 +`T`，桶容量上界为 `T + τ`。
- 到达时先算 `X' = X − (ta − LCT)`（负值截 0），`X' ≤ τ` 则一致。

Wikipedia 明确提醒：GCRA 应被理解为 **leaky bucket as a meter**（计量器）而非 **as a queue**（队列）；它不做排队整形，只判「超不超」。

## 二、等价性推导

两者可用不变量 `TAT = LCT + X` 串起来：

- 判据：`X' ≤ τ` ⟺ `max(0, X − (ta − LCT)) ≤ τ` ⟺ `ta ≥ LCT + X − τ = TAT − τ`，与虚拟调度逐字相同。
- 更新：`TAT' = ta + max(0, X − (ta − LCT)) + T = max(TAT, ta) + T`，同样一致。

自检里用 300 组随机到达序列（随机速率 / 容限 / 间隔 / cost）逐事件对拍两者，判定与 `retry_after` 全部相同。

## 三、τ 的口径差异（一处真实的文献分歧）

| 出处 | 扣减缓冲 | 同一瞬间突发额度 |
| --- | --- | --- |
| ITU-T I.371 / Wikipedia | `τ` | `floor(τ/T) + 1` |
| Brandur《Rate Limiting, Cells, and GCRA》 | `τ + T` | `floor(τ/T) + 2` |

Brandur 文中写的是「减去代表突发总容量的固定缓冲 `(τ + T)`」，比 I.371 的 `τ` 多扣了一个发射间隔，等价于多发一张令牌。自检里 `rate=10, τ=0.4 (=4T)` 时两者分别是 **5** 与 **6**。实现时必须在 README 里写清采用哪一口径——本文与代码默认 I.371。

同理，边界 `ta == TAT − τ` 上：I.371 原文的严格大于判为**非一致**，工程实现普遍用 `>=` 判为**一致**。两条断言分别钉住。

## 四、为什么 GCRA 优于时间桶

时间桶（`FixedWindow`）在窗口边界会出现**双倍突发**：`limit=60, window=1s` 时，t=0.9s 打满 60 次、t=1.1s 又打满 60 次，0.2 秒内放行 120 次。GCRA 在同一条序列上 0.2 秒内只放行 2 次（`τ=0` 时每个时刻 1 次）。

GCRA 的另一条性质是**闲置不攒信用**：空转 1000 秒后同一瞬间的突发额度仍是 `floor(τ/T)+1`，不会因为「攒了很久」而暴涨——`max(TAT, ta)` 这一项挡住了它。

## 五、被拒不改状态

`非一致 → TAT 不变` 这一条有两种写法都可以跑，但语义差别很大：若实现成「被拒也推进 TAT」，客户端重试风暴会把 TAT 不断推向未来，形成惩罚性延长。自检里连续 50 次在 t=0.02s 被拒后，`retry_after` 恒为 0.08s、TAT 恒为 0.1s，钉住「拒绝不惩罚」。

## 六、代码结构

| 文件 | 内容 |
| --- | --- |
| `python/gcra.py` | `FixedWindow` / `Gcra`（含 `strict`、`buffer_mode` 两个开关）/ `ContinuousLeakyBucket` / `measure_throughput` |
| `python/selfcheck_gcra.py` | 25 条断言（实跑全绿） |
| `python/main.py` | 同一条到达序列上三种计量器的对照输出 |
| `go/gcra.go` | Go 侧同构实现（`NewGcra` / `NextAllowed` / `BurstCapacity` / `NewContinuousLeakyBucket`） |

## 参考资料

- Wikipedia, *Generic cell rate algorithm* — <https://en.wikipedia.org/wiki/Generic_cell_rate_algorithm>（转述 ITU-T I.371 的虚拟调度与连续状态漏桶两段原文，含「GCRA 是 meter 不是 queue」的说明）
- Brandur Leach, *Rate Limiting, Cells, and GCRA* — <https://brandur.org/rate-limiting>（TAT / 发射间隔 / `(τ+T)` 缓冲的口径，以及时间桶与漏桶缺点的对比）
- Wikipedia, *Leaky bucket* — <https://en.wikipedia.org/wiki/Leaky_bucket>（as a meter 与 as a queue 的区分）
