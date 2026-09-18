# 流式布局与弹性尺寸（Flexbox 主轴算法）

## 简介

游戏 UI 里"一排按钮自动排开、容器变宽时自动均分、变窄时自动收缩、排不下就换行"这套逻辑，标准答案就是 **CSS Flexbox 的主轴布局算法**。W3C **CSS Flexible Box Layout Module Level 1** 把这套算法写成了可逐条执行的规范文本（§9.3 分行、§9.7 弹性长度解析），它是 Unity Layout Group、Unreal UMG、Cocos Layout、Flutter Flex 的公共参照物。

- **flex base size**：分配前的基准尺寸（≈ Unity `LayoutElement.preferredWidth`）
- **hypothetical main size**：flex base 经 min/max 钳制后的尺寸，用于**分行**与**判定 grow/shrink**
- **flex grow / shrink**：剩余空间/亏空空间的分配权重
- **freeze（冻结）**：某项的尺寸被定死后退出后续分配，是 §9.7 循环推进的唯一保证
- **min/max violation（违约）**：分配结果被 min/max 修正后，需要把违约项冻结并重算

## 原理详解

### 1. §9.3 收集 flex lines（换行）

```
容器 inner main = 300，四项 outer hypothetical = 120
  i0(120) → 累计 120
  i1(120) → 累计 240   ← 还能放
  i2(120) → 若加入则 360 > 300  ⇒ 换行
  i3(120) → 累计 240
结果：[[i0,i1], [i2,i3]]
```

三条要点（本 demo 断言 [9][10][11]）：

| 要点 | 规范原文要点 | 后果 |
| --- | --- | --- |
| 用 outer hypothetical | "the size of a flex item is its **outer hypothetical main size**" | 换行判定发生在**弹性分配之前** |
| 首项放不下也单独成行 | "If the very first uncollected item wouldn't fit, collect just it into the line" | 不会出现空行死循环 |
| 零尺寸项归上一行 | 规范 note | 已被"正好填满"的行末尾仍会挂上 0 尺寸项 |

### 2. §9.7 Resolving Flexible Lengths（九步）

```
1. 决定 used flex factor：Σ outer hypothetical < 容器 → grow，否则 shrink
2. target = flex base size，全部未冻结
3. 冻掉"不该参与分配"的项：
     · flex 因子为 0
     · grow 时 flex base > hypothetical 的项
     · shrink 时 flex base < hypothetical 的项
4. initial free space = 容器 − Σ(冻结项取 outer target / 其余取 outer base)
5. 循环：
   a. remaining free space（同上口径，重算）
   b. 未冻结因子之和 < 1 → remaining = min(|initial×Σfactors|, |remaining|)（保留符号）
   c. 按因子比例分配
        grow  ：target = base + remaining × (grow_i / Σgrow)
        shrink：scaled_i = shrink_i × inner_base_i
                target = base − |remaining| × (scaled_i / Σscaled)
   d. Fix min/max violations：钳到 [max(0,min), max]，content-box floor 到 0
        target 被改大 → min violation；被改小 → max violation
   e. Freeze over-flexed items：Σ(钳后−钳前)
        == 0 → 全部冻结（收敛）
        >  0 → 冻结所有 min 违约项
        <  0 → 冻结所有 max 违约项
      （规范 note：至少冻一项，保证循环推进并终止）
6. used main size = target
```

### 3. 两条反直觉结论

**① grow 因子之和 < 1 时不会填满容器。** 规范 §9.7 步骤 5b 明文：若未冻结项因子之和小于 1，则只取 `initial free space × Σfactors`。实测容器 600、两项 base 100、grow 各 0.25 → 各得 200，**容器还剩 200 空着**（断言 [3]）。

**② shrink 不是按因子平分，而是按 `shrink × inner flex base size` 加权。** 所以"大块让得多"：base 100 与 base 300、shrink 均为 1、容器 200 → 亏空 200 按 100:300 摊 → **50 / 150**，而不是各让 100（那样小项会变成 0）（断言 [5]）。

### 4. 冻结循环实例（断言 [6]）

```
容器 100，A(base 200, min 150, shrink 1)，B(base 200, shrink 1)
轮 1：Σ outer base = 400，remaining = −300
       scaled 200:200 → 各让 150 → A=50, B=50
       A 被 min 150 拉回 150（+100，min violation），B 无违约
       Σviolation = +100 > 0 → 冻结 A(150)
轮 2：remaining = 100 − (150 + 200) = −250，只剩 B
       B = 200 − 250 = −50 → floor 到 0（+50，min violation）
       Σviolation = +50 > 0 → 冻结 B(0)
轮 3：全部冻结 → 收敛
结果：A=150, B=0，总宽 150 > 容器 100（**溢出是规范允许的结果**）
```

