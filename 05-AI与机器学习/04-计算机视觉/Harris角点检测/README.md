# Harris 角点检测(结构张量与角点响应)

## 简介

Harris 角点检测器由 Chris Harris 与 Mike Stephens 在 1988 年的论文《A Combined Corner and Edge Detector》中提出,思路是:**把窗口沿任意方向平移 (u,v),若窗口内像素强度变化都很大,则该窗口中心是角点**。它把这条几何直觉写成二次型,再用一个不需求特征值的响应函数判角点。

关键概念:

- **结构张量 `M`**:`M = Σ w(x,y) [[Ix², IxIy], [IxIy, Iy²]]`,把窗口内的梯度分布压成一个 2×2 对称矩阵。
  - `w` 是窗口函数,OpenCV 教程原文:"either a **rectangular window** or a **Gaussian window**"。
- **响应函数 `R = det(M) − k·trace(M)²`**:`det = λ1λ2`、`trace = λ1+λ2`,所以 `R` 只由特征值决定,不必真的解特征值。
- **特征值的几何含义**:λ1、λ2 都小 → 平坦区;一个远大于另一个 → 边缘;两个都大且接近 → 角点。
- **`k`**:经验常数,OpenCV 教程示例取 **0.04**(`cv.cornerHarris(gray, 2, 3, 0.04)`),取值区间 0.04~0.06。
- **`blockSize`**:教程说明它是"corner detection 考虑的邻域大小",即窗口半径。

## 原理详解

### 从 E(u,v) 到 R

```
E(u,v) = Σ_{x,y} w(x,y) [ I(x+u, y+v) - I(x,y) ]²          # 平移窗口的强度变化

   Taylor 展开 + 一阶近似
        ▼
E(u,v) ≈ [u v] M [u v]ᵀ ,   M = Σ_{x,y} w(x,y) [ Ix²   IxIy ]
                                               [ IxIy  Iy²  ]
        ▼
R = det(M) - k * trace(M)² = λ1λ2 - k(λ1+λ2)²
```

判别表(教程原文的三种情形):

| 情形 | 特征值 | R | 结论 |
| --- | --- | --- | --- |
| 平坦区 | λ1、λ2 都很小 | \|R\| 小 | flat |
| 边缘 | λ1 ≫ λ2(或反之) | **R < 0** | edge |
| 角点 | λ1、λ2 都大且 λ1 ≈ λ2 | **R 很大且 > 0** | corner |

### R < 0 有闭式边界,不是"边缘总是负"

令 `r = λ1/λ2 ≥ 1`,则 `R = λ2²·(r − k(r+1)²)`。`R > 0` 等价于

```
r < (1 - 2k + sqrt(1 - 4k)) / (2k)
```

代入 `k = 0.04` 得上界 **≈ 22.9564**;`k = 0.06` 降到 **≈ 14.5982**,`k = 0.02` 升到 **≈ 47.9792**。也就是说:

- `r = 22` 的结构在 `k=0.04` 下仍是"角点",`k=0.06` 下已被判成"边缘"(自检 B 段实测 `2.360 → −6.460`);
- **`k` 越大 = 对边缘抑制越强 = 角点更"纯"但更容易漏**。这就是"k 是敏感度旋钮"的定量含义。

### 结构张量必为半正定

`M` 是若干 `[Ix, Iy]ᵀ[Ix, Iy]` 的加权和,因此 `λmin ≥ 0` 恒成立(等价于 Cauchy–Schwarz:`Sxx·Syy − Sxy² ≥ 0`)。自检 C/D 段在整幅棋盘图上验证了这一点,数值上 `min λ2 = 0`(理想阶梯边缘恰好秩 1)。这条性质决定了 **Shi-Tomasi 的响应永远不会是负数** ——

### Shi-Tomasi(`λmin`)与 Harris 的判据差异

`goodFeaturesToTrack` 用的判据是 `R = min(λ1, λ2)`,直接取小特征值。差别不只是换了个函数:

