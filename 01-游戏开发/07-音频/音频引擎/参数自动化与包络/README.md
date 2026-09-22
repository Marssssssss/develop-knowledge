# 音频引擎的参数自动化与包络（W3C AudioParam 语义）

## 一句话

游戏音频引擎（FMOD/Wwise/Web Audio 同构）里「音量淡入、滤波扫频、ADSR 包络」都不是逐帧写值，而是往参数上**排事件**；渲染时按事件表求值。这套事件表的语义在 W3C《Web Audio API》§1.6.2 里有完整、可逐条断言的定义，本 demo 把它落成代码。

## 一、五类事件与取值公式

| 方法 | 事件 | 取值 |
| --- | --- | --- |
| `setValueAtTime(v, T)` | `SetValue` | `v(t≥T) = v` |
| `linearRampToValueAtTime(v, T1)` | `LinearRampToValue` | `V0 + (V1−V0)·(t−T0)/(T1−T0)` |
| `exponentialRampToValueAtTime(v, T1)` | `ExponentialRampToValue` | `V0·(V1/V0)^((t−T0)/(T1−T0))` |
| `setTargetAtTime(v, T0, τ)` | `SetTarget` | `V1 + (V0−V1)·e^(−(t−T0)/τ)` |
| `setValueCurveAtTime(vals, T0, TD)` | `SetValueCurve` | `k = ⌊(N−1)/TD·(t−T0)⌋`，在 `V[k]` 与 `V[k+1]` 间线性插值 |

规范明确：**自动化事件时间不按采样率量化**，公式直接用给定的数值时刻。

## 二、四条最容易写错的规则

1. **斜坡的区间属于斜坡自己**：`t` 落在 `[T0, T1)` 内时要由斜坡求值，而不是由它前面那个 `SetValue` 求值。本 demo 首版正是这里写错（`0→4` 的中点算成 0），修好方式是「先找第一个晚于 `t` 的事件，若它是斜坡/曲线就由它求值」。
2. **无前序事件时斜坡从 `currentTime` 起算**：规范规定等价于先插入 `setValueAtTime(当前值, currentTime)`。
3. **指数斜坡到不了 0**：`value == 0` 直接抛 `RangeError`；`V0 = 0` 或 `V0`、`V1` 异号时整段 `v(t) = V0`。规范建议用 `setTargetAtTime` 替代 —— 这正是 ADSR 释音段的标准写法。
4. **时间参数为负抛 `RangeError`**，小于 `currentTime` 时**钳到 `currentTime`**（不是报错）。

## 三、曲线的区间互斥

- 若 `(T0, T0+TD)` 内已有任何事件 → `NotSupportedError`；
- 曲线排好后，再往 `[T0, T0+TD)` 内插事件 → `NotSupportedError`；
- 恰好落在曲线**端点**同刻的事件是允许的（规范原文：*"it's ok to schedule a value curve exactly at the time of another event"*）；
- 曲线结束后会**隐式**插入 `setValueAtTime(V[N−1], T0+TD)`，后续自动化从末值接着走。

`k = ⌊(N−1)·(t−T0)/TD⌋` 的边界：本 demo 用 `[0,1,2,3]` 的四点曲线断言 `t=1/6 → 0.5`、`t=0.5 → 1.5`、`t=T0+TD → 3`。

## 四、两种取消的分歧（很关键）

- `cancelScheduledValues(tc)`：删掉**时刻 ≥ tc** 的事件。它**不保留** tc 时刻的值，参数会回落到前一个事件的值 —— 取消一段衰减中的 ADSR，音量会**跳回起音峰值**。
- `cancelAndHoldAtTime(tc)`：删掉**时刻 > tc** 的事件，并让参数在 tc 之后保持 tc 时刻的值（跨越 tc 的曲线被截断，且规范要求截断后的输出与未截断时一致）。

实测对照（ADSR 在 `t=0.2` 时值为 `0.75`）：`cancel` 后 `t=0.9` 读到 `1.000`（跳回峰值），`hold` 后读到 `0.750`。

## 五、a-rate 与 k-rate

`AudioParam` 的自动化速率决定渲染粒度：`a-rate` **逐样本**求值（斜坡在块内连续变化），`k-rate` 整块只取**块首**一个值。自检用「1 秒 0→1 斜坡 + 4 采样块」对照：`a-rate` 得 `[0, 0.25, 0.5, 0.75]`，`k-rate` 得全 `0`。

## 六、运行

```bash
cd python && python selfcheck_param.py   # 51 断言全绿
python main.py
```

Go 侧（`go/param.go`）为同口径移植，`ValueAt` 的事件选择、`guard` 的区间互斥、两种取消与 `RenderBlock` 与 Python 一一对应；本机无 Go 工具链，走人工审查 + 括号/结构静态检查。

## 参考资料（本 README 实读）

- W3C《Web Audio API 1.1》§1.6.2 AudioParam Automation（事件表插入规则、五类公式、`NotSupportedError` 条件、`cancelScheduledValues` / `cancelAndHoldAtTime` 算法、`AutomationRate` 枚举）：`https://www.w3.org/TR/webaudio-1.1/`
