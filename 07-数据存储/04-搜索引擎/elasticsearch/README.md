# Elasticsearch 倒排索引与近实时搜索（REST API）

## 简介

Elasticsearch 是基于 Apache Lucene 的分布式搜索与分析引擎：数据以 JSON 文档形式存入 *index*（索引），通过 HTTP REST API 读写。它解决的核心问题是**全文检索**——用倒排索引（inverted index）让"按词找文档"变成一次有序 term 字典上的查找，而不是逐行扫描。

- **关键概念**
  - **文档（document）**：JSON 对象，一组字段的键值对，有唯一 `_id`；`_source` 保存原始文档体。
  - **索引（index）**：存储与交互的基本单位，由 documents + mappings + settings 组成，底层被切分为多个 shard 分布到集群各节点。
  - **倒排索引（inverted index）**：列出所有出现过的唯一词（term），以及每个 term 出现在哪些文档中；每个 text 字段有自己独立的倒排索引。
  - **分片（shard）**：一个自包含的 Lucene 索引；主分片数创建后固定，副本分片数可动态调整。
  - **refresh / 近实时（NRT）**：把内存缓冲区写入一个新 segment 并打开、使其可被搜索的轻量过程；默认每 1 秒一次，所以写入到可搜索之间有约 1 秒延迟。
- **历史背景**：Shay Banon 2004 年为给妻子找菜谱开发了 Compass，2010 年重命名为 Elasticsearch 发布——把 Lucene 的能力包装成带 REST API 的分布式服务。

## 原理详解

### 1. 倒排索引：term → 文档列表

正排是"文档 → 内容"，倒排是"词 → 文档"。以《Elasticsearch 权威指南》的经典示例，三个文档去掉停用词后（源自官方指南 *Making Text Searching* 一节）：

| Term   | Doc 1 | Doc 2 | Doc 3 |
| ------ | ----- | ----- | ----- |
| brown  | X     |       | X     |
| fox    | X     | X     | X     |
| quick  | X     | X     |       |
| the    | X     |       | X     |

term 列表是有序的，查找一个词就是一次二分查找，然后直接拿到它的 posting list（文档列表）。倒排索引还可为每个 term 存储：包含该词的文档数（df）、该词在某文档中出现的次数（tf）、文档长度等统计信息——这些正是 BM25 相关性评分的输入。

### 2. 按字段选择数据结构（per-field data structures）

Elasticsearch 默认索引每个字段，且**按字段类型选用专门的数据结构**（官方文档 *Data in: documents and indices*）：

| 字段类型 | 底层结构 | 支持的操作 |
| --- | --- | --- |
| `text` | 倒排索引（经分词器分析） | 全文检索、相关性评分 |
| `keyword` | 倒排索引（整串作为一个 term，不分析） | 精确匹配、排序、聚合 |
| 数值 / 日期 | BKD 树 | 范围查询 |
| geo_point / geo_shape | BKD 树 | 地理查询 |

这解释了 `text` 与 `keyword` 的本质区别：同一个字符串，`text` 会被分析成多个 token 分别进倒排表，`keyword` 整串就是一个 term。

### 3. 写入路径与近实时搜索（NRT）

Lucene 的 "index" = 一组不可变 segment + 一个 commit point。写入文档的完整链路（官方文档 *Near real-time search*）：

```
 文档写入
    │
    ▼
 in-memory indexing buffer ──(refresh: 写出并打开新 segment)──▶ 新 segment
                                                                 │
                                                                 ▼
                                                     filesystem cache（便宜，文件已在缓存即可读）
                                                                 │
                                                                 ▼
                                                     flush 到磁盘（昂贵，含 fsync，即真正的 commit）
```

1. 文档先进入内存索引缓冲区，此时**搜索不到**。
2. **refresh**：把缓冲区内容写成一个新 segment——先写文件系统缓存（便宜），不 fsync；文件进入缓存后即可打开读取，其中的文档立即可搜索。
3. 默认**每 1 秒** refresh 一次（且仅对最近 30 秒内收到过搜索请求的索引生效），这就是"近实时（约 1 秒内可搜索）"的由来。
4. 真正的 commit（flush 到磁盘 + fsync）由 translog 保障持久性，比 refresh 昂贵得多，所以不能每次写入都做。
5. segment 不可变带来的好处：无锁、可被内核缓存长期复用、便于压缩；代价是更新/删除靠标记 + 后台段合并完成。

### 4. 查询路径：match 与 term

- `match` 查询会对查询文本**先分析再匹配**（与索引时相同的分析链），是全文检索的标准查询；默认 `operator: or`，可用 `and` 收紧。
- `term` 查询不做分析，直接拿整个词去精确匹配 term 字典——适合 `keyword` 字段或数值；对 `text` 字段用 `term` 常常因为大小写/分词不一致而查不到。

### 5. 核心 REST API（本 demo 用到的）

| 操作 | 方法 + 路径 | 说明 |
| --- | --- | --- |
| 删除索引 | `DELETE /<index>` | 整库删除（demo 复跑用） |
| 创建索引 | `PUT /<index>` | 请求体可带 `mappings` / `settings` |
| 索引文档 | `PUT /<index>/_doc/<id>` 或 `POST /<index>/_doc` | 后者自动生成 `_id` |
| 刷新 | `POST /<index>/_refresh` | 手动触发 refresh，立即可搜索 |
| 搜索 | `GET /<index>/_search` | 请求体为 Query DSL JSON |
| 文本分析 | `GET /<index>/_analyze` | 观察分词器把文本切成哪些 token |

## 对比 / 选型

