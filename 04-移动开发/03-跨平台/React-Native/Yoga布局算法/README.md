# Yoga 布局引擎：取整、缓存与两遍弹性分配

> 目录：`04-移动开发/03-跨平台/React-Native/Yoga布局算法/`
> 语言：Python（`python/main.py` + `python/selfcheck_yoga.py`，**60 断言实跑全绿**）/ Go（`go/yoga.go` + `go/main.go` 人工审查 + bracket_check + go_sanity + go_crossref）

## 一、简介

Yoga 是 React Native 的布局引擎，是一份 **Flexbox 子集的 C++ 实现**。它和浏览器 flexbox 的关键差别不在「支持哪些属性」，而在三处工程化取舍：

1. **像素网格对齐**（`PixelGrid.cpp`）——布局算完还要再过一遍取整；
2. **测量缓存**（`Cache.cpp`）——文本的 `measure` 很贵，靠四条规则判断能否复用；
3. **两遍弹性分配**（`CalculateLayout.cpp`）——规范（CSS Flexbox §9.7）要求循环到收敛，Yoga 固定只跑两遍。

本 demo 把这三段逐行转写成可执行模型，用断言把源码里的**分支顺序、常量、以及注释与实现打架的地方**钉死。

## 二、原理

### 2.1 `roundValueToPixelGrid`：先缩放、再按 0.5 进位

```cpp
double fractial = fmod(scaledValue, 1.0);
if (fractial < 0) ++fractial;              // fmod(-2.2) = -0.2 -> 0.8
...
fractial > 0.5 || inexactEquals(fractial, 0.5) ? +1.0 : +0.0
```

要点：

| 输入 | 结果 | 说明 |
| --- | --- | --- |
| `1.5` | `2.0` | **0.5 一律进位**，不是 IEEE 的四舍六入五取偶 |
| `2.5` | `3.0` | 同上 |
| `-2.2` | `-2.0` | 负数先补 1 再判 0.8 > 0.5，等价于「四舍五入」 |
| `-2.6` | `-3.0` | 补 1 后 0.4 < 0.5 |
| `1.4 @ psf=2` | `1.5` | 先乘 `pointScaleFactor` 再取整，最后除回去 |

`inexactEquals` 的 epsilon 是**硬编码 0.0001**，且**两个 NaN 视为相等**（`Comparison.h:56`）。

### 2.2 宽度不是「取整后的宽度」，而是「取整后的两条边相减」

```cpp
setDimension(Width,
    roundValueToPixelGrid(absoluteNodeRight, ...) - roundValueToPixelGrid(absoluteNodeLeft, ...));
```

100px 三等分的经典结果因此是 **33 / 34 / 33**（中间那格吃掉误差），位置是 0 / 33 / 67。若改成「先取整宽度再累加」，会出现 33+33+33=99 的缝隙。

`NodeType::Text` 走 `textRounding`：位置 `forceFloor`（只向下），宽度在**有小数值时 `forceCeil`、无小数值时 `forceFloor`** —— 两头夹住，保证文本宽度只会变大不会变小，源码注释写明这是为了避免截字。

### 2.3 测量缓存的四条规则

`canUseCachedMeasurement` 先看一条否决（`lastComputed` 为负 → 直接 False），再对宽高各自求「是否兼容」：

| 规则 | 条件 |
| --- | --- |
| 同一约束 | `lastMode == mode && inexactEquals(取整后的可用空间)` |
| 精确且尺寸相同 | `mode == StretchFit && inexactEquals(size, lastComputed)` |
| 曾按最大内容量过 | `mode == FitContent && lastMode == MaxContent && size >= lastComputed` |
| 更严格但仍装得下 | 两次都 `FitContent` 且 `lastSize > size && lastComputed <= size` |

`pointScaleFactor != 0` 时**先用像素网格取整再比**（本 demo 的 E8/E9：可用 1.4 与 1.2 在 `psf=1` 下被判为同一约束，在 `psf=0` 下不是）。

### 2.4 FlexLine：gap、抬 1、以及一个「注释与实现不一致」

`calculateFlexLine` 的分行判据是「加上这一项会不会超过可用主轴尺寸」，但**首项永远不会被挤到下一行**（断行要求 `!itemsInFlow.empty()`）。

两个合计因子：

- `totalFlexGrowFactors`：`0 < x < 1` 时抬到 1；
- `totalFlexShrinkScaledFactors`：源码注释同样写「needs to be floored to 1」，但**判据是 `> 0 && < 1`，而它恒为负**（`-shrink × basis`），所以**永远抬不起来**。本 demo 断言了 `-0.4` 原样保留，只做记录不做「修正」。

### 2.5 两遍分配：默认行为其实是「修复前」的行为

规范要循环到收敛，Yoga 只跑两遍：第一遍把被 min/max 夹住的项**冻结**并把差额从剩余空间里扣掉，第二遍给其余项按因子分配（同样要过一遍 min/max）。

关键分歧点是第一遍用**原始合计因子**还是**滚动（已被扣减的）合计因子**：

