# Sobel 边缘检测(图像梯度)

## 简介

- Sobel 算子是**离散微分算子**:用一对 3x3 卷积核对图像做相关运算,近似计算图像强度函数的梯度,是经典边缘检测的第一步。
- 它把**高斯平滑与微分结合**在一起(中心列/行权重 ×2),比纯差分对噪声更鲁棒(OpenCV 官方教程原话:"joint Gaussian smoothing plus differentiation operation")。
- 关键概念:
  - **Gx**:水平方向变化率 → 检测**垂直**边缘;**Gy**:垂直方向变化率 → 检测**水平**边缘
  - **梯度幅值** `G = sqrt(Gx² + Gy²)`,工程近似 `G ≈ |Gx| + |Gy|`(OpenCV 教程给出的简化式)
  - **梯度方向** `θ = atan2(Gy, Gx)`,垂直于边缘走向,后续 Canny 非极大值抑制要用
- 历史:Irwin Sobel 1968 年在 Stanford AI Lab(SAIL)提出;3x3 核精度不足时 OpenCV 用 Scharr(1925)核替代。

## 原理详解

边缘 = 强度发生显著跳变的位置。一维信号里"跳变"在一阶导数上表现为极大值,推广到二维即"梯度幅值大的像素"。

标准 3x3 核(OpenCV 文档原文):

```
Gx = [ -1 0 +1 ]      Gy = [ -1 -2 -1 ]
     [ -2 0 +2 ]           [  0  0  0 ]
     [ -1 0 +1 ]           [ +1 +2 +1 ]
```

处理流程:

```
灰度图 I ──> correlate(I, Gx) ──> Gx(垂直边缘响应)
        └─> correlate(I, Gy) ──> Gy(水平边缘响应)
                                     │
Gx,Gy ──> G = sqrt(Gx²+Gy²) ──> 阈值二值化 ──> 边缘图
        └─> θ = atan2(Gy,Gx) ──> 边缘法向(方向图)
```

- 核中心行/列权重 2 = 对中间像素加权的高斯平滑,邻域行/列权重 1;[−1,0,1] 部分是中心差分。
- 相关 vs 卷积:OpenCV `filter2D`/`Sobel` 实际执行**相关**(不翻转核);Gx 不对称,两种运算差一个符号(本 demo 按 OpenCV 行为实现,已在代码注释说明)。
- Scharr:当 ksize=3 时 Sobel 只是导数的粗近似,OpenCV 提供 `Scharr()`/`ksize=-1` 用 `[−3 0 +3]` 权重获得更高旋转对称精度。

核心 API(OpenCV):

```
cv.Sobel(src, dst, ddepth, dx, dy, ksize=3, scale=1, delta=0, borderType)
  src     输入(通常先灰度化 + 高斯模糊)
  ddepth  输出深度;8 位输入若指定 CV_8U 会截断负梯度,官方推荐 CV_16S/CV_64F
  dx, dy  导数阶(1,0)=Gx,(0,1)=Gy
  ksize   核大小 1/3/5/7;-1 表示 3x3 Scharr
```

## 对比 / 选型

| 算子 | 阶数 | 方向性 | 特点 |
| --- | --- | --- | --- |
| Sobel | 一阶 | 有向(Gx/Gy) | 平滑+微分,噪声鲁棒,最常用第一步 |
| Scharr | 一阶 | 有向 | 3x3 时比 Sobel 更精确,同速度 |
| Laplacian | 二阶 | 无向 | `Δsrc = ∂²src/∂x² + ∂²src/∂y²`,对噪声敏感,常配 LoG |
| Canny | 多阶段 | — | Sobel 梯度 + NMS + 双阈值滞后,工业级边缘 |

## 环境准备

- 操作系统:任意(纯标准库)
- 语言版本:Python 3.8+
- 依赖:无

## 运行方式

```bash
python3 sobel.py
```

## 关键代码片段

```python
GX = ((-1, 0, 1), (-2, 0, 2), (-1, 0, 1))
GY = ((-1, -2, -1), (0, 0, 0), (1, 2, 1))

def correlate3x3(img, kernel):          # 零填充 + 相关(与 OpenCV 一致)
    for y in range(h):
        for x in range(w):
            acc = sum(img[y+dy][x+dx] * kernel[dy+1][dx+1] ...)

gx, gy = correlate3x3(img, GX), correlate3x3(img, GY)
mag[y][x]  = math.hypot(gx[y][x], gy[y][x])   # G = sqrt(Gx²+Gy²)
ang[y][x]  = math.atan2(gy[y][x], gx[y][x])   # 边缘法向
edge       = 1 if mag[y][x] >= T else 0       # 阈值二值化
```

## 性能与边界

- 单像素代价 O(k²)=9 次乘加;整图 O(W·H·k²)。分离实现可拆成 [1,2,1]×[−1,0,1] 两次一维卷积,降到 O(W·H·k)。
- 幅值上限:8 位图像、3x3 核时 |Gx| ≤ 4×255=1020,故 ddepth 至少 CV_16S。
- 零填充边界会人为制造图像四周的"假边缘",生产中常用 replicate/reflect 边界。

## 注意事项与常见坑

1. **CV_8U 截断丢边**(demo 4,官方教程 "One Important Matter"):黑→白跳变为正梯度、白→黑为负梯度;输出直接存 uint8 时负值全变 0,**整条边消失**。规避:用 CV_16S/CV_64F 计算 → 取绝对值 → 再转 uint8。
2. **Gx/Gy 命名反直觉**:dx=1(Gx)检测的是 x 方向变化,即**垂直**边缘;别按"名字里有 x 就是竖线"去记,按"求导方向"记。
3. **相关 vs 卷积符号差**:自己实现时若用真卷积(翻转核),Gx/Gy 与 OpenCV 输出正好差一个符号;梯度**幅值**不变,方向差 180°。
4. **先平滑再求导**:真实图像噪声会放大成大量伪边缘,标准管线是 GaussianBlur → Sobel(官方示例即如此)。
5. **本 demo 用 10x10 级别小图 + ASCII 渲染**,只为看清核的行为;规模无关紧要,算法逐像素同构。

## 参考资料(实际阅读过的权威来源)

- [Sobel Derivatives — OpenCV 2.4 官方教程](https://docs.opencv.org/2.4/doc/tutorials/imgproc/imgtrans/sobel_derivatives/sobel_derivatives.html) — Gx/Gy 核定义、G 组合公式、Scharr 说明(出自 *Learning OpenCV*,Bradski & Kaehler)
- [Image Gradients — OpenCV 4.x 官方教程](https://docs.opencv.org/4.x/da/d85/tutorial_js_gradients.html) — cv.Sobel 参数表、CV_8U 负梯度截断陷阱
- [Edge Detection Using OpenCV — opencv.org](https://opencv.org/platforms/edge) — 梯度幅值 √(Gx²+Gy²) 与 convertScaleAbs 的标准组合用法
