# Canny 边缘检测(五阶段流水线)

## 简介

Canny 是 1986 年 John F. Canny 提出的**多阶段**边缘检测器,OpenCV 官方教程称它为"最优检测器(optimal detector)",因为它的设计目标就是同时满足三条判据:

- **低错误率(low error rate)**:只检出真实存在的边,不漏也不误。
- **良好定位(good localization)**:检出的边像素与真实边像素距离最小。
- **单一响应(minimal response)**:一条边只产生一个检测响应。

与 042 Sobel 的区别:042 只做"求梯度",输出的是**梯度幅值图**(一条边宽好几像素、且受噪声干扰);Canny 在其后加了**非极大值抑制**与**双阈值 + 滞后**,把梯度图收成**细且连通**的二值边缘图。所以 Canny 是 Sobel 的下游。

关键概念:

- **四个方向量化**:梯度方向被量化到 0/45/90/135 四个角度之一,再沿该方向做非极大值抑制。
- **双阈值 `high` / `low`**:`> high` 必为边、`< low` 必非边,中间段靠**连通性**裁决。
- **滞后(hysteresis)**:中间段只有与"确定边"连通才被接受 —— 这是"弱边能被带出来、孤立噪声不会"的机制来源。
- **阈值比**:Canny 建议 `high : low` 落在 **2:1 ~ 3:1**(OpenCV 教程的交互例子固定用 `ratio = 3`)。

## 原理详解

### 五阶段(OpenCV 4.x / 3.4 教程一致)

```
输入灰度图 I
  1. 去噪      I' = I * G(5x5, sigma≈1.4)          # 3.4 教程给 1/159 整数核
  2. 求梯度    Gx = I' * SobelX, Gy = I' * SobelY
               G  = sqrt(Gx^2 + Gy^2)  或  |Gx| + |Gy|
               θ  = atan2(Gy, Gx)       -> 量化到 0/45/90/135
  3. 非极大值抑制  沿 θ 方向与两个邻居比较,非局部极大置 0   -> 细边
  4. 双阈值     G > high  -> 确定边; G < low -> 丢弃
  5. 滞后       middle 段:与确定边 8 连通 -> 保留,否则丢弃
```

### 3.4 教程给出的 5x5 整数高斯核(除数 159)

```
K = 1/159 * [ 2  4  5  4  2 ]
            [ 4  9 12  9  4 ]
            [ 5 12 15 12  5 ]
            [ 4  9 12  9  4 ]
            [ 2  4  5  4  2 ]
```

本 demo 实测:该整数核是对 **σ ≈ 1.4** 连续高斯核的定点近似,最大相对偏差 **7.16%**(逐元素比较 25 个系数,见自检 A 段)。两者都满足"2D 核 = 1D 核外积"(可分离),本 demo 的 `gaussian_kernel_2d` 与整数核外积的差为 0。

### 非极大值抑制的四个方向

梯度方向**垂直于边缘**。把 [0,180) 量化成 4 档后,每档对应一组固定邻居:

| 量化角 | 邻居偏移 | 该方向对应的边缘走向 |
| --- | --- | --- |
| 0° | (x+1,y) / (x−1,y) | 垂直边缘 |
| 45° | (x+1,y+1) / (x−1,y−1) | 斜边 |
| 90° | (x,y+1) / (x,y−1) | 水平边缘 |
| 135° | (x−1,y+1) / (x+1,y−1) | 斜边 |

OpenCV 教程用「点 A 在垂直边上、梯度方向指向 B 与 C」的例子说明:只有 A 同时大于 B 和 C 才保留。

### 滞后的三种情形(OpenCV 教程原例)

| 像素 | 幅值位置 | 连通性 | 结果 |
| --- | --- | --- | --- |
| edge A | `> maxVal` | — | 确定为边 |
| edge C | `< maxVal` 但 `> minVal` | 与 A 相连 | 保留,得到完整曲线 |
| edge B | `> minVal` | 与确定边**不连** | 丢弃(哪怕它与 C 同处一个区域) |

教学讲义把它写成区域标记:`M ≥ θ_high` 为 strong、`θ_high > M ≥ θ_low` 为 weak,对 weak 做连通域标记后**保留含 strong 像素的连通域**,其余整块丢弃。这也解释了为什么 Canny 对"长线状结构"友好、对"孤立噪声点"免疫。

