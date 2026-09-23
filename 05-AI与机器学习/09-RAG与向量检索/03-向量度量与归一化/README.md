# 向量度量空间与归一化

## 1. 简介

同样的向量集合，换一个度量就换一套检索结果。RAG 里最常见的翻车现场是：**embedding 明明是按余弦训练的，索引却按 L2 建**。两者的排序只有在向量归一化之后才等价。

本 demo 按 [hnswlib](https://github.com/nmslib/hnswlib) 的 `space_l2.h` / `space_ip.h` 与 [faiss](https://github.com/facebookresearch/faiss) 的 `MetricType.h` 逐条核对度量实现，把这些「看起来差不多的定义」做成可断言的差异：

- `L2Sqr` 返回的是**平方**距离；
- `InnerProductDistance = 1.0f - InnerProduct(...)` ——**没有归一化**，而且**可以为负**；
- hnswlib **没有 cosine 空间**，要余弦就得自己先把向量归一化再用 `ip`；
- 距离函数走哪条 SIMD 路径由 **`dim` 的整除性**决定，浮点求和顺序一变结果就不同；
- 顺带踩到一个 Python 侧的坑：3.12+ 的 `sum()` 对 float 用补偿求和，**与 C++ 的朴素累加不是一回事**。

## 2. 原理

### 2.1 L2：官方从不写根号

```cpp
static float L2Sqr(const void *pVect1v, const void *pVect2v, const void *qty_ptr) {
    ...
    for (size_t i = 0; i < qty; i++) {
        float t = *pVect1 - *pVect2;
        pVect1++; pVect2++;
        res += t * t;
    }
    return (res);
}
```

返回 `Σ(aᵢ−bᵢ)²`。开根是单调变换，**排序完全一致**，所以省掉 `sqrt` 是安全的；但**距离值本身不能跨实现比对**——同样两点的「距离」，hnswlib 报的是别人的平方。faiss 的 `METRIC_L2` 注释也写明是 `squared L2 search`。

### 2.2 IP：一个算减法的「距离」

```cpp
static float InnerProductDistance(const void *pVect1, const void *pVect2,
                                  const void *qty_ptr) {
    return 1.0f - InnerProduct(pVect1, pVect2, qty_ptr);
}
```

两个后果：

1. **它不需要为正**。`⟨a,b⟩ > 1` 时结果就是负数。所以它不是度量空间意义上的「距离」，连非负性都没有，更别提三角不等式。
2. **它不是余弦**。没有除以两个模长。想要余弦，必须自己先归一化——这正是 hnswlib 不提供 cosine 空间的原因（源码里 `space_ip.h` 通篇找不到 `cosine`）。

faiss 侧的语义也对得上：`METRIC_INNER_PRODUCT` 的注释是 `maximum inner product search`，即**越大越近**，与 L2 的「越小越近」方向相反。

### 2.3 单位向量下的三者等价

当 `‖u‖ = ‖w‖ = 1`：

$$
\|u-w\|^2 = \|u\|^2 + \|w\|^2 - 2\langle u,w\rangle = 2 - 2\cos(u,w)
$$

于是 **argmin L2 ≡ argmax IP ≡ argmax cosine**。这是「归一化后用 L2 索引做余弦检索」的全部依据。本 demo 用 10 个查询 × 30 个点对这个等价关系做了逐对比对，并给出未归一化时的负控（10/10 全部不一致）。

反例（长度主导）：`q=(1,0)` 面对 `长同向 (10,0)` 与 `短反向 (-0.5,0)`：

| 度量 | 短反向 | 长同向 | 谁更近 |
| --- | --- | --- | --- |
| L2 平方 | 2.25 | 81 | 短反向 |
| IP | −0.5 | 10 | 长同向 |

### 2.4 SIMD 派发由 `dim` 决定

```cpp
InnerProductSpace(size_t dim) {
    fstdistfunc_ = InnerProductDistance;
    if (dim % 16 == 0)      fstdistfunc_ = InnerProductDistanceSIMD16Ext;
    else if (dim % 4 == 0)  fstdistfunc_ = InnerProductDistanceSIMD4Ext;
    else if (dim > 16)      fstdistfunc_ = InnerProductDistanceSIMD16ExtResiduals;
    else if (dim > 4)       fstdistfunc_ = InnerProductDistanceSIMD4ExtResiduals;
}
```

**命中顺序是 `%16 → %4 → >16 → >4`**，所以 `dim=20` 走的是 `SIMD4Ext`（20%4==0 先命中），不是 `Residuals`——这条很容易凭直觉写反。

浮点加法不满足结合律，分块宽度不同 ⇒ 求和顺序不同 ⇒ 结果可能不同。本机实测 `[1e16,1,1,1,−1e16,1,1,1]` 点乘全 1 向量：

| 路径 | 结果 |
| --- | --- |
| 朴素累加（C++ 标量版） | 3.0 |
| SIMD4 分块 | 0.0 |
| SIMD16 分块（宽度覆盖全长） | 3.0 |

SIMD16 与标量相同是**负控**：宽度覆盖全长时分块退化为同序求和。跨 `dim` 的一致性断言必须留容差。

### 2.5 一个 Python 侧的对照

Python 3.12 起，`sum()` 对 float 走 Neumaier 补偿求和。同一份数据：

```
朴素累加          3.0
sum(catastrophic) 6.0
```

**本 demo 的所有距离计算都刻意用朴素 for 循环**，不用内置 `sum()`，否则「对拍 C++」这件事本身就不成立。

## 3. 代码结构

| 文件 | 说明 |
| --- | --- |
| `python/metrics.py` | `L2Sqr` / `InnerProduct` / `InnerProductDistance` / `normalize` / `cosine` / SIMD 派发与三种求和路径 |
| `python/selfcheck_metrics.py` | 自检，39 条断言 |
| `python/main.py` | 演示：归一化前后的排序分歧 / 长度主导反例 / 负距离 / SIMD 派发表 / 求和顺序 |
| `go/metrics.go` | Go 同题实现（含 `main`） |

## 4. 运行

```bash
cd python && python selfcheck_metrics.py   # PASS=39  FAIL=0
cd python && python main.py
cd go && go run .                           # 本机无 Go 工具链，未实跑
```

## 5. 自检覆盖的坑

| 断言 | 说明 |
| --- | --- |
| `L2Sqr` 是平方值、与开根排序一致 | 单调变换不改变序 |
| `InnerProductDistance` 可为负 | `⟨a,b⟩=2` 时距离 −1 |
| 单位向量 `‖u−w‖² == 2−2⟨u,w⟩ == 2−2cos` | 三条路线严格相等 |
| 归一化后三种排序全一致 / 未归一化则不一致 | 成对正负对照 |
| `dim=20 → SIMD4Ext` | 派发命中顺序易写反 |
| SIMD16 宽度覆盖全长时等于标量 | 负控 |
| Python `sum()` ≠ 朴素累加 | 转写 C++ 时的隐藏陷阱 |
| IP 距离不满足三角不等式 | 它不是度量空间 |

## 6. 参考资料（本轮实读）

- [hnswlib/space_l2.h](https://raw.githubusercontent.com/nmslib/hnswlib/master/hnswlib/space_l2.h) —— `L2Sqr` 的标量与 SIMD 实现
- [hnswlib/space_ip.h](https://raw.githubusercontent.com/nmslib/hnswlib/master/hnswlib/space_ip.h) —— `InnerProductDistance = 1.0f - InnerProduct`、`InnerProductSpace` 的 SIMD 派发；**无 cosine 空间**
- [faiss/MetricType.h](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/MetricType.h) —— `METRIC_INNER_PRODUCT` 是 maximum inner product search、`METRIC_L2` 是 squared L2 search
- [Python 3.12 What's New: sum() now uses Neumaier summation](https://docs.python.org/3/whatsnew/3.12.html) —— 内置 `sum()` 对 float 的补偿求和（定性著录）
