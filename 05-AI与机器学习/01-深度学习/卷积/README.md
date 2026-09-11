# 2D 卷积 · im2col forward + backward

## 简介

2D 卷积是 CNN 的核心算子,但朴素的 7 重嵌套循环实现无法利用 CPU/GPU 的向量化能力。**im2col (image-to-column)** 把卷积 → **GEMM (矩阵乘)**,从而把"逐元素乘 + 求和"变成 BLAS 优化的矩阵乘,这是 PyTorch / TensorFlow / cuDNN 在 CPU 上的卷积底层技术。本 demo 用 NumPy `np.lib.stride_tricks` 实现零拷贝的 im2col 视图,并完整写出反向传播(col2im 累加回输入、权重梯度、bias 梯度),用数值梯度做正确性验证。

- **关键概念**:
  - **im2col (image-to-column)**:把每个卷积窗口展平成 col 矩阵的一行,输入 `(N, C, H, W)` → col `(C·kh·kw, N·Hout·Wout)`。
  - **col2im (column-to-image)**:im2col 的逆操作,把 col 矩阵的列分散回输入位置,**多个窗口共享同一像素时梯度累加**(用 `+=` 而非 `=`)。
  - **GEMM-based convolution**:核心是把卷积写成 col_W · cols = col_Y,所有计算变成矩阵乘,BLAS 优化后比朴素循环快几十倍。
  - **stride/padding/output shape**:`Hout = (H + 2P − kh) / S + 1`(整除),`pad = (kh-1)/2` 对应 "same" padding。
- **历史背景**:im2col 由 Chellapilla et al. 2006 *High Performance Convolutional Neural Networks for Document Processing* 提出,后被 Caffe / PyTorch 广泛采用。后续研究 (Winograd、FFT、direct convolution) 进一步降 FLOPs,但 im2col 仍是教学与中小网络的标配。

## 原理详解

依据 arXiv 2408.12561 (ssProp) §Preliminaries + arXiv 2009.00612 (Operational vs Convolutional),im2col 把卷积 → GEMM 的完整数据流如下。

### 1. 前向 im2col 数据流

```text
X (N, Cin, H, W) ─── im2col ──→  cols (Cin·kh·kw, N·Hout·Wout)
                                    │
W (Cout, Cin, kh, kw) ──reshape──→ W_col (Cin·kh·kw, Cout)
                                    │
                                    ▼
                          Y_col = W_col^T · cols    (Cout, N·Hout·Wout)
                          Y_col + b (broadcast)
                                    │
                          reshape + transpose
                                    ▼
                          Y (N, Cout, Hout, Wout)
```

### 2. 卷积窗口的几何示意

以 4×4 输入 + 3×3 核 + stride=1 + pad=0 为例,9 个输出位置每个对应一个 3×3 窗口,im2col 把 9 个窗口堆成 9×9 矩阵:

```text
输入 (4×4):            im2col (9×9):
 1  2  3  4             [1,2,5,6, 2,3,6,7, 3,4,7,8]   pos(0,0)
 5  6  7  8             [5,6,9,10,6,7,10,11,7,8,11,12] pos(1,0)
 9 10 11 12             [9,10,13,14,10,11,14,15,11,12,15,16] pos(2,0)
13 14 15 16             [...窗口(1,1)...]                ...
                       [...窗口(2,2)...]
                       9 行 × 9 列 (3×3 = 9 元素/窗口)
```

权重展平为 `(1, 9)` 行向量,做一次矩阵乘 `cols @ W_col.T → (9, 1)`,再 reshape 回 `3×3`。

### 3. 反向传播(对应 arXiv 2408.12561 Eq 3, 4, 5)

| 梯度 | 公式 (本文记号) | 实现 |
| --- | --- | --- |
| `dW` | $\sum_b \sum_i \sum_j X_{b,p,i+sm,j+sn} \cdot \partial L/\partial Y_{b,q,i,j}$  (Eq 3) | `dW_col = cols @ dY_col.T` |
| `db` | $\sum_b \sum_i \sum_j \partial L/\partial Y_{b,q,i,j} \cdot 1$  (Eq 5) | `db = dY_col.sum(axis=1)` |
| `dX` | $\sum_q \sum_m \sum_n \partial L/\partial Y_{b,q,i-m,j-n} \cdot W_{q,p,m,n}$  (Eq 4) | `dcols = W_col @ dY_col` 再 `col2im` |

### 4. 反向 col2im 的累加细节

同一输入像素被多个输出窗口共享(滑窗重叠),所以 col2im 必须**累加**而非赋值:

```text
输入像素 X[1,1] 被 4 个输出窗口用到:
  窗口 (0,0) 的右下角
  窗口 (0,1) 的左下角
  窗口 (1,0) 的右上角
  窗口 (1,1) 的左上角

→ dX[1,1] = dY[0,0] + dY[0,1] + dY[1,0] + dY[1,1]   (累加!)
```

