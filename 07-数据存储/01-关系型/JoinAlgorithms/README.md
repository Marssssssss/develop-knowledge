# Join 算法(nested loop / hash join / GRACE / sort-merge)

## 简介

等值连接是关系库最贵的算子之一。本 demo 实现 5 种 join 算法并验证结果一致性与成本模型:**朴素/块嵌套循环、哈希连接(build+probe)、GRACE 分区哈希连接、排序归并连接**。依据 CMU 15-445 (Spring 2023) Lecture 11。

关键概念:build/probe 阶段 / 分区(partition)/ 重复键回溯 / 外部归并排序成本 / hybrid hash join。

## 原理详解

**五算法一览**(成本:R=M 页 m 元组、S=N 页 n 元组、B 缓冲页):

| 算法 | I/O 成本 | 讲义示例(M=1000,N=500,B=100,0.1ms/IO) |
| --- | --- | --- |
| 朴素嵌套循环 | M + m·N | ~1.4 小时 |
| 块嵌套循环 | M + ⌈M/(B-2)⌉·N | ~50 秒 |
| 索引嵌套循环 | M + m·C | 视索引而定 |
| 排序归并 | M+N + sort cost | 0.75 秒 |
| **哈希连接** | **3(M+N)** | **0.45 秒** |

**哈希连接**:

1. **Build**:扫描小表(外表),用 h1 在连接键上建哈希表(值可为元组或引用;可配合 Bloom filter 提前拒绝不存在的键)。
2. **Probe**:扫描大表(内表),每条元组算 h1 探测哈希表,**只需外表(或其分区)放得进内存**,内表可以无限大。
3. **GRACE 分区哈希连接**(表超内存):两表用**同一 h1** 分区写到 k 个桶 → 不同分区必不匹配 → 逐对分区载入内存做小 join。分区阶段 2(M+N)(两表读写),probe 阶段 M+N,**总 3(M+N)**。单分区仍超内存 → 换 h2 **递归分区**;单键重复超多 → 该键退化为块嵌套循环。
4. **Hybrid 优化**:热点分区留内存立即比对,其余才落盘(讲义:难正确实现)。

**排序归并连接**:两表按 key 排序(外部归并排序成本 `2P(1+⌈log_{B-1}⌈P/B⌉⌉)`)后双指针归并;**重复键**需固定外表指针、枚举内表重复段,外键重复出现时内表指针回到重复段起点回溯。

**选型**(讲义结论):哈希几乎总是更快;但数据倾斜(hash 对 skew 敏感)、输入已按 key 有序、或结果本身需要排序时,sort-merge 更优 —— 好的 DBMS 两者都实现。

## 环境

- Python ≥ 3.8;Go ≥ 1.21(静态审查)

## 运行方式

```bash
python3 python/main.py   # 输出 ALL 5 ... ASSERTIONS PASSED
go run go/main.go
```

## 关键代码片段

```python
# GRACE:两表同一 h1 分区 → 跨分区必不匹配
for r in R:
    pr[r[0] % k].append(r)

# sort-merge 重复键:内表从重复段起点枚举全部组合
jj = j
while jj < len(s_sorted) and s_sorted[jj][0] == rk:
    out.append((r_sorted[i], s_sorted[jj])); jj += 1
```

## 性能与边界

- 哈希连接最大可连接表 ≈ (B-1)(B-2) 页(无递归分区);N 页表约需 √N 缓冲,工程上加 fudge factor f>1 即 √(fN)。
- 朴素嵌套循环 1.4 小时 vs 哈希 0.45 秒(讲义数字)—— 算法选择差 4 个数量级。
- sort-merge 在 B > √L(较大表页数)时可把排序与归并合并成一趟,成本同样为 3(M+N)。

## 注意事项与常见坑

- **skew**:全部元组同键 → hash join 分区退化为块嵌套;讲义建议该键兜底。
- sort-merge 重复键不回溯 → 漏配对(本 demo 专门断言 1×3 组合)。
- 哈希表建在**小表**;搞反方向会无谓多探测(本 demo probe 次数 = |内表| 有断言)。

## 参考资料(实际阅读过的权威来源)

- [CMU 15-445 (Spring 2023) Lecture 11: Join Algorithms — Notes](https://15445.courses.cs.cmu.edu/spring2023/notes/11-joins.pdf) — 五算法成本公式、GRACE/递归分区、hybrid hash join、示例数字(经 WebSearch 摘录阅读)
- [CMU 15-445 (Spring 2023) Slides 11-joins](https://15445.courses.cs.cmu.edu/spring2023/slides/11-joins.pdf) — 分区哈希 join 两阶段图示与 3(M+N) 推导(经 WebSearch 摘录阅读)
- [TUM: Data Processing on Modern Hardware — Join slides](https://db.in.tum.de/teaching/ss21/dataprocessingonmodernhardware/MH_4.pdf) — 并行/硬件感知 hash join、build 易并行 probe 免同步(经 WebSearch 摘录阅读)