## 对比 / 选型

| 维度 | Flexbox 主轴算法 | 游戏引擎常见简化 |
| --- | --- | --- |
| 两趟 | 分行（hypothetical）→ 分配（target） | Unity Layout Group 同样两趟（先算 preferred 再分配 flexible） |
| min/max | 规范内建违约冻结循环 | Unity `minWidth/maxWidth`、Cocos 部分实现只做**一次钳制**、不重分配 → 结果会溢出 |
| 收缩权重 | `shrink × base` | 多数自研 UI 直接按权重平分 → 小元素被压成 0 |
| 换行 | §9.3 用 outer hypothetical | UMG Wrap Box 同理；自研常见 bug 是"用最终尺寸判断换行"造成二次回流 |

## 环境准备

- Python ≥ 3.8（标准库）/ Go ≥ 1.21，无第三方依赖

## 运行方式

```bash
cd python && python3 flex_layout.py    # 21 项断言（算法在 flex_layout_core.py）
cd go     && go run flex_layout.go     # 打印各场景结果
```

## 关键代码片段

```python
if sum_factors < 1:                       # §9.7 步骤 5b
    scaled = initial_free * sum_factors
    if abs(scaled) < abs(remaining):
        remaining = scaled

if using_grow:
    total = sum(it.grow for it in unfrozen)
    for it in unfrozen:
        it.target = it.flex_base + remaining * (it.grow / total)
else:
    # 收缩按 shrink × inner flex base size 加权 —— 大块多让
    scaled_shrink = {id(it): it.shrink * it.flex_base for it in unfrozen}
    total = sum(scaled_shrink.values())
    for it in unfrozen:
        it.target = it.flex_base - abs(remaining) * (scaled_shrink[id(it)] / total)
```

## 性能与边界

- §9.7 的循环**每轮至少冻一项**，故轮数 ≤ 项数 n，单行复杂度 O(n²)（每轮重算 Σ）；n 为一行内元素个数，几十个量级完全无压力。
- 换行是 O(n)。真正的代价在**交叉轴**与嵌套布局：父容器尺寸依赖子项 → 子项尺寸依赖父容器，形成多趟迭代（Unity 的 `CanvasUpdateRegistry.PerformUpdate` 分 PreLayout/Layout/PostLayout 三趟正是为此，见本大类 UI 合批 demo）。
- min 约束下**允许溢出**：规范不做"最后再压缩一遍"的兜底，超出的部分由 overflow 决定（滚动/裁切）。

## 注意事项与常见坑

1. **用错尺寸口径是最常见 bug**：分行用 *outer hypothetical*、算 free space 时冻结项用 *outer target* 而未冻结项用 *outer base*、收缩加权用 *inner* base。三处口径不同，混用会得到差一个 margin 的结果。
2. **`flex-basis: 0` 与 `flex-grow` 组合**才是"按比例瓜分全部空间"；`flex-basis: auto`（内容尺寸）时，grow 只分配**剩余**空间，比例并不等于最终尺寸比。
3. **`shrink` 不能防止溢出**：min 约束会触发冻结，结果是溢出而不是继续压缩。
4. **一次钳制 ≠ 规范算法**：只做"算完再 clamp"的简化实现，在 A/B 那个例子里会得到 A=150, B=50（总宽 200，凭空多出 100），与浏览器不一致。
5. **嵌套布局要分趟**：父布局先算，子布局后算（Unity 按层级深度排序 dirty Layout 列表），否则需要迭代到不动点。
6. **不要在同一帧反复触发重建**：布局 + 图形重建是 UI 卡顿的主因之一（见本大类 UI 合批 demo）。

## 参考资料（实际阅读过的权威来源）

- [CSS Flexible Box Layout Module Level 1](https://www.w3.org/TR/css-flexbox-1/) — §9.3 Collect flex items into flex lines（分行规则与零尺寸项 note）、§9.7 Resolving Flexible Lengths（九步算法、因子之和 < 1 规则、scaled flex shrink、min/max 违约与冻结推进 note），本 demo 全部断言的直接来源
- [Unity Learn — Optimizing Unity UI](https://learn.unity.com/tutorial/optimizing-unity-ui) — `CanvasUpdateRegistry.PerformUpdate` 的三步流程与 Layout 重建按层级深度排序（用于"嵌套布局要分趟"一节）
