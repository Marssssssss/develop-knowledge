# IVF-PQ：倒排粗量化 + 乘积量化（Faiss）

## 一、简介

当向量规模从"能放进内存"变成"放不进内存"，暴力近邻就不够了。Faiss 的经典组合是 **IVF（倒排文件）做粗量化 + PQ（乘积量化）做压缩编码**：

- **IVF** 把向量空间切成 `nlist` 个 Voronoi 单元，查询时只探 `nprobe` 个单元 ⇒ 检索从穷举变成**非穷举**；
- **PQ** 把 `d` 维向量切成 `M` 段、每段用 `ksub` 个中心量化 ⇒ 每个向量压成几个字节，距离还能用**查表累加**算出来。

本 demo 用纯标准库把这套结构、两种距离口径（ADC / SDC）和 `nprobe` 的召回-代价权衡跑通。

## 二、原理详解

### 2.1 IVF：只扫 `nprobe` 个倒排列表

`IndexIVF.h` 的原文：

> At search time, the vector to be searched is also quantized, and **only the list corresponding to the quantization index is searched**. This speeds up the search by making it non-exhaustive. This can be relaxed using multi-probe search: a few (**nprobe**) quantization indices are selected and several inverted lists are visited.

- `nlist`：粗量化中心（= 倒排列表）个数；
- `nprobe`：**默认 1**，即默认只探最近的那一个列表。

`nprobe` 是召回与延迟之间唯一的旋钮：

```text
nprobe=1   召回@10=0.425  平均扫过  5.1% 的库
nprobe=2   召回@10=0.490  平均扫过  7.7% 的库
nprobe=4   召回@10=0.515  平均扫过 13.8% 的库
nprobe=8   召回@10=0.530  平均扫过 21.9% 的库
nprobe=16  召回@10=0.530  平均扫过 40.7% 的库
nprobe=32  召回@10=0.530  平均扫过 100%  的库   ← = nlist，退化为穷举
```

两条必须记住的结论：

1. `nprobe` 越大召回**单调不降**（多探列表只会多给候选）；
2. **`nprobe = nlist` 时召回也到不了 1.0** —— 因为 PQ 是有损的，它给召回设了**天花板**；想突破只能换更好的量化（更大的 `nbits`、更多 `M`）或加 refine 步骤重排。

### 2.2 PQ：切段 + 每段 k-means

`ProductQuantizer.h` 的参数关系：

| 符号 | 含义 |
| --- | --- |
| `M` | 子量化器个数（把 `d` 维切成 `M` 段） |
| `nbits` | 每个子索引的位数 |
| `dsub = d / M` | 每段维度 |
| `ksub = 2^nbits` | 每段的中心数 |
| centroids 布局 | `(M, ksub, dsub)`，另有转置副本 `(dsub, M, ksub)` 与 `centroids_sq_lengths (M, ksub)` |

码字大小 = `ceil(M × nbits / 8)` 字节。例：`d=16, M=4, nbits=4` ⇒ 2 字节，相对 float32 的 64 字节是 **32×** 压缩。常见 `nbits=8` 时由 `PQEncoder8` 直接按字节打包，`nbits` 非 8 的倍数时用 `PQEncoderGeneric` 按位打包。

类注释还提醒了一点：**PQ 用 k-means 训练、最小化 L2 距离，因此"偏向 L2"**——内积/余弦场景要额外处理（归一化或换量化器）。

### 2.3 编码的是**残差**，不是原向量

`IndexIVFPQ.h`：*"Inverted file with Product Quantizer encoding. Each **residual** vector is encoded as a product quantizer code."*

即 `code = PQ(x − 粗量化中心)`，`x ≈ 粗量化中心 + decode(code)`。这一步是 IVFPQ 比裸 PQ 准得多的关键：残差的方差远小于原向量，同样的码长能表达得更细。

### 2.4 距离：ADC 与 SDC

**ADC（Asymmetric Distance Computation）**：查询端不量化，先算一张 `M × ksub` 的距离表

```text
dis_table(m, j) = ||x_m − c_(m,j)||²     m = 0..M-1, j = 0..ksub-1
```

然后对库里的每个码字做**查表累加**：

```text
ADC(x, y) = Σ_m  dis_table(m, code_y[m])
```

由构造可知它**恒等于** `||r − decode(code_y)||²`（本 demo 断言两者差 < 1e-9，是逐块可加的恒等式，不是近似）。整个扫描过程只有整数查表 + 加法，没有浮点向量运算——这才是 PQ 快的根本原因。

**SDC（Symmetric Distance Computation）**：查询端**也**量化，距离通过预先算好的中心间距离表 `sdc_table`（大小 `M × ksub × ksub`）得出：