| 结构 | Harris `R` | `min(λ1,λ2)` | 谁会检出 |
| --- | --- | --- | --- |
| λ=(100,1) 的强边缘 | **−308.04** | **1.0** | Harris 拒绝;`λmin` 给出正响应 |
| 理想阶梯边缘(λ2=0) | R<0 | **0** | 都不检(前提是阈值 > 0) |
| 理想棋盘角点 | 2.697e10 | **179200** | 都检 |

所以 `λmin` **不能用"R > 0"当判据**,必须显式给一个阈值(它没有天然的零点)。

### 核心 API(cv.cornerHarris)

```
void cornerHarris(InputArray src, OutputArray dst, int blockSize, int ksize, double k,
                  int borderType = BORDER_DEFAULT)
```

- `src`:灰度、**float32**。教程明确要求先 `cvtColor` 转灰度再 `np.float32()`;传 uint8 会得到被截断的弱响应。
- `blockSize`:邻域大小(窗口半径)。
- `ksize`:Sobel 求导的孔径,**必须奇数**;本 demo 只实现 3(与教程示例一致)。
- `k`:响应函数里的自由参数。
- `dst`:与 `src` 同尺寸的响应图,值可正可负。

教程随后演示的落地点是两件事:① `dst` 先 `cv.dilate` 再按 `0.01*dst.max()` 阈值化;② 需要高精度时用 `cv.cornerSubPix()` 做**亚像素精化**(先取每个角点簇的质心,再迭代精化)。

## 环境准备

- 操作系统:任意(无平台相关代码)
- Python 3.8+(**仅标准库**)
- Go 1.16+(仅标准库)

## 运行方式

### Python

```bash
cd python
python3 harris_check.py     # 49 项断言
```

`harris.py` 是库:`response_image(灰度图, block_size, ksize, k, mode, method) -> 响应图`。

### Go

```bash
cd go
go run .          # Go 1.21+(go.mod 已随目录提供)
```

`go/` 下两个同包文件:`harris.go`(实现)+ `main.go`(断言与入口)。

## 关键代码片段

```python
def structure_tensor(gx, gy, block_size=2, mode="rect", sigma=None):
    """M 的三个分量 = Σ w * (Ix², IxIy, Iy²),w 归一化到总和 1。"""
    w = window_weights(block_size, mode, sigma)
    r = block_size
    for y in range(h):
        for x in range(wd):
            a = b = c = 0.0
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    wv = w[dy + r][dx + r]
                    ixx, iyy = gx[y + dy][x + dx], gy[y + dy][x + dx]
                    a += wv * ixx * ixx          # Sxx
                    b += wv * ixx * iyy          # Sxy
                    c += wv * iyy * iyy          # Syy
            sxx[y][x], sxy[y][x], syy[y][x] = a, b, c
```

```python
def eigenvalues(sxx, sxy, syy):
    """对称 2x2 的闭式特征值:mid ± sqrt(((a-c)/2)² + b²)。"""
    mid = 0.5 * (sxx + syy)
    rad = math.sqrt((0.5 * (sxx - syy)) ** 2 + sxy * sxy)
    return mid + rad, mid - rad           # (λmax, λmin)
```

```python
def response_from_tensor(sxx, sxy, syy, k=0.04, method="harris"):
    if method == "harris":
        return sxx * syy - sxy * sxy - k * (sxx + syy) ** 2      # det - k*tr²
    return min(eigenvalues(sxx, sxy, syy))                        # Shi-Tomasi
```

## 性能与边界

- 复杂度:`O(H·W·(2r+1)²)`,`r = blockSize`。`blockSize=2` 时每像素 25 次窗口累加,Sobel 另加 18 乘加。工程实现里窗口求和走 `boxFilter`(可分离,`O(H·W)`),本 demo 用朴素卷积以便逐项可验。
- 内存:`Sxx/Sxy/Syy` 三张与图像同尺寸的浮点图(响应图可复用其一)。
- 数值范围:响应随对比度的 **4 次方**缩放 —— 图像整体乘 `c`,则 `Ix` 乘 `c`、`M` 乘 `c²`、`det` 与 `tr²` 都乘 `c⁴`,故 `R` 乘 `c⁴`。本 demo 实测 `c=2` 时比值**恰为 16.000000000000**。
- 尺度:Harris **不具备尺度不变性**。窗口是固定像素尺寸,把同一图案放大 4× 后,同 `blockSize` 下的响应峰并不落在放大后的原位置(实测 argmax 从 `(8,8)` 移到 `(42,33)`,簇数从 1 变成 3)。
- 旋转:响应是旋转不变的(特征值不随 `M` 的正交变换改变),自检 H 段在 1 像素宽 L 角上验证 90° 旋转后**逐像素差为 0**。

