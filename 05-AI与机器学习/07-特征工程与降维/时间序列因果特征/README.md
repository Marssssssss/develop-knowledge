# 时间序列因果特征:滚动窗口的对齐陷阱 · 用支持集机械判定泄漏

> demo 516。本目录所有数字都由 `python/` 实跑产出;pandas 3.0.6 / scikit-learn 1.9.1 对拍
> **1439 项全过**(`_scratch` 临时脚本,不入库),Go 侧另做 3304 项转写对拍。

## 一、这个 demo 在讲什么

时间序列的交叉验证有两类失败,而它们**长得很像、治法完全不同**:

1. **跨切分泄漏**:训练集里混进了测试期或测试期之后的信息。`TimeSeriesSplit` + `gap` 治它。
2. **行内泄漏**:某一行特征自己就含了"预测该行目标时才该知道"的信息。切分无论怎么切都治不了
   —— 每一折内部同样泄漏。

这个 demo 用一个可控到极点的设定把两者分开:目标序列是 `x_t = 0.9 x_{t-1} + ε_t`,
预测目标是**下一期** `y_t = x_{t+1}`。于是"知道 `x_t`"和"知道 `x_{t+1}`"的差别可以被 R² 精确量化。

结论里最反直觉的一条:**训练集与留出集的分数落差,不能用来判断有没有泄漏**。实测落差最小的
恰恰是泄漏最严重的特征(0.064,占三档中最小),因为泄漏特征与目标几乎是确定关系,换到留出集
照样准。判泄漏只能靠机械检验 —— 本 demo 给出一个不需要标签、不需要切分、不需要跑模型的探针。

## 二、原理详解

### 1. 滚动窗口的对齐,是"末端在哪"的问题

pandas 的 `rolling` 默认窗口是**右闭**的,标签 `i` 覆盖 `[i - window + 1, i]` —— 也就是说
`rolling_mean(x, 5)[i]` **包含 `x[i]` 自己**。这是所有对齐问题的根。

`center=True` 只做一件事:**把窗口末端右移 `(window - 1) // 2`**,再套用 `closed` 规则。所以它是
**偏左**的(取整向下),`window=4` 时标签 `i` 覆盖 `[i-2, i+1]` 而不是对称的 `[i-2, i+2]`。
`closed` 的四个取值只把区间整体挪 ≤1 格:基准 `[start, end]`;`left` → `[start-1, end-1]`;
`both` → `[start-1, end]`;`neither` → `[start, end-1]`。

`min_periods` 默认取 `window`(整数窗口时),故前 `window-1` 行是缺失值;`std` **默认 `ddof=1`**,
`window=1` 且 `ddof=1` 时分母为 0,结果是缺失而不是 0。

### 2. "因果"的判据只有一条,而且必须说准

> **第 `t` 行的特征不得依赖 `x[j]` (`j > t`)。**

注意它**不等于**"只用历史"。在"用 `t` 期已知量预测 `t+1`"的设定下,`x[t]` 是决策时刻的已知量,
含它不构成未来依赖。把这两件事混为一谈,会写出两类相反的错误:把合法特征当泄漏删掉,或者把真泄漏
当对齐问题放过。于是本 demo 分三档:

| 档位 | 构造 | 支持集(第 i 行读到的下标) | 未来下标数 |
| --- | --- | --- | --- |
| 因果 | `shift(rolling_mean(x,5), 1)` | `[i-5, i-1]` | 0 |
| 错位 | `rolling_mean(x,5)` | `[i-4, i]` | 0 |
| 泄漏 | `rolling_mean(x,5,center=True)` | `[i-2, i+2]` | 2 |
| 泄漏 | `shift(rolling_mean(x,5), -1)` | `[i-3, i+1]` | 1 |

**"错位"这一档未来下标数是 0**,这就是反直觉的关键:它含当期但不含未来,在上述设定下不是泄漏,
只是**对齐差 1 格** —— 特征的注释若写着"过去 5 期均值"而代码含了当期,那是文档与实现不符。
真正致命的是 `center=True` 与 `shift(-1)`,它们读到了尚未发生的观测,与标签怎么定义无关。

### 3. `TimeSeriesSplit` 的 `gap` 治的是标签侧

官方定义很干净:`gap` 是"**从每个训练集末端剔除、留在测试集之前的样本数**"。它**完全不移动
测试集**,只把训练集末端整块砍掉。所以它治的是:标签若为未来 `h` 步的统计量
(`y_t = mean(x[t+1..t+h])`),训练集最后一行的标签会落进测试期 —— 这是**标签侧**的越界。
特征侧的越界(窗口右端越过 `t`)靠 `gap` 治不好。另两条口径:官方写明 `test_size` 默认
`n_samples // (n_splits + 1)`"是 `gap=0` 时允许的最大值";而 `n_splits >= 2` 的校验**不在**
`TimeSeriesSplit` 里,在父类 `_BaseKFold.__init__`,属构造期检查。

## 三、环境与运行