### 核心 API(cv.Canny)

```
void Canny(InputArray image, OutputArray edges, double threshold1, double threshold2,
           int apertureSize = 3, bool L2gradient = false)
```

- `threshold1` / `threshold2`:即 `low` / `high`(教程示例 `cv.Canny(img, 100, 200)`)。
- `apertureSize`:内部 Sobel 核的尺寸,**默认 3**(OpenCV 还支持 5/7)。
- `L2gradient`:**默认 false**,此时幅值用 `|Gx| + |Gy|`;置 true 才用 `sqrt(Gx²+Gy²)`(更准但更慢)。

## 环境准备

- 操作系统:任意(本 demo 无平台相关代码)
- Python 3.8+(**仅标准库**,不依赖 OpenCV / NumPy)
- Go 1.16+(仅标准库)

## 运行方式

### Python

```bash
cd python
python3 canny_check.py     # 47 项断言
```

`canny.py` 是库:`canny(灰度图, low, high) -> 0/1 边缘图`(灰度图为 `list[list[float]]`)。

### Go

```bash
cd go
go run .          # Go 1.21+(go.mod 已随目录提供)
```

`go/` 下是两个同包文件:`canny.go`(流水线实现)+ `main.go`(断言与入口),
拆开是为了守住「单源文件 ≤ 300 行」的仓库约束。

## 关键代码片段

```python
def non_max_suppression(mag, sectors, eps_ratio=1e-9):
    """沿梯度方向只保留局部极大。"""
    h, w = len(mag), len(mag[0])
    peak = max(v for row in mag for v in row)
    eps = peak * eps_ratio                     # 浮点残差护栏,见「注意事项」
    out = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            m = mag[y][x]
            if m <= eps:
                continue
            (d1x, d1y), (d2x, d2y) = NMS_OFFSETS[sectors[y][x]]
            n1 = mag[y + d1y][x + d1x] if 0 <= x + d1x < w and 0 <= y + d1y < h else 0.0
            n2 = mag[y + d2y][x + d2x] if 0 <= x + d2x < w and 0 <= y + d2y < h else 0.0
            if m > n1 and m >= n2:             # 平局规则必须写死,见「注意事项」
                out[y][x] = m
    return out
```

```python
def hysteresis(mag, low, high):
    """双阈值 + 滞后:确定边做种子,8 连通洪水填充带出弱边。"""
    WEAK, STRONG = 1, 2
    state = [[0] * w for _ in range(h)]
    stack = []
    for y in range(h):
        for x in range(w):
            if mag[y][x] >= high:              # 确定边
                state[y][x] = STRONG
                stack.append((x, y))
            elif mag[y][x] >= low:             # 中间段
                state[y][x] = WEAK
    while stack:                               # 从确定边出发扩散
        x, y = stack.pop()
        for dx, dy in NEIGHBORS_8:
            if state[y + dy][x + dx] == WEAK:
                state[y + dy][x + dx] = STRONG
                stack.append((x + dx, y + dy))
    return [[1 if v == STRONG else 0 for v in row] for row in state]
```

## 性能与边界

- 复杂度:5x5 高斯 + 两个 3x3 Sobel 均为 `O(H·W·k²)`,NMS 与滞后 `O(H·W)`,总 `O(H·W)`,与图像像素数线性。滞后用显式栈做连通域标记,不用递归,不会爆栈。
- 常数:Sobel 3x3 每个像素 18 乘加;5x5 高斯每像素 25 乘加(**可分离后降为 10 乘加**,本 demo 的核已验证可分离)。
- 本 demo 的 Python 版是纯解释执行的双层循环,`H·W` 到 10⁶ 量级会明显变慢;实际工程应把相关运算交给 SIMD / OpenCV。
- 边界:本 demo 用 clamp 复制;OpenCV 默认 `BORDER_DEFAULT`(reflect_101)。差异只影响最外 1~2 像素。
- 阈值上限:幅值范围取决于图像动态范围。未平滑的阶跃边缘,`|Gx|` 恰为 `4·(high−low)`;本 demo 的 0→200 阶跃得 800,所以 `high=300` 能检出、`high=500` 检不出。

## 注意事项与常见坑

