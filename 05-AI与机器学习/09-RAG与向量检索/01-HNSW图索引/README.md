# HNSW 分层可导航小世界图

## 1. 简介

HNSW（Hierarchical Navigable Small World）是向量检索里最常用的图索引：把「可导航小世界图」按概率分层，查询从最稀疏的顶层一路贪心下降到最稠密的底层，从而把近邻搜索从 O(N) 压到近似 O(log N)。

本 demo 按 [hnswlib](https://github.com/nmslib/hnswlib) 官方实现（`hnswlib/hnswalg.h`、`hnswlib/space_l2.h`、`hnswlib/space_ip.h`，master 分支）逐段转写其核心机制，重点落在**容易被读错、写错却不报错**的几处：

- 层级是指数分布，但乘的是 `1/log(M)` 而不是 `log(M)`；
- `top_candidates` 是**距离大顶堆**，而 `candidateSet` 是**距离小顶堆**——两者用同一个比较器，靠「存负距离」区分；
- 启发式选边的剪枝判据是「已被选中的邻居比查询还近」；
- `ef` 会被 `max(ef_, k)` 悄悄抬高，所以 `ef < k` 的实验根本测不到 ef 的作用。

## 2. 原理

### 2.1 层级分配

```cpp
mult_ = 1 / log(1.0 * M_);                      // hnswalg.h:152
revSize_ = 1.0 / mult_;                          // = log(M)

int getRandomLevel(double reverse_size) {        // hnswalg.h:227
    std::uniform_real_distribution<double> distribution(0.0, 1.0);
    double r = -log(distribution(level_generator_)) * reverse_size;
    return (int) r;
}
```

调用处传的是 `mult_`（`addPoint` 第 1324 行 `getRandomLevel(mult_)`），所以

$$
\ell = \left\lfloor \frac{-\ln U}{\ln M} \right\rfloor,\quad U\sim(0,1]
$$

于是 `P(ℓ ≥ 1) = P(U ≤ 1/M) = 1/M`，`P(ℓ ≥ 2) = 1/M²`。**边界含等号**：`U = 1/M` 时 `-log(U)/log(M) = 1.0`，`(int)` 截断后恰好是 1 而不是 0。

常见误写是 `* log(M)`，那会让高层结点数量爆炸（M=16 时 `P(ℓ≥1)` 从 6.25% 变成 100%）。本 demo 用 `U = 1/M` 与 `U = 1/M²` 两个边界值把方向钉死。

### 2.2 两层容量

```cpp
maxM_  = M_;          // 上层
maxM0_ = M_ * 2;      // 底层，hnswalg.h:118-119
size_t Mcurmax = level ? maxM_ : maxM0_;
```

底层出度是上层的**两倍**。这不是调参习惯，而是官方在构造里写死的。

### 2.3 双堆与打断条件

`searchBaseLayer` 用两个 `priority_queue`，比较器都是

```cpp
struct CompareByFirst {
    constexpr bool operator()(pair<dist_t,tableint> const& a,
                              pair<dist_t,tableint> const& b) const noexcept {
        return a.first < b.first;
    }
};
```

`std::priority_queue` 把「按比较器最大」者放 `top()`，所以：

| 堆 | 入堆内容 | `top()` 语义 |
| --- | --- | --- |
| `top_candidates` | `(dist, id)` | **最远**（满了就 pop 掉最远） |
| `candidateSet` | `(-dist, id)` | **最近**（`(-dist)` 的最大值 = `dist` 的最小值） |

主循环的打断条件：

```cpp
if ((-curr_el_pair.first) > lowerBound && top_candidates.size() == ef_construction_)
    break;
```

两个条件必须**同时**成立：候选池已满 **且** 最近的未处理候选已经比池中最远者还远。注意是 `==` 不是 `>=`——池子没满时哪怕候选很远也要继续填。循环末尾 `lowerBound = top_candidates.top().first;` 只在「新元素入池」这个分支里更新。

### 2.4 启发式选边

```cpp
for (pair<dist_t,tableint> second_pair : return_list) {
    dist_t curdist = fstdistfunc_(getDataByInternalId(second_pair.second),
                                  getDataByInternalId(curent_pair.second), ...);
    if (curdist < dist_to_query) { good = false; break; }
}
```

即：**候选 c 被丢弃，当且仅当存在已入选的 s 满足 `d(s,c) < d(q,c)`**。这是为了保证邻居在方向上发散，避免所有边都指向同一个密集簇。

两个容易忽略的点：

1. 开头有 `if (top_candidates.size() < M) return;` —— 候选不足 M 时**完全不剪枝**原样返回。
2. 新点自己的边用 `M_` 剪，回连对方的边用 `Mcurmax` 剪（`mutuallyConnectNewElement`）。对方链表满了时，官方做法是「把新点并进去，以对方为查询再跑一次启发式」，并**注释掉**了另一套更朴素的方案（直接替换掉最远的那个邻居）。

### 2.5 高层是纯贪心，底层才是 beam search

`searchKnn` 里 `level > 0` 的下降是一段 `while (changed)` 的单点贪心（等价于 `ef=1`），只有第 0 层才走 `searchBaseLayerST`，且

```cpp
top_candidates = searchBaseLayerST<...>(currObj, query_data, std::max(ef_, k), ...);
while (top_candidates.size() > k) top_candidates.pop();
```

**`ef` 的实际值是 `max(ef_, k)`**。想测「ef 太小导致漏召回」，必须用 `k = 1`（或 `k ≤ ef`）；`k=10, ef=4` 的实测召回是 1.000，因为 ef 被抬成了 10。本 demo 两条曲线都跑了。

### 2.6 入口点

- 第一个元素「Do nothing」：`enterpoint_node_ = 0; maxlevel_ = curlevel;`（注意此时连层号都没用上）。
- 之后**只有** `curlevel > maxlevelcopy` 时才把入口点换成新点。所以「全 0 层」的数据集入口点恒为 0。

### 2.7 距离函数的 SIMD 派发（space_ip.h）

```cpp
if (dim % 16 == 0)      fstdistfunc_ = InnerProductDistanceSIMD16Ext;
else if (dim % 4 == 0)  fstdistfunc_ = InnerProductDistanceSIMD4Ext;
else if (dim > 16)      fstdistfunc_ = InnerProductDistanceSIMD16ExtResiduals;
else if (dim > 4)       fstdistfunc_ = InnerProductDistanceSIMD4ExtResiduals;
```

**走哪条代码路径由 `dim` 能否被 16/4 整除决定**，与向量内容无关。浮点加法不满足结合律，所以不同 dim 的求和顺序不同、末位可能差 1 ulp——写跨维度的一致性断言时要留容差。

## 3. 代码结构

| 文件 | 说明 |
| --- | --- |
| `python/hnsw.py` | 模型：双堆、`getRandomLevel`、`searchBaseLayer`、`getNeighborsByHeuristic2`、`mutuallyConnectNewElement` |
| `python/selfcheck_hnsw.py` | 自检，63 条断言 |
| `python/main.py` | 演示：层级分布 / 出度上限 / ef-召回曲线 / 距离计算次数 |
| `go/hnsw.go`、`go/main.go` | Go 同题实现（`container/heap` 复刻两个方向的堆） |

## 4. 运行

```bash
cd python && python selfcheck_hnsw.py   # PASS=63  FAIL=0
cd python && python main.py
cd go && go run .                        # 本机无 Go 工具链，未实跑
```

## 5. 自检覆盖的坑

| 断言 | 说明 |
| --- | --- |
| `U=1/M → level 1`（含等号） | 写反 `mult_` 会立刻失败 |
| `P(level=0) ≈ 1-1/M` | 用 4000 个确定性 U 反推分布 |
| `top_candidates.top()` 是最远 | 大顶堆语义 |
| `候选数 < M 时启发式不剪枝` | 官方 early return；**测试里 M 必须 ≤ 候选数**，否则断言恒假 |
| 共线簇只剩最近者 / 正交点全保留 | 剪枝判据的成对正负对照 |
| `d(s_i,s_j) ≥ d(q,s_j)`（i<j） | 入选序列的多样性性质 |
| `ef=1` 的 base search ≡ 纯贪心 | 两种写法同结果 |
| `ef=max(ef_,k)` 抬高 | `ef_=16 < k=20` 仍返回 20 条 |
| `ef=N` 时 top-1 与暴力一致 | 图索引的上界正确性 |
| 全 0 层时入口点恒为 0 | 入口点更新条件 |

## 6. 参考资料（本轮实读）

- [hnswlib/hnswalg.h](https://raw.githubusercontent.com/nmslib/hnswlib/master/hnswlib/hnswalg.h) —— 层级生成、`searchBaseLayer`、`getNeighborsByHeuristic2`、`mutuallyConnectNewElement`、`searchKnn`
- [hnswlib/space_l2.h](https://raw.githubusercontent.com/nmslib/hnswlib/master/hnswlib/space_l2.h) —— `L2Sqr` 返回**平方**距离
- [hnswlib/space_ip.h](https://raw.githubusercontent.com/nmslib/hnswlib/master/hnswlib/space_ip.h) —— `InnerProductDistance = 1.0f - InnerProduct`、SIMD 派发
- [hnswlib/hnswlib.h](https://raw.githubusercontent.com/nmslib/hnswlib/master/hnswlib/hnswlib.h) —— `SpaceInterface` 抽象
- Malkov & Yashunin, *Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs*, arXiv:1603.09320 —— 原论文（本轮只作定性著录，定量结论一律以源码为准）