这就是为什么 col2im 必须 `+=` 而非 `=`(朴素实现用 `np.add.at`;本 demo 用 stride 视图 + `+=` 等价)。

### 5. 核心 API

- `im2col(X, kh, kw, stride, pad)` → `(C·kh·kw, N·Hout·Wout)`,**零拷贝**(stride_tricks 视图)
- `col2im(cols, X_shape, kh, kw, stride, pad)` → `(N, C, H, W)`,带 pad 移除
- `Conv2D(in_channels, out_channels, kh, kw, stride, pad, seed)` — 卷积层
  - `forward(X)` → `Y`,并缓存 `cols / W_col / X_shape`
  - `backward(dY)` → `dX`,同时设置 `self.dW, self.db`
- `numerical_grad_dW(W, X, dY, kh, kw, stride, pad, eps=1e-5)` — 中心差分验证 dW

## 对比 / 选型

| 实现方式 | 速度 (CPU) | 速度 (GPU) | 内存 | 复杂度 |
| --- | --- | --- | --- | --- |
| **朴素 7 重循环** | 极慢 | — | $O(1)$ | 易 |
| **im2col + GEMM (本 demo)** | 快 (×10 ~ ×100) | 快 (cuBLAS GEMM) | $O(k^2)$ 倍膨胀 | 中 |
| **Winograd** (F(2×2, 3×3)) | 中 | 极快 | $O(k^2)$ | 难 |
| **FFT-based** | 中 | 中 | $O(N \log N)$ | 难 |
| **Direct / OneDNN** | 快 | 快 | $O(1)$ | 难 |

> ⚠️ **im2col 的代价**:col 矩阵比原输入膨胀约 $k^2$ 倍(`k=3` → ×9,`k=5` → ×25)。极端大核 / 大特征图时需用 Winograd 或 oneDNN direct 算法规避。生产框架通常根据 `k` / `C` / `H·W` 自动选算法。

## 环境准备

- Python 3.13+(实测 3.13.12)
- 依赖:`numpy`

## 运行方式

```bash
python conv2d.py
```

预期输出(关键行):

```text
Test 1: Conv2D forward + numerical dW check
  X: (2, 3, 5, 5)  →  Y: (2, 4, 5, 5)  (Hout=5, Wout=5)
  dW max |ana - num| = 3.4e-12

Test 4: col2im 多次累加 (kernel 滑窗重叠处正确累加)
  col2im 还原:
    期望: [[1. 2. 3.] [4. 5. 6.] [7. 8. 9.]]
    实际: [[1. 2. 3.] [4. 5. 6.] [7. 8. 9.]]
```

## 关键代码片段

**im2col 零拷贝视图**(`conv2d.py`):

```python
def im2col(X, kh, kw, stride, pad):
    N, C, H, W = X.shape
    Hout = (H + 2 * pad - kh) // stride + 1
    Wout = (W + 2 * pad - kw) // stride + 1
    if pad > 0:
        X = np.pad(X, ((0,0),(0,0),(pad,pad),(pad,pad)), mode='constant')
    sN, sC, sH, sW = X.strides
    cols = np.lib.stride_tricks.as_strided(
        X,
        shape=(N, C, kh, kw, Hout, Wout),
        strides=(sN, sC, sH, sW, stride*sH, stride*sW),
    )
    return cols.transpose(0, 4, 5, 1, 2, 3).reshape(N*Hout*Wout, -1).T
```

**Conv2D forward (GEMM 形式)**(`conv2d.py`):

```python
def forward(self, X):
    cols = im2col(X, self.kh, self.kw, self.stride, self.pad)  # (C·kh·kw, N·Hout·Wout)
    W_col = self.W.reshape(self.W.shape[0], -1)                 # (Cout, C·kh·kw)
    Y_col = W_col @ cols + self.b.reshape(-1, 1)                # (Cout, N·Hout·Wout) ← GEMM
    Y = Y_col.reshape(self.W.shape[0], N, Hout, Wout).transpose(1, 0, 2, 3)
    self.cache = dict(X_shape=X.shape, cols=cols, W_col=W_col.T)
    return Y
```

**Conv2D backward (Eq 3 + 4 + 5)**(`conv2d.py`):

```python
def backward(self, dY):
    N, C_out, Hout, Wout = dY.shape
    dY_col = dY.transpose(1, 0, 2, 3).reshape(C_out, -1)
    # Eq 3: dW = cols @ dY_col.T
    self.dW = (self.cache['cols'] @ dY_col.T).T.reshape(self.W.shape)
    # Eq 5: db = Σ dY
    self.db = dY_col.sum(axis=1)
    # Eq 4: dX = W_col @ dY_col, then col2im (累加!)
    dcols = self.cache['W_col'] @ dY_col
    return col2im(dcols, self.cache['X_shape'], self.kh, self.kw, self.stride, self.pad)
```