```
basis 20 ×3，可用 100，grow 1:1:1，max 25 / 35 / 无
原始总量      -> [25, 35, 37.5]  残留 2.5（末项第二遍又被夹住，空间没分完）
滚动总量(默认) -> [25, 35, 40.0]  残留 0（两项都在第一遍被冻结）
```

`Config.h` 的默认值是

```cpp
Errata errata_ = Errata::MinSizeUndefinedInsteadOfAuto | Errata::FlexFirstPassUsesRunningTotals;
```

即 **24**：默认走「滚动总量」，也就是 GitHub issue #2006 修复前的老行为，靠 errata 位保持兼容。同时 `MinSizeUndefinedInsteadOfAuto` 默认开启意味着 CSS Flexbox §4.5 的自动最小尺寸**默认不生效**（`computedAutoMinMainSize` 被填成 Undefined）。

`YGErrataClassic = 2147483646 = All & ~1`，即「除 `StretchFlexBasis` 外全开」。

## 三、对比

| 维度 | 浏览器 flexbox | Yoga |
| --- | --- | --- |
| 弹性长度求解 | 循环到收敛（§9.7） | 固定两遍，官方注释承认「不会处理所有情况」 |
| 取整 | 通常交给合成器，布局值仍为浮点 | 布局结果整体过 `roundLayoutResultsToPixelGrid` |
| 文本测量 | 由排版引擎自己缓存 | `Cache.cpp` 四条规则 + 像素网格取整后比较 |
| 兼容开关 | 无 | `Errata` 位掩码（`All`/`Classic` + 逐位） |
| grow 之和 < 1 | 剩余空间不分配 | 同上，但合计因子被抬到 1，导致**分得更少**（`0.2/0.3` 时残留 30/60） |

## 四、环境

- Python 3.13（仅标准库）
- Go 1.21+（无本机工具链，Go 版只做人工审查与静态检查）

## 五、运行

```bash
cd python && python main.py            # 打印取整 / 分行 / 两遍分配的对照
cd python && python selfcheck_yoga.py  # 60 项断言
```

## 六、关键代码

| 文件 | 对应源码 |
| --- | --- |
| `python/main.py:round_value_to_pixel_grid` | `yoga/algorithm/PixelGrid.cpp:15` |
| `python/main.py:round_layout_results_to_pixel_grid` | `yoga/algorithm/PixelGrid.cpp:65` |
| `python/main.py:can_use_cached_measurement` | `yoga/algorithm/Cache.cpp:45` |
| `python/main.py:calculate_flex_line` | `yoga/algorithm/FlexLine.cpp:16` |
| `python/main.py:distribute_free_space` | `CalculateLayout.cpp:1063 / :1269` |

## 七、性能边界

- 两遍分配是 **O(行内项数)**，与规范的「不定次循环」相比牺牲了完备性换可预测的耗时（官方注释原话）。
- 测量缓存命中时跳过整棵子树的 measure；被否决时（如可用空间从 `MaxContent` 收紧）要重测。
- `pointScaleFactor` 非 0 时，每次缓存比较都要多两次 `roundValueToPixelGrid`。
- 第一遍用滚动总量会让后续项的基准尺寸变大，从而**冻结更多项**——本 demo 的 H4 断言到「冻结 2 项 vs 1 项」。

## 八、坑

1. **0.5 是进位不是取偶**：`round(2.5) = 3`，与 Python 内建 `round()` 相反。
2. **宽度是两条边相减**：直接对宽度取整会破坏「总宽守恒」。
3. **文本节点位置只向下取整**（`forceFloor = textRounding`），普通节点才是四舍五入。
4. **负数取整**：`fmod` 保号，必须先 `+1` 再判，否则 `-2.2` 会被算成 `-3`。
5. **`psf = 0` 的除法**：C++ 得 NaN，Python 会抛 `ZeroDivisionError`，需要显式模拟 IEEE。
6. **shrink 合计永远不会被抬到 1**（判据 `> 0` 与恒负矛盾），注释不可信。
7. **默认 errata 是「修复前行为」**，且自动最小尺寸默认关闭；想对齐浏览器需显式 `setErrata(Errata::None)`。
8. **两项都被 min 夹住时第二遍会溢出**：本 demo H7/H8 得到 `[55, 55] = 110 > 100`，这是两遍法的已知不完备。
9. `inexactEquals` 对两个 NaN 返回 True，写断言时不能靠「NaN != NaN」来区分。

## 九、参考资料（实际读过）

- `facebook/yoga@main` — `yoga/algorithm/CalculateLayout.cpp`、`PixelGrid.cpp`、`Cache.cpp`、`FlexLine.cpp`、`SizingMode.h`、`numeric/Comparison.h`、`enums/Errata.h`、`YGEnums.h`、`config/Config.h`
  （经 `cdn.jsdelivr.net/gh/facebook/yoga@main/...` 抓取）
- Yoga 官方文档 `website/docs/about-yoga.md` 与 `website/docs/styling/flex-basis-grow-shrink.mdx`
- W3C CSS Flexible Box Layout Module Level 1 §9.7（resolve flexible lengths）、CSS Sizing 3 §auto box sizes（源码注释引用）
- GitHub issue `facebook/yoga#2006`（第一遍用滚动总量冻结多余项的修复动机，源码注释直接给出链接）
