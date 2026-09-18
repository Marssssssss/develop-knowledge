# 九宫格缩放（9-Slice / border-image）

## 简介

把一张图切成**九块**（四角、四边、中间），只拉伸/平铺四条边与中间，四角**原样保留**，这样把一张小图铺到任意尺寸的矩形上时，圆角、高光、描边这些「形状信息」不会被拉变形。它是游戏 UI 里最基础、出场率最高的技术：按钮底图、面板背景、气泡框、进度条槽、列表项底色，几乎全靠它。

- **切片（slice）**：四条从图像边缘向内的偏移，把图像切成九宫格
- **角块（corner）**：只缩放到角区域大小，**永不平铺**——它承载圆角半径等形状信息
- **边块（edge）**：沿边的法向拉伸到区域厚度，沿边的切向按平铺策略重复
- **中间块（middle）**：默认不绘制（`fill` 关键字才画），这是 CSS 与游戏引擎口径差异最大的一处
- **平铺策略**：`stretch`（拉伸）/ `repeat`（平铺居中）/ `round`（整块缩放平铺）/ `space`（留白均分）

规范出处：W3C **CSS Backgrounds and Borders Module Level 3 §5 Border Images** 给出了这套模型的完整定义（切片、绘制区、平铺、绘制顺序），是游戏引擎各家 9-slice 实现的共同参照。本 demo 把该规范的每一条硬规则都写成可执行代码 + 断言。

## 原理详解

### 1. 切片：§5.2 `border-image-slice`

四条内缩偏移（上、右、下、左）把图像切成九块：

```
      left     middle      right
   ┌────────┬──────────┬────────┐
 t │  tl    │   tc     │   tr   │   ← 四角：原样
 o ├────────┼──────────┼────────┤
 p │  ml    │   mc     │   mr   │   ← 四边：拉伸/平铺
   ├────────┼──────────┼────────┤
 b │  bl    │   bc     │   br   │   ← 中间：默认丢弃
   └────────┴──────────┴────────┘
```

规范里的三条硬规则（本 demo 断言 [2][3] 覆盖）：

| 规则 | 原文要点 | 后果 |
| --- | --- | --- |
| 百分比基准 | 水平偏移相对**图像宽度**、垂直偏移相对**图像高度** | 100×80 的图，25% 的上下偏移是 20px 而非 25px |
| 越界即 100% | "Computed values larger than the size of the image are interpreted as 100%" | slice 写 999 等价于整图高度 |
| 左右/上下之和超限 | 左+右 ≥ 图宽 → **上边、下边、中间**三块为空 | 不是「重叠」，是这些块直接变成空图 |

### 2. 绘制区：§5.3 `border-image-width`

目标矩形同样被四条偏移切成九块区域。取值形态：

- **数字**：`border-width` 的倍数（初始值 `1`）
- **`auto`**：取对应切片的内在尺寸
- **百分比**：相对 border image area 的尺寸

游戏引擎通常没有 `border-width` 概念，直接把**切片尺寸**当作目标侧的边框区厚度（Unity 的 `pixelsPerUnit` / `border` 四元组即如此）。

### 3. 两步缩放与平铺：§5.6

```
第一步 Scale to border-image-width          第二步 Scale to border-image-repeat
  上/下边：高 → 区域高，宽等比                  stretch：宽 → 中间区宽（1 块）
  左/右边：宽 → 区域宽，高等比                  round  ：缩放每块使整数块刚好填满
  四  角：缩放到角区宽高                        repeat ：不缩放、铺 floor(n) 块、居中
  中  间：宽随上边因子、高随左边因子             space  ：不缩放、余量均分到 n+1 个间隙
```

四种平铺在 `src=27 → 区域长 200` 下的实测（本 demo [4]）：

| 策略 | 块数 n | 单块长 | 首块起点 | 间隙 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `stretch` | 1 | 200 | 0 | — | 直接拉满，图案被拉伸 |
| `repeat` | 7 | 27 | **5.5**（居中） | 0 | 两端各裁掉半块 |
| `round` | 7 | **28.5714** | 0 | 0 | 缩放每块使 7×块长 = 200 |
| `space` | 7 | 27 | 1.375 | **1.375** | 8 个等间隙（块前/块间/块后） |

注意 `repeat` 的居中规则是规范明文："If the first keyword is **repeat**, the top, middle, and bottom images are **centered horizontally** in their respective regions. Otherwise the images are placed at the **left edge**." —— 只有 `repeat` 居中，其余三种都靠左/靠上。

### 4. 核心函数（Python / Go 命名一致）