```bash
cd python
python main.py                  # 实验台:E1 对齐 / E2 行内泄漏 / E3 KFold / E4 gap / E5 留出 / E6 探针
python selfcheck_rolling.py     # PASS 72
python selfcheck_splits.py     # PASS 75
python selfcheck_model.py      # PASS 40
python selfcheck_features.py   # PASS 59
cd ../go && go run .            # 同包多文件必须用 `go run .`
```

纯标准库,无第三方依赖。自检**离线可跑**;与 pandas / sklearn 的对拍脚本放在工作区
`.scratch/` 下(已 gitignore,不入库)。本机无 Go 工具链,Go 侧证据为静态检查 + 转写对拍,见第九节。

## 四、关键代码

对齐内核只有 8 行,全部对齐语义都在这里(`python/rolling.py`):

```python
def _window_bounds(i, window, center, closed):
    end = i + (window - 1) // 2 if center else i
    start = end - window + 1
    if closed is None or closed == "right":   lo, hi = start, end
    elif closed == "left":                    lo, hi = start - 1, end - 1
    elif closed == "both":                    lo, hi = start - 1, end
    elif closed == "neither":                 lo, hi = start, end - 1
    return lo, hi
```

因果探针(`python/main.py` 的 `audit`)是三轴计数,不含任何启发式:

```text
轴一「未来依赖」:扰动 x[j],数「第 t < j 行跟着变」的 (t, j) 对数   # > 0 即泄漏
轴二「对齐」    :扰动 x[t],数「第 t 行跟着变」的行数               # 含当期?
轴三「下一期」  :扰动 x[t+1],数「第 t 行跟着变」的行数             # 含 x[t+1]?
```

自检里另有一个**支持集探针**:扰动 `x[j]`、看第 `i` 行变不变,直接收集"这一行读了哪些下标"。
它比读代码猜窗口强,因为它测的是实现**实际**读了什么。为防止"探针恒返回大集合"骗过断言,
探针自身带两个负控(常量函数 → 空集;读全序列 → 全集)。

## 五、实测数字

E1 对齐(手算可验):`t=6`(`x=7`)这一行,默认版 `6.0`(**含** `x[t]`)、`.shift(1)` 版 `5.0`
(只到 `x[t-1]`)、`center` 版 `7.0` —— 它含 `x[7]=8`,把下一期搬到了本期。

E2/E5 三档在时序 CV 与真·留出下的 R²(序列 1200 期,目标 `y_t = x_{t+1}`):

| 特征 | 时序 CV(5×100) | 最后 20% 留出 | 训练→留出落差 |
| --- | --- | --- | --- |
| 因果 `mean(x[t-5..t-1])` | 0.3070 | 0.3583 | **0.1755** |
| 错位 `mean(x[t-4..t])` | 0.4756 | 0.5091 | 0.1458 |
| 泄漏 `mean(x[t-2..t+2])` | 0.8285 | 0.8310 | **0.0638** |

**泄漏版在时序 CV 下照样拿到 0.83** —— 时序 CV 只能挡住跨切分的泄漏,挡不住行内泄漏。
而这个漂亮的数字不是好消息,是作弊成功的证据。同时注意最后一列:**落差最小的恰恰是泄漏最重的**,
"落差小 = 可信"是错的判据。

E3 随机 KFold 在自相关序列上的虚高(同一特征,只换切分方式):时序 `TimeSeriesSplit(5)` 折内 R² =
`0.439 0.378 0.431 0.225 0.343`(均值 `0.3633`),随机 `KFold(5)` = `0.423 0.515 0.551 0.580
0.568`(均值 `0.5276`)。相邻行的特征窗口高度重叠,KFold 把"邻居"放进训练集,等于让模型抄答案。

E4 `gap` 的结构性作用(标签跨 `h=3` 期):`gap` 从 0 增到 3,训练末行从 791 退到 788,"标签伸进
测试期的训练行数"从 3 降到 0 —— 而 CV R² 几乎不动(0.0284 → 0.0282)。把序列改成非平稳(第 1000
期起整体抬升 3.0),CV R² 全变负(-0.2439 → -0.2540),**但下降幅度依旧无法反推泄漏**。所以 `gap`
的价值是**消掉一个结构性错误**,不是"把分数抬上去"。

E6 机械因果探针(3 个种子 × `n=48`,扰动幅度 1.0;分母是各自的有效行数,三档 NaN 边缘不同):

| 特征 | 依赖未来 `x[j]`(`j>t`) | 扰动 `x[t]`→第 t 行 | 扰动 `x[t+1]`→第 t 行 |
| --- | --- | --- | --- |
| 因果 | 0 / 2709 | 0 / 129 | 0 / 126 |
| 错位 | **0 / 2838** | 132 / 132 | 0 / 129 |
| 泄漏 center | 264 / 3102 | 132 / 132 | 132 / 132 |

`center` 的未来依赖对数 264 = 3 种子 × 88,而 88 恰好是每个扰动点 `j` 贡献它的两个未来位置
`t=j-1` 与 `t=j-2`(48 个点被 NaN 边缘砍到 44 个)—— 与"窗口右伸 2 格"严格吻合。
**错位版未来依赖为 0** 这一格,就是把"对齐失误"与"泄漏"彻底切开的那一刀。