1. **理想阶跃的两个梯度峰严格相等,平局规则必须写死。**
   0→200 的竖直阶跃,平滑后 `|G|` 在等距的两列上分别是 **434.1353 / 434.1353**(自检实测)。用 `>=` 比较两列都留(边缘 2 像素宽),用 `>` 比较一列都不留(边缘整条消失)。本 demo 取"先出现的邻居必须严格更小、后出现的允许相等",保证每行恰 1 个边缘像素。真实图像因为噪声不会精确平局,所以这个坑只在**合成图/仿真**里暴露,但一旦暴露就是整幅图级别的错误。
2. **平坦区有 1e-14 量级的浮点残差,会变成伪边。**
   本 demo 实测:同一幅图的平坦区,`|Gx|` 因累加顺序不同会得到 **0.0 与 2.842e-14** 两种结果;而 `L1 = |Gx| + |Gy|` 把残差累加后更明显。若用 `m == 0.0` 判"无梯度",NMS 会在纯平坦区输出一整排伪边(自检 D 段把这个现象单独断言出来了)。规避:**给 NMS 加一个相对于峰值的地板**(本 demo 用 `peak * 1e-9`),或依赖双阈值兜底。
3. **阈值取 0 会把整幅图判成边缘。**
   判据是 `>=` 而不是 `>`,所以 `low = high = 0` 时连"梯度恒为 0 的常量图"也全部满足 `>= high`。自检 F 段实测:8x8 常量图输出 **64/64** 全 1。工程上的意思是:**阈值必须 > 0**。
4. **`apertureSize` 与 `L2gradient` 的默认值容易记反。**
   默认 `apertureSize=3`、`L2gradient=false`。也就是说 **OpenCV 默认用 `|Gx| + |Gy|` 近似**,而不是教科书上的 `sqrt(Gx²+Gy²)`;实测两者在 45° 边上比值恰为 **√2 ≈ 1.4142**(两个分量相等时)。
5. **要先转灰度、先降噪。**
   `cv.Canny` 的输入按教程要求是单通道灰度图;`Sobel` 对噪声敏感,跳过 5x5 高斯会让噪声直接进 NMS。反过来,**过度平滑会抹掉弱边** —— 尖锐边缘的梯度峰会被抹平,`high` 再也够不着。
6. **`high`/`low` 是绝对阈值,跨图不可移植。** 图像对比度变化时幅值整体缩放(对比度 ×2 → 各分量 ×2 → `det`/`tr` 类响应 ×4),固定阈值必然失效。实践中要么按比例相对 `max` 取,要么用中位数启发式(`low ≈ 0.67·median`、`high ≈ 1.33·median`)——注意后者是**工程惯例,不是 Canny 原文口径**,本 demo 的实现里已标注。

## 参考资料(实际阅读过的权威来源)

- [OpenCV 4.x — Canny Edge Detection(tutorial_py_canny)](https://docs.opencv.org/4.x/da/d22/tutorial_py_canny.html) — 五阶段流程、5x5 高斯、Sobel 求梯度、四方向量化、NMS 的 A/B/C 例子、滞后阈值三种情形、`cv.Canny` 签名与 `apertureSize` / `L2gradient` 默认值。
- [OpenCV 3.4 — Canny Edge Detector(tutorial_canny_detector)](https://docs.opencv.org/3.4.2/da/d5c/tutorial_canny_detector.html) — 三条设计判据(低错误率 / 良好定位 / 单一响应)、除数 159 的 5x5 整数核、Sobel 掩码、方向量化到 0/45/90/135、"Canny recommended an upper:lower ratio between 2:1 and 3:1"、`ratio = 3` 的交互实现。
- [Stanford EE368 — Canny edge detector(讲义正文)](https://web.stanford.edu/class/ee368/Handouts/Lectures/2013_Autumn/9-Edge-Detection/Canny_Edge_Detector.pdf) — 边缘法线量化到 horizontal/−45°/vertical/+45°、strong/weak 的双阈值定义式 `M ≥ θ_high` 与 `θ_high > M ≥ θ_low`、典型 `θ_high/θ_low = 2...3`、连通域标记后"丢弃不含 strong 像素的区域"、σ 取 √2 / 2√2 / 4√2 的对比图。