| 函数 | 说明 |
| --- | --- |
| `slice_image` / `SliceImage` | §5.2 切片，含百分比基准、100% 截断、和超限置空 |
| `resolve_tiling` / `ResolveTiling` | §5.5 四种平铺，返回 `(n, tile, offset, gap)` |
| `layout_edge` / `LayoutEdge` | §5.6 两步：先按区域厚度等比缩放，再平铺 |
| `nine_slice` / `NineSlice` | 组装：四角缩放、四边平铺、中间按 `fill` 决定 |

## 对比 / 选型

| 方案 | 圆角是否变形 | 内存 | 适用 |
| --- | --- | --- | --- |
| 整图拉伸 | **变形**（实测 27px 圆角被放大到 133.33px） | 1 张 | 纯色/无形状信息的底 |
| **9-slice（stretch）** | 不变形 | 1 张 | 纯色/渐变面板、按钮 |
| 9-slice（repeat/round） | 不变形 | 1 张 | 有重复花纹的边框（铆钉、虚线、链条） |
| 9-slice（space） | 不变形 | 1 张 | 花纹必须等距且不缩放（图标序列） |
| 多张切图 | 不变形 | N 张 | 已基本被 9-slice 取代 |

## 环境准备

- Python ≥ 3.8（仅标准库）/ Go ≥ 1.21
- 无第三方依赖

## 运行方式

```bash
cd python && python3 nine_slice.py     # 26 项断言（算法在 nine_slice_core.py）
cd go     && go run nine_slice.go      # 打印各步结果
```

## 关键代码片段

```python
def resolve_tiling(kind: str, src: float, dst: float) -> Tiling:
    if kind == "stretch":
        return Tiling(kind, 1, dst, 0.0, 0.0)          # 拉满
    n_float = dst / src
    if kind == "repeat":
        n = int(math.floor(n_float))
        return Tiling(kind, n, src, (dst - n * src) / 2.0, 0.0)   # 居中
    if kind == "round":
        n = max(1, int(round(n_float)))
        return Tiling(kind, n, dst / n, 0.0, 0.0)      # 整块数填满
    if kind == "space":
        n = int(math.floor(n_float))
        gap = (dst - n * src) / (n + 1) if n > 0 else dst
        return Tiling(kind, n, src, gap, gap)          # n+1 个等间隙
```

## 性能与边界

- **四角不参与拉伸**是全部收益的来源：整图拉伸时 81→400 会把 27px 圆角放大到 **133.33px**（4.94×），9-slice 下恒为 27px。
- **目标小于四角之和**时中间区被压到 0，四角互相覆盖（40×40 目标 + 27 切片 → 中间 0×0，角块总宽 54 > 40）。很多引擎此时会退化成整图缩放或直接不画，需要美术给最小尺寸约束。
- 平铺块数 `n = floor(dst/src)`：目标很长、切片很小时块数线性增长，`round`/`space` 在大尺寸下的视觉差异会被摊薄，优先考虑 `stretch`。

## 注意事项与常见坑

1. **中间块默认不画**。CSS 需要显式 `fill`；但 Unity `Image.Type.Sliced` 与 Android NinePatch **默认填充中间**（NinePatch 由黑线标记的可拉伸区与内容区决定）。跨引擎复用资源时这是最常见的「中间空了一块」事故。
2. **百分比基准不是统一量**：上下看高度、左右看宽度，非正方形图上 `25%` 的四条偏移并不相等。
3. **`repeat` 会居中裁半块**，而 `round`/`space`/`stretch` 靠左。无缝花纹用 `repeat` 时，两端各露半块是**规范行为**，不是 bug；想要完整块请用 `round` 或 `space`。
4. **`space` 的间隙数是 n+1 而不是 n-1**——块前、块间、块后都要留，写错会导致尾部溢出。
5. **切片线必须落在纯色/规则区域**：切到渐变或花纹上，拉伸后会出现明显的接缝。
6. **Android NinePatch 的 1px 黑边不算图像内容**：真正的可用区域是去掉四周 1px 标记线之后的部分，导入 Unity/自研管线前要先剥掉，否则四边会多出一条黑线。

## 参考资料（实际阅读过的权威来源）

- [CSS Backgrounds and Borders Module Level 3 — §5 Border Images](https://www.w3.org/TR/css-backgrounds-3/#border-images) — §5.2 切片（百分比基准、100% 截断、和超限置空）、§5.3 border-image-width、§5.5 四种 repeat 取值、§5.6 四步绘制流程与 `repeat` 居中规则，本 demo 全部规则的直接来源
- [Unity Learn — Optimizing Unity UI](https://learn.unity.com/tutorial/optimizing-unity-ui) — Canvas/Sub-canvas 与 UI 渲染口径（本大类 demo 4 的主来源，此处用于核对 UGUI 的 Sliced 绘制语义）
