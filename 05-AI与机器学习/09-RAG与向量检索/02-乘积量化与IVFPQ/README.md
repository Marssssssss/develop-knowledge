# 乘积量化与 IVF-PQ

## 1. 简介

向量检索要扛住千万级规模，光靠图索引不够：**内存**才是第一道墙。1000 万条 768 维 float32 就是 30 GB。乘积量化（PQ）把每个向量压成几十个字节的短码，代价是距离只能近似计算。

本 demo 按 [faiss](https://github.com/facebookresearch/faiss) 官方源码（`faiss/impl/ProductQuantizer.{h,cpp}`、`faiss/IndexIVFPQ.cpp`、`faiss/IndexIVF.cpp`、`faiss/IndexIVF.h`、`faiss/Clustering.h`，main 分支）转写 PQ 与 IVF-PQ 的核心机制，重点在**公式与常量本身**：

- `code_size = ceil(nbits·M/8)`，`d % M == 0` 是硬约束，`nbits > 24` 官方直接拒答；
- ADC（Asymmetric Distance Computation）**不必还原向量**——对距离表按 code 求和即可，且结果与「还原后再算」严格相等；
- 内积度量下必须走 `‖x‖² + ‖y‖² − 2⟨x,y⟩`，多出来的 `‖y‖²` 就是官方那张 `centroids_sq_lengths`；
- `nprobe` 会被 `min(nlist, nprobe)` 悄悄钳住；
- 预计算表有三个拒答条件，其中一个门槛是 **2 GiB**。

## 2. 原理

### 2.1 派生值（set_derived_values）

```cpp
FAISS_THROW_IF_NOT_MSG(M > 0, "M must be > 0");
FAISS_THROW_IF_NOT_MSG(d % M == 0,
        "The dimension of the vector (d) should be a multiple of the number of subquantizers (M)");
dsub = d / M;
FAISS_THROW_IF_MSG(nbits > 24, "nbits larger than 24 is not practical.");
code_size = (nbits * M + 7) / 8;
ksub = 1 << nbits;
```

把 d 维切成 M 段，每段 dsub 维、各自一个 ksub 点的码本。码长是 `(nbits*M+7)/8` 字节——注意是**按位打包**，所以 `M=3, nbits=6` 得到的是 3 字节而不是 2 字节（`ceil(18/8)`）。

### 2.2 ADC：为什么不还原向量

```cpp
// dis_table (m, j) = || x_m - c_(m, j)||^2
// 查询时逐段查表、求和：
d(x, y) = Σ_m dis_table(m, code[m])
```

而 `ŷ = decode(code)` 是各段码字的拼接，于是

$$
\|x-\hat y\|^2=\sum_{m}\|x_m-c_{m,\mathrm{code}[m]}\|^2=\sum_m \mathrm{dis\\_table}(m,\mathrm{code}[m])
$$

这是**恒等式**而不是近似：省掉的是解码开销，误差本身来自量化，与是否解码无关。本 demo 把两侧都算了一遍做对拍，并额外换一份 code 验证不是巧合。

内积模式下 `faiss` 提供的是 `compute_inner_prod_table`（`<x_m, c_(m,j)>`，不是 L2），配合 `centroids_sq_lengths` 走

$$
\|x-\hat y\|^2=\|x\|^2+\|\hat y\|^2-2\langle x,\hat y\rangle
$$

两条路线必须给出同一个数，自检里也做了对拍。

### 2.3 `by_residual`：默认开启的一段隐含步骤

```cpp
IndexIVFPQ::IndexIVFPQ(...)
    : pq(d_in, M, nbits_per_idx) {
    code_size = pq.code_size;
    is_trained = false;
    by_residual = true;          // <-- 官方写死，不是 False
    use_precomputed_table = 0;
    scan_table_threshold = 0;
}
```

`by_residual = true` 意味着 PQ 编的是**残差** `x − c_coarse(x)`，而不是原始向量。这一步把 IVF 的粗量化误差从 PQ 里剥离出去，代价是查询时每条倒排链都要用各自的残差查询重算距离表（这也是下一节预计算表存在的理由）。

### 2.4 nprobe 的钳制

```cpp
const size_t cur_nprobe = std::min(nlist, params ? params->nprobe : this->nprobe);
FAISS_THROW_IF_NOT(cur_nprobe > 0);
```

`nprobe` 默认是 **1**（`faiss/IndexIVF.h`），且被 `nlist` 钳住。所以 `nlist=16, nprobe=64` 的有效 probes 是 16，召回不再增长——本 demo 的输出里 16 与 64 两行的召回完全相同。

### 2.5 预计算表的三个拒答条件

```cpp
size_t precomputed_table_max_bytes = ((size_t)1) << 31;   // 2 GiB
...
if (!(quantizer->metric_type == METRIC_L2 && by_residual)) { /* 不预计算 */ }
if (table_size > precomputed_table_max_bytes) { /* 不预计算 */ }
```

表大小 = `M * ksub * nlist * sizeof(float)`。也就是说 `M=4, nbits=8, nlist=2^21` 时约 8.6 GB，直接被 2 GiB 门槛拒掉。另外官方只在 **L2 度量 + 残差编码**时才认为预计算有价值，且 `{MultiIndexQuantizer 且 pq.M % miq->pq.M == 0}` 会走 type 2（更紧凑）。

**它为什么默认是关的**：`use_precomputed_table = 0`，只有 `train_encoder` 里 `if (by_residual) precompute_table();` 才可能把它打开。收益只在「倒排链长度 > `ksub * M`」时才体现。

### 2.6 训练样本量口径

```cpp
idx_t IndexIVFPQ::train_encoder_num_vectors() const {
    return pq.cp.max_points_per_centroid * pq.ksub;
}
```

`ClusteringParameters` 默认 `niter=25`、`min_points_per_centroid=39`、`max_points_per_centroid=256`。所以训练语料至少要 `256 * ksub` 条——`nbits=8` 时是 **65536** 条。demo 里为了跑得快把 `niter` 降到 8 并缩小了数据量，**这是 demo 的妥协不是官方默认值**，README 与输出都标注了。

## 3. 代码结构

| 文件 | 说明 |
| --- | --- |
| `python/pq.py` | ProductQuantizer：派生值校验、k-means 训练、编码/解码、两张距离表、两条 ADC、打包 |
| `python/ivfpq.py` | IndexIVFPQ：粗量化、残差、nprobe 钳制、预计算表决策、检索与召回 |
| `python/selfcheck_pq.py` | 自检，58 条断言 |
| `python/main.py` | 演示：码长表 / 量化误差 / by_residual / nprobe-召回 / 预计算表决策 |
| `go/pq.go`、`go/ivfpq.go`、`go/main.go` | Go 同题实现 |

## 4. 运行

```bash
cd python && python selfcheck_pq.py   # PASS=58  FAIL=0
cd python && python main.py           # 约 1~3 分钟（纯 Python k-means++，已缩数据）
cd go && go run .                      # 本机无 Go 工具链，未实跑
```

## 5. 实测结论（本数据：600 条 8 维，niter=8）

| M / nbits | code | 平均重构误差 |
| --- | --- | --- |
| 2 / 8 | 2 B | 0.0253 |
| 4 / 4 | 2 B | 0.0420 |
| 4 / 8 | 4 B | 0.0015 |
| 8 / 4 | 4 B | 0.0026 |

两条读数值得记：

- **固定 nbits 时 M 增大误差单调下降**（码长变长）。
- **固定码长时 M 与 nbits 的取舍不单调**：本数据上 `M=4/nbits=8` 优于 `M=8/nbits=4`——`dsub=2` 配 256 个质心已经过饱和，不如把预算花在每段的码本分辨率上。这条结论**依赖于数据分布**，不要外推。

## 6. 自检覆盖的坑

| 断言 | 说明 |
| --- | --- |
| `code_size` 按位打包（3/6 → 3 字节） | 按 `M*nbits` 直接除 8 会算少 |
| `d%M != 0` / `nbits > 24` 抛错 | 官方两条硬校验 |
| ADC 求和 == 还原后 L2 | 恒等式对拍（含换 code 的负控） |
| 内积 ADC == 同一距离 | `‖x‖²+‖y‖²−2⟨x,y⟩` 路线对拍 |
| `train_encoder_num_vectors = 256*ksub` | faiss 训练语料下界 |
| `nprobe` 被 `min(nlist,·)` 钳住、`0` 抛错 | IndexIVF.cpp 的行为 |
| 预计算表三个拒答条件 | L2+残差 / 2 GiB / `-1` |

## 7. 参考资料（本轮实读）

- [faiss/impl/ProductQuantizer.cpp](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/impl/ProductQuantizer.cpp) —— `set_derived_values`、`compute_distance_table`、`compute_inner_prod_table`
- [faiss/impl/ProductQuantizer.h](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/impl/ProductQuantizer.h) —— centroids / centroids_sq_lengths 的 layout 注释
- [faiss/IndexIVFPQ.cpp](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/IndexIVFPQ.cpp) —— 构造默认值、`train_encoder_num_vectors`、`initialize_IVFPQ_precomputed_table`
- [faiss/IndexIVF.cpp](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/IndexIVF.cpp) —— `cur_nprobe = std::min(nlist, ...)`
- [faiss/IndexIVF.h](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/IndexIVF.h) —— `size_t nprobe = 1;`
- [faiss/Clustering.h](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/Clustering.h) —— `niter=25`、`min/max_points_per_centroid`
- Jégou et al., *Product Quantization for Nearest Neighbor Search*, TPAMI 2011 —— 原论文（本轮作定性著录，定量结论一律以源码为准）
