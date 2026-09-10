# Z-Buffer（深度缓冲）与 Z-Fighting

## 简介

Z-Buffer（深度缓冲）是实时渲染中解决"哪个片元离相机更近"的**逐像素可见性算法**：为每个像素额外存一个深度值，光栅化时比较新旧深度，近者胜出。本 demo 用纯软件光栅化（C / Python / Go 各一份，ASCII 帧缓冲输出）复现该算法，并演示两个经典问题——画家算法无法处理的互相贯穿三角形，以及定点深度精度不足导致的 Z-Fighting 条带。

**关键概念**

- **深度测试（Depth Test）**：片元深度 `d` 与缓冲中已存值比较，`d < old`（即 GL_LESS）才写入颜色与深度
- **透视校正深度**：透视除法后 **1/z 在屏幕空间是线性的**，对顶点 1/z 做重心插值即可得到正确深度
- **量化（Quantization）**：定点深度缓冲把 [0,1] 映射到 `2^bits - 1` 个整数，一个量化步长就是精度的物理边界
- **Z-Fighting（深度冲突）**：两个面深度差小于一个量化步长时，胜负由舍入决定，出现条纹 / 闪烁 / 抖动
- **精度前重后轻**：透视投影下深度缓冲实际存储的是 ~1/z 的映射，近处精度高、远处精度低

**历史**：Z-Buffer 思想最早由 Wolfgang Straßer 在 1974 年博士论文中描述，同年 Edwin Catmull 也独立提出；因实现简单、与绘制顺序无关，成为现代 GPU 的标准可见性方案（Wikipedia: Z-buffering）。

## 原理详解

### 1. 算法主循环（Marburg 大学讲义伪代码）

```text
初始化: depth[x][y] = 1.0（最远），color[x][y] = 背景色
for each 三角形:
    for each 覆盖的像素 (x,y)，插值出深度 d:
        if d < depth[x][y]:
            color[x][y] = 片元颜色
            depth[x][y] = d
```

### 2. 深度编码：为什么是 1/z

透视投影矩阵变换后做透视除法，Khronos OpenGL Wiki 给出眼空间到 NDC 的深度公式：

```text
z_ndc = (f+n)/(f-n) + 2·f·n / (z_eye · (f-n))
```

即 NDC 深度是 **z_eye 的倒数函数**——这就是"深度缓冲本质存 1/z"（UBC 讲义）的出处。本 demo 采用等价的归一化形式：

```text
depth(z) = (1/z - 1/n) / (1/f - 1/n)      # z=n → 0（最近），z=f → 1（最远）
```

### 3. 精度：一个量化步长对应多远？

对上式求导，`2^bits - 1` 级定点缓冲的一个步长对应的眼空间距离：

```text
Δz ≈ z² · (f - n) / (f · n · (2^bits - 1))
```

特征：**Δz 随 z² 增长**，远处精度平方级恶化。两条权威经验法则：

- OpenGL 蓝皮书（Khronos Wiki 引述）：整个缓冲约损失 `log2(f/n)` 位精度，近面逼近 0 时损失趋于无穷
- Khronos Wiki 反例：n=0.01、f=1000、16-bit 时，z 从 395.9 到 1000 的**全部**深度只能落到 65534 / 65535 两个值上

### 4. 两个演示场景

```text
场景 A：互相贯穿的三角形（T1、T2 平均深度相同）
  画家算法：按平均深度排序 → 后画者整体覆盖 → 贯穿处错误 ✗
  Z-Buffer：逐像素比较 → 正确的相交线 ✓

场景 B：近平行三角形对（D = C + 0.3 个世界单位，全场恒定）
  浮点 / 24-bit：量化步长 << 0.3 → D 全胜 ✓
  16-bit：远处步长 > 0.3 → 胜负由舍入决定 → Z-Fighting 条带 ✗
```

光栅化流程（三语言实现一致）：

```text
顶点（相机空间）──透视投影──▶ 屏幕坐标
      │                         │
      └─ 顶点 1/z ─┐            ▼
                  │    包围盒遍历像素
                  └──▶ 重心坐标插值 1/z ──▶ z = 1/iz
                                        │
                                        ▼
                      深度测试 d < depth[y][x] ──▶ 写颜色 + 写深度
```

## 对比 / 选型

| 可见性方案 | 复杂度 | 与绘制顺序 | 贯穿多边形 | 备注 |
| --- | --- | --- | --- | --- |
| 画家算法（深度排序） | O(n log n) 排序 | 强依赖 | ✗ 无法处理 | 循环遮挡还需拆分多边形 |
| BSP 树 | 建树昂贵 | 预计算后无关 | ✓ | 适合静态几何 |
| **Z-Buffer** | O(片元数) | **无关** | ✓ | 硬件标配，代价是显存带宽 |

| 深度格式 | 远处精度 | 适用 |
| --- | --- | --- |
| 16-bit 定点 | 差（远处 Z-Fighting 高发） | 老硬件 / 早期移动端 |
| 24-bit 定点 | 通常够用（UBC 讲义保守法则：f:n < 1000） | 桌面默认 |
| 浮点 / Reverse-Z | 远处也高（浮点数在 0 附近精度最高） | 现代引擎（GTA V 采用 Reverse-Z，见 Wikipedia: Z-fighting） |