## 性能与边界

- **精度**:数值梯度中心差分精度 $O(\epsilon^2)$,本 demo 取 $\epsilon = 10^{-5}$,测得 dW max err ≈ $10^{-12}$。
- **复杂度**:前向 1 次 GEMM:`(Cout, C·kh·kw) × (C·kh·kw, N·Hout·Wout)` = $O(\text{Cout} \cdot C \cdot kh \cdot kw \cdot N \cdot Hout \cdot Wout)$。反向 ≈ 3 次 GEMM + 1 次 col2im 累加。
- **内存峰值**:col 矩阵 ≈ $\text{C} \cdot kh \cdot kw \cdot N \cdot Hout \cdot Wout$ 浮点数,通常比 Y 大 `kh·kw` 倍(`k=3` → 9×)。
- **规模边界**:本 demo 用 `N=2, C=4, H=W=5` 在 CPU 上 < 0.1 s。生产规模如 `N=32, C=64, H=W=56`,需要 ≥ 1 GB 临时内存。
- **跨平台**:stride_tricks 在 Linux / macOS / Windows 上行为一致,因 NumPy strides 是字节单位。

## 注意事项与常见坑

1. **col2im 必须累加,不能赋值**:`Xp[i, j] += cols_reshaped[k]`,因为同一输入像素被多个输出窗口共享(滑窗重叠)。赋值会丢梯度。
2. **stride 不能整除时的 floor**:用 `//`(整除)而非 `/`(浮点除),`H + 2P - kh < 0` 时 Hout 会变负;务必提前 `assert H + 2P >= kh`。
3. **im2col 视图只读**:stride_tricks 构造的数组**不拥有数据**,修改它会改原 X。`im2col` 后若要修改 X,应 `.copy()`。
4. **weight reshape 顺序**:PyTorch / Caffe 的 W 形状是 `(Cout, Cin, kh, kw)`,本 demo 与之一致;`.reshape(Cout, -1)` 是把后三维压成一维,**保证和 im2col 的 `Cin·kh·kw` 列顺序一致**。
5. **输出 layout**:本 demo 输出 `(N, Cout, Hout, Wout)`(channels-first, PyTorch 风格);TF/Keras 是 `(N, Hout, Wout, Cout)`(channels-last)。做反向时 `dY.transpose(1, 0, 2, 3)` 顺序要跟着变。
6. **大核代价膨胀**:`k=7` 时 im2col 矩阵膨胀 49 倍,显存吃紧,应改 Winograd / FFT。
7. **grad_bias 求和维度**:`(N, Hout, Wout)` 三维都要 sum,只 sum 一个会留维度。
8. **CUDNN 真实算法**:PyTorch / TF GPU 实际跑的是 cuDNN 的 `IMPLICIT_GEMM` / `WINOGRAD` / `FFT`,**不是** im2col+cuBLAS。im2col 是教学与 CPU 实现的标准答案。

## 参考资料(实际阅读过的权威来源)

- [ssProp: Energy-Efficient Training for Convolutional Neural Networks with Scheduled Sparse Back Propagation — arXiv 2408.12561 §Preliminaries](https://arxiv.org/html/2408.12561v1) — im2col 形状 `(Bt·Hout·Wout, Cin·K·K)` + col_W `(Cin·K·K, Cout)` + 反向 Eq 3/4/5 完整推导(本 demo 的直接依据)
- [Operational vs Convolutional — arXiv 2009.00612](https://arxiv.org/pdf/2009.00612v1) — 把卷积写成 $\psi(Y, W)$ 节点函数 + $\varphi(\cdot)$ 池函数的形式化(Eq 2, 7, 8, 9, 10),以及更通用的"操作 vs 卷积"神经元对比
- [Layer Operations — DeepWiki a1henu/tinytorch](https://deepwiki.com/a1henu/tinytorch/4.3-layer-operations) — 真实开源项目 tinytorch 的 Conv2D 实现细节,确认 `grad_weight = grad_output @ col^T` + `col2im_op` 反向的标准顺序
- [Realization of reverse propagation of convolutional layers — ProgrammerSought](https://programmersought.com/article/598411652563/) — 鱼书《深度学习入门:基于 Python 的理论与实现》第 7 章的 Convolution/Pooling backward Python 实现,直接对应 Eq 3/4/5 的代码化
- [Stanford CS231n Convolutional Neural Networks](https://cs231n.github.io/convolutional-networks/) — im2col 在课程笔记中的标准讲法 + 数值梯度 `loss = sum(dout * Y)` 的标量化技巧
