# 搜索引擎

## 子主题

- Elasticsearch：倒排索引 / NRT refresh / text vs keyword
- 评分算法：BM25 (Lucene / Elasticsearch 默认)
- 近似最近邻：HNSW 多层图（Milvus / Qdrant / Pinecone / ES 8+ / Lucene 9+ 默认）
- Lucene 段合并：TieredMergePolicy / 段删除回收

## 已完成 demo

- [x] Elasticsearch 倒排索引与近实时搜索 — 见 [elasticsearch/](./elasticsearch/)（Python / Go）
- [x] BM25 评分算法（Lucene/Elasticsearch 默认相关性） — 见 [BM25评分/](./BM25评分/)（Python / Go）
- [x] HNSW 近似最近邻检索（Malkov 2016，多层可导航小世界图） — 见 [HNSW向量检索/](./HNSW向量检索/)（Python / Go）
- [x] Lucene 段合并与删除回收（TieredMergePolicy + 20 MB/s 节流） — 见 [段合并策略/](./段合并策略/)（Python / Go）

## 待研究

- [x] BM25 评分算法（已在 [BM25评分/](./BM25评分/)）
- [ ] 中文/IK 分词器与倒排表的字段长度归一化（影响 BM25 的 avgdl）
- [x] 向量检索（已在 [HNSW向量检索/](./HNSW向量检索/)）
- [ ] IVF-PQ（Faiss）：向量量化与索引协同
- [x] 段合并（已在 [段合并策略/](./段合并策略/)）
- [ ] Lucene 10 logical partitioning（合并已与搜索并行性脱钩）