## 注意事项与常见坑

1. **理想角点周围是一整块"响应平台",不是单点。**
   33×33 棋盘的理想角点上,矩形窗下 `R` 在 4×4=16 个像素上**完全相同**(实测 2.69746e10 ×16)。朴素邻域 NMS(严格比较)一个也删不掉,直接把 16 个点全当角点输出;换成"`>=` 比较"又会连带删掉真实峰。OpenCV 教程其实明说了这件事 —— "There may be a bunch of pixels at a corner, **we take their centroid**",并给出 `connectedComponentsWithStats` 取质心的做法。本 demo 的 `cluster_candidates` 就是这一步,实测把 16 个候选并成 1 个簇;**注意质心是 (15.5, 15.5),比边界所在像素 (16,16) 偏半个像素**,这是"取质心"近似的系统偏差。
2. **阈值必须相对 `max` 取,绝对阈值不可移植。**
   响应随对比度的 4 次方缩放,所以"R > 1000 算角点"这种写法换一张图就失效。自检 E 段实测:把阈值写死成 `1.2 × 原图 max`,原图检出 **0** 个像素、而对比度 ×2 的图上检出 **36** 个;改用 `0.01 × max` 的相对阈值,两张图给出**同一个角点集合**。
3. **`k` 不是越大越好,它有闭式上限。**
   `k=0.06` 时 `r_max ≈ 14.6`,意味着任何 `λ1/λ2 > 14.6` 的结构(相当多真实边缘)都会被划成"边缘"而漏检;`k=0.02` 时上界升到 48,又会把大量边缘算成角点。典型值 0.04 是这两头的折中,不是"精度参数"。
4. **忘记转 float 会得到"看起来很弱"的响应。**
   教程示例里两步是连着的:`gray = cvtColor(...)` 之后紧跟 `gray = np.float32(gray)`。用整型运算时 `det − k·tr²` 会因截断/溢出失真,`0.01*max` 的阈值也就失去意义。
5. **Harris 尺度不敏感、旋转不变 —— 两个性质不要混为一谈。**
   旋转不变来自特征值的正交不变性(精确);尺度不敏感是因为窗口固定。要求尺度不变就得往上走 SIFT 那套尺度空间(见 `../SIFT尺度空间/`)。
6. **`ksize` 必须奇数**,且 `blockSize` 与 `ksize` 是两个独立概念:`blockSize` 决定"窗口多大",`ksize` 决定"梯度用多大孔径算"。二者都调大会同时增加计算量与平滑度,漏掉细角点。

## 参考资料(实际阅读过的权威来源)

- [OpenCV 4.x — Harris Corner Detection(tutorial_py_features_harris)](https://docs.opencv.org/4.x/dc/d0d/tutorial_py_features_harris.html) — `E(u,v)` 与 Taylor 展开、`M` 的矩阵形式、"矩形窗或高斯窗"的原文表述、`R = det(M) − k·trace(M)²`、三种情形的特征值判别、`cv.cornerHarris` 参数(含 `src` 必须 float32)与示例 `k=0.04`、`cornerSubPix` 前"取角点像素质心"的做法。
- [Harris & Stephens 1988, *A Combined Corner and Edge Detector*](https://www.sciencedirect.com/science/article/pii/B9780080515816500241) — 响应函数与角点/边缘/平坦三分判据的原始出处(经 OpenCV 教程引述,本 demo 的公式与之一致)。
- 本 demo 自身实测(自检 49 项断言):`r_max(k)` 的闭式上界、平台大小 16 像素、簇质心偏移半像素、对比度 ×2 → 响应 ×16、尺度放大 4× 后峰位与簇数变化。
