# NOTES — 时间序列因果特征(demo 516)完整注意事项与口径差异

> README 受 200 行限制,这里放完整版。README 是入口,这里是明细。

## 一、pandas 对齐的 5 个易错点

1. **`rolling_mean(x, w)` 含当期,`.shift(1)` 才把右端推到 `t-1`。** 忘了 `shift(1)` 是最常见的
   对齐错误;但它**未必**是泄漏 —— 取决于标签时点。别一概删掉,先看支持集。
2. **`center=True` 是偏左的。** 它只把窗口末端右移 `(window - 1) // 2`,取整向下;所以偶数窗口
   `window=4` 时标签 `i` 覆盖 `[i-2, i+1]`,不是对称的 `[i-2, i+2]`。
3. **`std` 默认 `ddof=1`**(样本标准差)。`window=1` 且 `ddof=1` 时分母为 0,结果是**缺失而不是 0**。
   自检里成对断言了这一对分支(`ddof=1 → None`,`ddof=0 → 0.0`),否则"写死 ddof=0"也能蒙过去。
4. **`min_periods` 默认等于 `window`**(整数窗口)。`min_periods=0` 时 pandas 仍对**空窗口**求值:
   `sum` 得 0.0,`mean` / `min` / `max` 得 NaN —— 这个分支只有 `closed='neither'` 配 `window=1`
   才摸得到,很容易漏测。
5. **`closed` 四个取值只挪 ≤1 格**:基准 `[start, end]`;`left` → `[start-1, end-1]`;
   `both` → `[start-1, end]`(窗口宽 `w+1`);`neither` → `[start, end-1]`(窗口宽 `w-1`)。
   注意 `closed='neither'` 时窗口宽度比 `w` 小 1,若 `min_periods` 仍取默认的 `w`,**整列都是缺失**
   —— 这是排查"为什么全是 NaN"时的一个常见坑。

## 二、sklearn `TimeSeriesSplit` 的 4 个口径

6. **`split()` 是生成器。** 两条硬校验写在函数体里,**不迭代就一行都不执行**;只调用 `split()`
   拿不到任何异常。本目录自检的 `raises()` 辅助函数必须把生成器跑干 —— 这一点踩过。
7. **`gap` 不动测试集**,只砍训练集末端(官方原文:Number of samples to exclude from the end of
   each train set **before** the test set)。把它当成"把测试集往后推"会得出完全错误的结论。
8. **`test_size` 默认 `n_samples // (n_splits + 1)`**,官方说明它"是 `gap=0` 时允许的最大值"。
   notes 里的训练集大小公式 `i * n_samples // (n_splits + 1) + n_samples % (n_splits + 1)` 中,
   `i` 是 **1-based** —— 若按 0-based 读,`i=0` 会算出空训练集,与该 docstring 自己的示例矛盾。
9. **`n_splits >= 2` 的校验在父类 `_BaseKFold.__init__`,属构造期**,不在 `TimeSeriesSplit.split`
   里。本 demo 的 `splits.py` 是独立类(不继承),所以**不做**该检查,自检里能跑 `n_splits=1`
   的边界用例 —— 这是口径差异,不是缺陷。
10. **`max_train_size` 只在严格小于 `train_end` 时才截断**(源码 `if self.max_train_size and
    self.max_train_size < train_end`),等于 `train_end` 时不截。

## 三、Go 侧口径差异(本机无 Go 工具链)

11. **缺失值标记**:Python 用 `None`,Go 没有 `None`,统一用 `math.NaN()`。因此 Go 侧
    "结果本身是真 NaN"与"窗口不足"**不可区分** —— 不要把 `go/rolling.go` 的内核喂给本来就
    含 NaN 的序列。
12. **未指定值的表示**:`closed` 在 Python 是 `None`、在 Go 是 `""`;`min_periods` 与 `test_size`
    在 Go 里用 `-1`;`max_train_size` 不设上限在 Go 里是 `0`(判断写成 `<= 0`)。
13. **`rolling_count` 未移植**(Go 侧实验台没用到);Python 侧的 `pct_change` 只保留真分支,
    Go 侧语义相同但没跑对拍。
14. **`Rng` 的 2³² 回绕**:Go 的无符号整型乘法按 2³² 取模,所以 `go/rng.go` 不需要 Python 那层
    `& 0xFFFFFFFF`。转写对拍里逐值比过 3000 个 `uniform` 与 3000 个 `normal`,确认常数与语义一致。

## 四、本 demo 的建模取舍(哪些是"选了一种读法")

15. **`rolling_origin_splits` 在同参数下与 `TimeSeriesSplit` 的切分完全一致**(`horizon` 就是
    `test_size`)。差别只在:返回 list 可重复遍历、用 `min_train` 静默少给几折而不是报错、
    以及把"预测跨度"的语义写在名字上。自检断言了这个等价性,免得读者以为两者在测不同的东西。
16. **pandas 的 `rolling` 文档未给出各 `closed` 取值下的下标级覆盖区间对照表**,故本 demo 的
    对齐规则是**用 pandas 3.0.6 实测反推 + 官方措辞校验**得到的。这属于口读口径而非文档明示,
    已在 `python/rolling.py` 的模块 docstring 里标注来源。
17. **"因果/错位/泄漏"三档的定义是本 demo 自己立的**:判据取"第 `t` 行不得依赖 `x[j]` (`j > t`)",
    因此在 `y_t = x_{t+1}` 的设定下把"含当期"归为**对齐**而非泄漏。换一个标签时点(例如目标是同期量),
    同一条 `rolling_mean(x,5)` 就是泄漏 —— 判据不变,结论随设定变。
18. **E4 的非平稳变体只改了一处**:第 1000 期起整体抬升 3.0。它的作用是证明"CV R² 变负"
    与"泄漏有多大"之间**没有可用的对应关系**。