## 环境准备

- 操作系统：任意（纯控制台输出，无图形依赖）
- C：gcc / clang（C99+），链接 libm
- Python：3.8+，无第三方依赖
- Go：1.21+（使用内建 min/max），无依赖

## 运行方式

### C

```bash
gcc -O2 -Wall -Wextra zbuffer.c -lm -o zbuffer
./zbuffer
```

### Python

```bash
python3 zbuffer.py
```

### Go

```bash
go run zbuffer.go
```

## 关键代码片段

（Python 版）深度编码与测试——注意对 1/z 线性：

```python
def to_buffer(self, z):
    # z=NEAR → 0（最近），z=FAR → 1（最远）；对 1/z 线性
    d = (1.0 / z - 1.0 / NEAR) / (1.0 / FAR - 1.0 / NEAR)
    if self.bits:                              # 定点量化
        return round(d * ((1 << self.bits) - 1))
    return d

def put(self, x, y, z, tag):
    d = self.to_buffer(z)
    if d < self.depth[y][x]:                   # GL_LESS：严格小于才写
        self.depth[y][x] = d
        self.color[y][x] = tag
```

（Python 版）重心插值 1/z——透视校正的关键，对应"原理详解"第 2 节：

```python
# e0/e1/e2 为三个顶点的（未归一化）重心权重，inside 判定为三者符号与面积一致
iz = (e0 * inv[0] + e1 * inv[1] + e2 * inv[2]) / area
z = 1.0 / iz    # 屏幕空间线性插值 1/z，取倒数还原相机空间深度
```

## 性能与边界

- 深度测试本身 O(1)/片元，代价在**带宽**：1999–2005 年 PC 显卡上 Z-Buffer 管理占显存带宽的显著比例，因此发展出无损压缩与快速 clear（Wikipedia: Z-buffering）
- 精度边界（本 demo 参数 n=1、f=400）：16-bit 下 z=100 处 Δz≈0.15，z=300 处 Δz≈1.37——所以场景 B 中 0.3 的深度差在远处"打不动"测试；24-bit 下 z=300 处 Δz≈0.005，轻松分辨
- **near 面是精度第一杀手**：zNear 从 1.0 缩到 0.01，全缓冲精度约损失 log2(100) ≈ 6.6 bit（Khronos：近面影响远大于远面）

## 注意事项与常见坑

1. **多边形"透过"前面的面** → 九成是 zNear 设得太小（Khronos FAQ：近面逼近 0 时精度"急剧崩塌"）。规避：把 near 推远、far 拉近，让裁剪范围恰好包住场景
2. **共面几何闪烁**（贴花、阴影接收面） → 纯精度问题无法根除，用 polygon offset / stencil / 把面稍微移开
3. **比较规则影响结果**：本 demo 用 GL_LESS（严格小于）；用 GL_LEQUAL 时共面后画者胜——两种都合法，但输出不同，多 pass 渲染时尤其要注意
4. **绘制顺序仍可能造成细微差异**：共享边两侧的光栅化不一致（UBC 讲义指出），可请求 invariant vertex transformation 缓解
5. **超大场景**（太空 / 飞行模拟）单一 near/far 无法兼顾远近：多 pass 分区渲染（逐区 clear 深度）、浮点 / Reverse-Z / 对数深度

## 参考资料（实际阅读过的权威来源）

- [Depth Buffer Precision — Khronos OpenGL Wiki](https://www.khronos.org/opengl/wiki/Depth_Buffer_Precision) — 官方 FAQ：眼空间→窗口坐标深度公式全文推导、log2(zFar/zNear) 经验法则、n=0.01/f=1000 的 16-bit 反例、浮点深度最优性论证
- [Graphics Programming Cameras: Perspective Projection — Philipps-Universität Marburg（Wolfram Gothe）](https://www.mathematik.uni-marburg.de/~thormae/lectures/graphics1/graphics_6_1_eng_web.html) — 大学讲义：Z-Buffer 伪代码、NDC 深度与透视除法、Z-Fighting 成因与 near/far 选择
- [Visible Surface Determination 讲义 — UBC CPSC 414（Tamara Munzner）](https://www.cs.ubc.ca/~tmm/courses/cpsc414-03-fall/Vsep2003/slides/week9.fri.ppt) — Z-Buffer 算法与画家算法/BSP/Warnock 对比、"深度缓冲本质存 1/z"、f:n 比例经验法则、A-Buffer 扩展
- [Z-buffering — Wikipedia](https://en.wikipedia.org/wiki/Z-buffering) — 历史（Straßer 1974 / Catmull 1974）、16→24-bit 演进、带宽与压缩、w-buffer 变体（本文经由搜索快照阅读其镜像全文）
- [Z-fighting — Wikipedia](https://en.wikipedia.org/wiki/Z-fighting) — 现象定义、共面多边形、缓解手段（更高位数深度、polygon offset、stencil、Reverse-Z 见 GTA V 图形研究）
