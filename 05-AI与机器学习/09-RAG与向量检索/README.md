# RAG 与向量检索

聚焦检索侧（召回 + 排序 + 评估 + 融合）的**原理级**实现：不训练 embedding，只做「给定向量/文本之后，怎么又快又准地找到并排好序」。

## 已完成 demo

| 目录 | demo |
| --- | --- |
| [01-HNSW图索引/](./01-HNSW图索引/) | 621 HNSW 图索引：层级指数分布（`mult = 1/log M`）、双堆与打断条件、`getNeighborsByHeuristic2` 多样性剪枝、`ef = max(ef_, k)`、入口点只在超层时更新（2026-09-23 首批，Py 63 断言实跑全绿） |
| [02-乘积量化与IVFPQ/](./02-乘积量化与IVFPQ/) | 622 乘积量化与 IVF-PQ：`code_size = ceil(nbits·M/8)`、ADC 恒等式（对距离表求和 == 还原后 L2）、内积 ADC 的 `‖x‖²+‖y‖²−2⟨x,y⟩` 路线、`nprobe` 被 `min(nlist,·)` 钳住、预计算表的三个拒答条件（门槛 2 GiB）（Py 58 断言实跑全绿） |
| [03-向量度量与归一化/](./03-向量度量与归一化/) | 623 向量度量与归一化：`L2Sqr` 是平方距离、`InnerProductDistance = 1 − IP` 可为负、hnswlib 无 cosine 空间、单位向量下三种度量同序、SIMD 派发由 `dim` 整除性决定（Py 39 断言实跑全绿） |
| [04-检索评估指标/](./04-检索评估指标/) | 624 离线评估指标：scikit-learn 与 trec_eval 两套口径并列（折扣 `1/log₂(i+2)`、k 截断是置零、并列平均 DCG == 全排列平均、默认增益是相关性等级而非 `2^rel−1`、`recall@k` 判定在计数之前）（Py 39 断言实跑全绿） |
| [05-混合检索与RRF融合/](./05-混合检索与RRF融合/) | 625 混合检索与 RRF 融合：Lucene BM25（idf 恒正、tf 无 `(k1+1)`、`doScore` 单调改写、doc 长度 1 字节量化）+ RRF（`k = 60` 的出处与理由、Table 1 的 k 敏感性）+ CombMNZ / Condorcet 对照（Py 51 断言实跑全绿） |

## 待研究

- [ ] 分块（chunking）策略对召回的影响：固定长度 vs 语义切分 vs 滑动窗口重叠
- [ ] 重排（rerank）交叉编码器的代价模型：top-K 宽度与延迟/质量的权衡
- [ ] 元数据过滤与向量检索的耦合：pre-filter vs post-filter 的召回塌陷
- [ ] 多向量 /  late-interaction（ColBERT 类）索引的存储与检索开销
- [ ] 量化感知的召回补偿：PQ 之后的 rerank 与 OPQ 旋转的作用
- [ ] DiskANN / Vamana 与 HNSW 在 SSD 上的取舍
- [ ] 向量索引的增量更新与删除：墓碑机制对图连通性的长期影响
- [ ] 分布式分片策略：按 nlist 切 vs 按向量切，扇出查询的尾延迟
- [ ] 混合检索里稀疏分数与稠密分数的归一化体系统一（min-max / z-score / rank-based）
- [ ] 在线指标与离线指标的相关性：CTR/停留时长能否用 NDCG 代理

## 参考资料（本轮实读）

- [hnswlib/hnswalg.h](https://raw.githubusercontent.com/nmslib/hnswlib/master/hnswlib/hnswalg.h) · [space_l2.h](https://raw.githubusercontent.com/nmslib/hnswlib/master/hnswlib/space_l2.h) · [space_ip.h](https://raw.githubusercontent.com/nmslib/hnswlib/master/hnswlib/space_ip.h)
- [faiss ProductQuantizer.cpp](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/impl/ProductQuantizer.cpp) · [ProductQuantizer.h](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/impl/ProductQuantizer.h) · [IndexIVFPQ.cpp](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/IndexIVFPQ.cpp) · [IndexIVF.cpp](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/IndexIVF.cpp) · [IndexIVF.h](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/IndexIVF.h) · [Clustering.h](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/Clustering.h) · [MetricType.h](https://raw.githubusercontent.com/facebookresearch/faiss/main/faiss/MetricType.h)
- [scikit-learn `_ranking.py`](https://raw.githubusercontent.com/scikit-learn/scikit-learn/main/sklearn/metrics/_ranking.py)
- [trec_eval `m_ndcg.c`](https://raw.githubusercontent.com/usnistgov/trec_eval/main/m_ndcg.c) · [m_recall.c](https://raw.githubusercontent.com/usnistgov/trec_eval/main/m_recall.c) · [m_P.c](https://raw.githubusercontent.com/usnistgov/trec_eval/main/m_P.c)
- [Lucene `BM25Similarity.java`](https://raw.githubusercontent.com/apache/lucene/main/lucene/core/src/java/org/apache/lucene/search/similarities/BM25Similarity.java)
- Cormack, Clarke & Büttcher, *Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods*, SIGIR 2009 —— [PDF](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf)