## 六、注意事项与常见坑

完整版(18 条,含 pandas 对齐 5 点、sklearn 口径 4 点、Go 口径差异 4 点、建模取舍 4 点)见
[`NOTES.md`](./NOTES.md)。最要命的四条:

1. **`rolling_mean(x, w)` 含当期,`.shift(1)` 才把右端推到 `t-1`。** 但它**未必**是泄漏 ——
   取决于标签时点,先看支持集再决定删不删。
2. **`std` 默认 `ddof=1`**;`window=1` 时结果是缺失而不是 0。`closed='neither'` 时窗口宽度比 `w`
   小 1,若 `min_periods` 仍取默认的 `w`,**整列都是缺失**。
3. **`split()` 是生成器**,两条硬校验不迭代就不执行 —— 只调用 `split()` 拿不到异常。
4. **`gap` 不动测试集**,只砍训练集末端;别把它当成"把测试集往后推"。

## 七、参考资料(实际阅读过的来源)

- [pandas `DataFrame.rolling` API](https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.rolling.html)
  —— `window` / `min_periods` / `center` / `closed` 的官方定义、`closed` 四取值语义、`center` 位移规则
- [pandas Windowing operations 用户指南](https://pandas.pydata.org/docs/user_guide/window.html)
  —— 滚动窗口的对齐与 `closed='right'` 为基准的说明、`min_periods` 默认值
- [scikit-learn `sklearn.model_selection.TimeSeriesSplit` API](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html)
  —— 原文要点:"split time-ordered data, where other cross-validation methods are inappropriate,
  as they would lead to **training on future data and evaluating on past data**"、
  "successive training sets are **supersets** of those that come before them"、
  `gap` = "Number of samples to exclude from the end of each train set before the test set"、
  `test_size` 默认 "the maximum allowed value with `gap=0`"、notes 里的训练集大小公式
- [scikit-learn 1.9.1 源码 `model_selection/_split.py`](https://github.com/scikit-learn/scikit-learn/blob/main/sklearn/model_selection/_split.py)
  —— `TimeSeriesSplit.split` 的两条硬校验原文(`Cannot have number of folds=… greater than the
  number of samples=…`、`Too many splits=… with test_size=… and gap=…`)、`max_train_size` 只在
  严格小于 `train_end` 时才截断、以及 docstring 里 `n_splits=3, test_size=2(, gap=2)` 的
  **示例输出**(本目录自检直接拿它当数值锚点);另在 `_BaseKFold.__init__` 确认 `n_splits <= 1`
  的报错属构造期
- [scikit-learn §12.2 Common pitfalls — Data leakage](https://scikit-learn.org/stable/common_pitfalls.html)
  —— 原文定义:"information that **would not be available at prediction time** is used when
  building the model",后果是 "**overly optimistic** performance estimates";
  纪律:"never call `fit` on the test data"

pandas 的 `rolling` 文档未给出各 `closed` 取值下窗口覆盖区间的下标级对照表,故本 demo 的
对齐规则是**用 pandas 3.0.6 实测反推 + 官方措辞校验**得到的,这属于口读口径而非文档明示,
已在 `python/rolling.py` 的模块 docstring 与 [`NOTES.md`](./NOTES.md) §三.16 标注来源。
"因果/错位/泄漏"三档的定义同样是本 demo 自立的(§三.17):判据取"第 `t` 行不得依赖 `x[j]` (`j > t`)",
换一个标签时点(例如目标是同期量),同一条 `rolling_mean(x,5)` 就是泄漏 —— 判据不变,结论随设定变。

## 八、自检

| 文件 | 断言数 | 内容 |
| --- | --- | --- |
| `python/selfcheck_rolling.py` | PASS 72 | 支持集探针 + 两负控、`closed` 四取值的性质关系、`center` 位移、`ddof` 双分支、空窗口分支、`pct_change` 分母为 0 的位置 |
| `python/selfcheck_splits.py` | PASS 75 | 手推下标元组 + 超集链 + 两条硬校验(含"恰好差 1 必须放行"的反证)+ **官方 docstring 四组数值锚点** |
| `python/selfcheck_model.py` | PASS 40 | 选主元的必要性(附 naive 不选主元的负控)、`r2` 的常量 `y` 分支与**可取负**、LCG 首值与大样本矩 |
| `python/selfcheck_features.py` | PASS 59 | 四个构造的支持集逐一下标对照、`build_matrix` 同掩码、标准化分支、`audit` 三轴签名与负控、`VARIANTS` 文字标签与实现的相符性 |
| 合计 | **246** | 全部离线可跑、无第三方依赖 |

对拍(工作区 `.scratch/`,不入库):与 pandas 3.0.6 + scikit-learn 1.9.1 逐项比输出
**OK 1439 / BAD 0**;Go 侧转写对拍 **OK 3304 / FAIL 0**(含 `windowBounds` 逐格、滚动四函数全网格、
`TimeSeriesSplit` 上千组网格、LCG 逐值、支持集与 `Audit` 三轴);Go 静态检查
`bracket_check` 6 文件全 BALANCED、`go_sanity` OK、`go_crossref` 无重名无未定义调用。
