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
- [x] 倒排表压缩与跳表（Lucene 104 postings：256 值一块做 d-gap 再 PForDelta；`token` 一字节高 3 位是 numExceptions（故上限 7）、低 5 位是 bitsPerValue；patch 下标要塞进 1 字节故 `BLOCK_SIZE ≤ 256`；块间铺多级跳表支撑 `advance(target)`） — 见 [倒排表压缩与跳表/](./倒排表压缩与跳表/)（Python / Go）
- [x] BlockMaxWAND 与动态剪枝（MaxScoreBulkScorer：impacts 上界切 outer window、`INNER_WINDOW_SIZE = 1<<12` 内层窗口收集进 bitset、`partitionScorers` 按 `maxWindowScore/cost` 排序划 essential / non-essential） — 见 [BlockMaxWAND与动态剪枝/](./BlockMaxWAND与动态剪枝/)（Python / Go）
- [x] BKD 树多维点索引（`numLeaves = ceil(pointCount / 512)` 只由点数决定、与分布无关故能 one-pass 自底向上打包；内节点包围盒**不落盘**、查询沿路径 `pushBounds` 推出来；`numIndexDims < numDims` 的多余维度只存不索引） — 见 [BKD树多维点索引/](./BKD树多维点索引/)（Python / Go）
- [x] 高亮器与摘要片段生成（UnifiedHighlighter 的 OffsetSource 五态与 `getOptimizedOffsetSource` 的升降级判据；PassageScorer 迷你 BM25（k1=1.2 / b=0.75 / pivot=87）用 `numDocs = 1 + len/pivot` 反推 IDF；`LengthGoalBreakIterator` 的 min vs closest-to-length 两种断点；top-N 有界小堆复用对象） — 见 [高亮器与摘要片段生成/](./高亮器与摘要片段生成/)（Python / Go）
- [x] FST 与前缀补全 suggester（`encode(w) = Integer.MAX_VALUE - w` 让「升序」即「权重降序」；payload = surface + 分隔符 + vint(docId) 且 `MAX_DOC_ID_LEN_WITH_SEP = 6`；`liveDocsRatio` 参与的 `MAX_TOP_N_QUEUE_SIZE = 5000` 队列上界启发式） — 见 [FST与前缀补全suggester/](./FST与前缀补全suggester/)（Python / Go）
- [x] 模糊查询与编辑距离自动机（`MAXIMUM_SUPPORTED_DISTANCE = 2`；`floatToEdits` 三分支 + `1-0.8 = 0.1999…` 导致的「本该 2 实际 1」二进制浮点陷阱；OSA 与真 Damerau-Levenshtein 的差异；`ParametricDescription.size = minErrors.length × (w+1)`） — 见 [模糊查询与编辑距离自动机/](./模糊查询与编辑距离自动机/)（Python / Go）
- [x] 索引排序与提前终止（`SortField.getIndexSorter()` 仅 STRING/INT/LONG/DOUBLE/FLOAT 非 null；`IndexWriterConfig.setIndexSort` 是「全有或全无」；`Sort.getPrimarySortField` 的三条跳过规则：字段为 null、段内缺该字段、skipper 稠密且 min==max） — 见 [索引排序与提前终止/](./索引排序与提前终止/)（Python / Go）
- [x] FST 的 TopN 搜索与零输出补全（`Util.TopNSearcher` 的有界优先队列与 `maxQueueDepth`；「0 output completion」补齐与 `assert foundZero`；`isComplete = rejectCount + topN <= maxQueueDepth`；根节点**不参与** output 上推、零弧不变式只约束非根） — 见 [FST的TopN搜索与零输出补全/](./FST的TopN搜索与零输出补全/)（Python / Go）

## 待研究

- [x] BM25 评分算法（已在 [BM25评分/](./BM25评分/)）
- [x] 中文/IK 分词器与倒排表的字段长度归一化（影响 BM25 的 avgdl）（已在 [分析器链与中文分词/](./分析器链与中文分词/)）
- [x] 向量检索（已在 [HNSW向量检索/](./HNSW向量检索/)）
- [x] IVF-PQ（Faiss）：向量量化与索引协同（已在 [IVFPQ向量量化/](./IVFPQ向量量化/)）
- [x] 段合并（已在 [段合并策略/](./段合并策略/)）
- [ ] Lucene 10 logical partitioning（合并已与搜索并行性脱钩）
- [x] 高亮（postings 的 positions/offsets 与 unified highlighter 的 OffsetSource 抉择）（已在 [高亮器与摘要片段生成/](./高亮器与摘要片段生成/)；FVH 的差异未做）
- [ ] 向量量化的 refine / 重排（PQ 的召回天花板如何靠二级精排突破）
- [x] suggester / completion（FST + 前缀权重）（已在 [FST与前缀补全suggester/](./FST与前缀补全suggester/) 与 [FST的TopN搜索与零输出补全/](./FST的TopN搜索与零输出补全/)）
- [ ] Lucene 10 BKD 的 1≤dims≤8 索引维压缩策略与 packed index 差异
- [ ] ConcurrentMergeScheduler 的合并线程配额与 backpressure
- [ ] HNSW 图构建时的多样性启发式（Lucene 的 `HnswGraphBuilder` 剪枝边策略）