```text
SDC(x, y) = ||q(x) − q(y)||² = Σ_m ||c_(m, code_x[m]) − c_(m, code_y[m])||²
```

好处是查询侧不再需要算距离表（省掉 `M × ksub` 次距离计算），代价是**两端都被量化**，误差更大。本 demo 断言同一对向量上 `SDC ≠ ADC`。

> 注意别和 `IndexIVFPQ` 的 `use_precomputed_table` 混淆：那张表大小是 **nlist × pq.M × pq.ksub**，是另一回事。

## 三、对比：三种距离/扫描方式

| 方式 | 查询端是否量化 | 每查询预处理 | 误差 | 适用 |
| --- | --- | --- | --- | --- |
| 暴力 L2 | 否 | 无 | 0 | 小规模基线 |
| ADC | 否 | `M × ksub` 次距离 | 仅库端量化 | **默认做法** |
| SDC | **是** | 0（查 `sdc_table`） | 两端量化 | 库极大、想省预处理 |

## 四、环境

- Python 3.9+（仅标准库）；Go 1.21+；C（C99）。无第三方依赖。

## 五、运行方式

```bash
cd python && python ivf_pq.py     # 20 条断言，全部实跑通过（含训练，约 10~30 秒）
cd go     && go run .
cd c      && cc -std=c99 ivf_pq.c -lm -o a.out && ./a.out
```

> Go 静态检查用 `python _docs/tools/go_sanity.py --spec check=2 <file>`（`check` 为 2 参签名）。
> Go / C 版为了可复现，用 LCG 直接构造中心表（不做 k-means 训练），重点验证**结构与距离口径**。

## 六、关键代码

编码残差（不是原向量）：

```python
def encode_residual(self, r):
    return [min(range(self.ksub), key=lambda i: l2(slice_vec(r, m), self.subcents[m][i]))
            for m in range(self.M)]
```

距离表 + ADC：

```python
def distance_table(self, r):                 # M × ksub
    return [[l2(slice_vec(r, m), self.subcents[m][j]) for j in range(self.ksub)]
            for m in range(self.M)]

@staticmethod
def adc(table, code):
    return sum(table[m][code[m]] for m in range(len(code)))   # 只有查表与加法
```

只扫前 `nprobe` 个列表：

```python
order = sorted(range(self.nlist), key=lambda j: l2(q, self.coarse[j]))[:nprobe]
```

## 七、性能边界与注意事项

1. **召回天花板来自量化误差**，不是来自 IVF。`nprobe` 调到底也没用；要提召回就加大 `nbits`（更多中心）或加一步精确重排。
2. **`nprobe` 的代价是线性的**：扫描量 ≈ `nprobe / nlist × N`，但列表长度通常不均，实际会偏离这个比例。
3. **`nbits` 增长会让训练成本线性上升**（每段要跑 `ksub`-means），且 `ksub` 不能超过训练样本量级，否则大量空簇。
4. **PQ 偏向 L2**：官方注释明说训练目标是最小化 L2 距离。做内积检索要先归一化向量，或换用专门的内积量化路径。
5. **残差编码是 IVFPQ 的精髓**：直接用 PQ 编码原向量（裸 `IndexPQ`）在同码长下明显更差。
6. 本 demo 用 8 个高斯簇的合成数据，绝对召回数字没有参考价值，**看趋势和恒等式**即可。

## 八、参考资料（均为本轮实际读过）

1. `IndexIVF.h`（只扫对应列表、`nprobe` 默认 1、`code_size`）— <https://cdn.jsdelivr.net/gh/facebookresearch/faiss@main/faiss/IndexIVF.h>
2. `IndexIVFPQ.h`（"Each **residual** vector is encoded as a product quantizer code"、`use_precomputed_table` 表大小 `nlist*M*ksub`）— <https://cdn.jsdelivr.net/gh/facebookresearch/faiss@main/faiss/IndexIVFPQ.h>
3. `ProductQuantizer.h`（`M`/`nbits`/`dsub`/`ksub`、centroids 布局、`compute_distance_table`、`sdc_table`、`PQEncoder8/16/Generic`、"biased towards L2"）— <https://cdn.jsdelivr.net/gh/facebookresearch/faiss@main/faiss/impl/ProductQuantizer.h>
4. `ProductQuantizer.cpp`（`compute_distance_table` 的转置/非转置两条实现、`search` 先建表再 `pq_knn_search_with_tables`）— <https://cdn.jsdelivr.net/gh/facebookresearch/faiss@main/faiss/impl/ProductQuantizer.cpp>
