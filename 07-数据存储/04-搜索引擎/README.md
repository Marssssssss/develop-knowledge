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
- [x] 分析器链与中文分词（char filter / tokenizer / token filter 三段式；IK 的 ik_max_word vs ik_smart 是同一批词元的裁决开关；`discount_overlaps` 默认 true 把 0-increment 重叠词排除出 norm） — 见 [分析器链与中文分词/](./分析器链与中文分词/)（C / Python / Go）
- [x] bool 查询与评分合并（四类 occurrence；must/should 分数**相加**；filter/must_not 走 filter context 不计分且可缓存；`minimum_should_match` 默认值规则与 6 种规格） — 见 [Bool查询与评分合并/](./Bool查询与评分合并/)（C / Python / Go）
- [x] 分布式检索两阶段与深分页（query 阶段每片回 from+size 条、fetch 只去命中分片；`max_result_window` 10000；search_after + PIT；`search_type` 本地 IDF vs 全局 IDF 会翻转排序） — 见 [分布式检索与深分页/](./分布式检索与深分页/)（C / Python / Go）
- [x] DocValues 列式存储与聚合（倒排 term→docs vs 列存 doc→term；doc-value-only 需扫整列；global ordinals 的 packed-ints 映射与"归并排序全部 term"的代价） — 见 [DocValues列式存储与聚合/](./DocValues列式存储与聚合/)（C / Python / Go）
- [x] IVF-PQ 向量量化（编码的是**残差**；`M×ksub` 距离表 + ADC 恒等于到重构残差的平方距离；SDC 查 `M×ksub×ksub` 表；`nprobe=nlist` 也到不了召回 1.0） — 见 [IVFPQ向量量化/](./IVFPQ向量量化/)（C / Python / Go）

## 待研究

- [x] BM25 评分算法（已在 [BM25评分/](./BM25评分/)）
- [x] 中文/IK 分词器与倒排表的字段长度归一化（影响 BM25 的 avgdl）（已在 [分析器链与中文分词/](./分析器链与中文分词/)）
- [x] 向量检索（已在 [HNSW向量检索/](./HNSW向量检索/)）
- [x] IVF-PQ（Faiss）：向量量化与索引协同（已在 [IVFPQ向量量化/](./IVFPQ向量量化/)）
- [x] 段合并（已在 [段合并策略/](./段合并策略/)）
- [ ] Lucene 10 logical partitioning（合并已与搜索并行性脱钩）
- [ ] 高亮（postings 的 positions/offsets 与 FVH / unified highlighter 的差异）
- [ ] 向量量化的 refine / 重排（PQ 的召回天花板如何靠二级精排突破）
- [ ] suggester / completion（FST + 前缀权重）