| 维度 | Elasticsearch | MySQL `LIKE '%kw%'` | MongoDB |
| --- | --- | --- | --- |
| 全文检索 | 倒排索引，毫秒级，带相关性评分 | 无法走索引，全表扫描，无评分 | 有 text 索引但功能较弱 |
| 写入可见性 | NRT，默认 1s 后可搜 | 事务提交即可见 | 写入即可见 |
| 一致性/事务 | 近实时、最终一致 | 强一致、ACID | 单文档原子 |
| 适用规模 | PB 级，水平扩展（分片） | 单机为主 | 分布式 |
| 典型用途 | 搜索、日志、可观测性 | 交易型业务数据 | 文档型业务数据 |

常见架构：MySQL 存正本，通过 CDC/双写把数据同步到 ES 做搜索——各取所长。

## 环境准备

- 操作系统：任意（本 demo 只依赖 HTTP）
- Elasticsearch ≥ 8.x 单节点，本地启动：
  ```bash
  docker run -d --name es -p 9200:9200 \
    -e discovery.type=single-node -e xpack.security.enabled=false \
    docker.elastic.co/elasticsearch/elasticsearch:8.15.0
  ```
- Python ≥ 3.8（仅标准库 `urllib` / `json`）
- Go ≥ 1.20（仅标准库 `net/http` / `encoding/json`）

## 运行方式

### Python

```bash
python3 python/es_demo.py
```

### Go

```bash
cd go && go run es_demo.go
```

两个程序执行完全相同的 7 步流程（见下），预期输出一致。

## 关键代码片段

Python 版核心（`python/es_demo.py`）：先建带显式 mapping 的索引，写入 3 篇文档后**立即搜索 → 0 命中**，手动 refresh 后再搜 → 命中，直观演示 NRT：

```python
# 第 2 步：显式 mapping——title 走倒排+分词，tag 整串精确匹配
http("PUT", "/demo-es", {"mappings": {"properties": {
    "title": {"type": "text"},
    "tag":   {"type": "keyword"},
}}})

# 第 4/5/6 步：NRT 演示——refresh 前后各搜一次
before = http("GET", "/demo-es/_search", {"query": {"match": {"title": "search"}}})
http("POST", "/demo-es/_refresh")
after = http("GET", "/demo-es/_search", {"query": {"match": {"title": "search"}}})
```

`_analyze` 观察标准分词器如何把整句切成小写 token（这些 token 就是倒排表里的 term）：

```python
tokens = http("GET", "/demo-es/_analyze",
              {"field": "title", "text": "Elasticsearch Makes Text Searchable"})
# 输出: elasticsearch / makes / text / searchable —— 全部小写化并逐词切分
```

## 性能与边界

- **查询**：单 term 查找是 term 字典上的二分 + posting list 合并，亚毫秒级；`match` 多 term 退化为布尔查询组合。
- **refresh 成本**：refresh 远轻于 commit，但每次产生一个新 segment，频繁手动 refresh（比如每写一篇文档就刷一次）会显著拖慢写入并造成大量小段；生产中应调整 `index.refresh_interval` 而不是手动刷（官方指南明确警告）。
- **refresh_interval 陷阱**：其值为时间单位（`1s`、`30s`）；裸写 `-1` 表示关闭自动刷新，裸写数字 `1` 表示 **1 毫秒**——官方文档原话"sure way to bring your cluster to its knees"。
- **分片规模**：官方建议单分片 10-50GB；主分片数建好后不可改，改字段类型需要 reindex。

## 注意事项与常见坑

1. **刚写入搜不到** → 现象：index doc 后立即 search 返回 0 hits。原因：默认 1 秒 refresh 间隔（NRT）。规避：等待 ~1s，或写测试时手动 `POST _refresh`，或写请求带 `?refresh=wait_for`。
2. **对 text 字段用 term 查不到** → 现象：文档里明明有 "Elasticsearch"，`term: {"title": "Elasticsearch"}` 却 0 命中。原因：索引时被分析成小写 `elasticsearch`，term 查询不分析、大小写不匹配。规避：改用 `match`，或把字段定义为 `keyword`。
3. **text 当 keyword 用** → 现象：对 `text` 字段做排序/聚合报错或结果混乱。原因：`text` 被分词成多值。规避：多字段 mapping（`text` + `.keyword` 子字段）。
4. **依赖第三方客户端库** → 本 demo 刻意只用标准库：ES 的全部能力就是一个 HTTP + JSON 协议，curl / urllib / net/http 就能覆盖，理解协议比记 SDK 重要。

## 参考资料（实际阅读过的权威来源）

- [Index fundamentals | Elastic 官方文档](https://www.elastic.co/guide/en/elasticsearch/reference/current/documents-indices.html) — index 的组成（documents/mappings/settings）、分片模型、segment 与 refresh_interval 默认值
- [Near real-time search | Elastic 官方文档](https://www.elastic.co/es/guide/en/elasticsearch/reference/current/near-real-time-search) — refresh 的定义、内存缓冲区 → 文件系统缓存 → 磁盘 flush 的完整链路、1 秒默认间隔
- [Data in: documents and indices | Elastic 官方文档 (7.0)](https://www.elastic.co/guide/en/elasticsearch/reference/7.0/documents-indices.html) — 倒排索引定义、text 走倒排 / 数值与 geo 走 BKD 树、动态映射
- [Making Text Searchable | Elasticsearch: The Definitive Guide](https://elastic.co/guide/en/elasticsearch/guide/current/making-text-searchable.html) — 倒排索引 term→doc 表格、segment 不可变的收益
- [Match query | Elasticsearch 官方参考](https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl-match-query.html) — match 与 term 的区别、operator/fuzziness 参数、完整请求示例
