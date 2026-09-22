# Cassandra 二级索引与 SAI

## 一、简介

Cassandra 的传统二级索引（2i）是**本地索引**：每个节点只索引自己持有的数据，一次带索引条件的查询
要广播到所有节点（scatter-gather），每个节点查本地索引再回基表取行。SAI（Storage-Attached Index）
沿用了这个"本地索引 + 全局广播"的骨架，但把磁盘格式和索引布局重做了一遍：

- 字符串索引从 SASI 的旧格式换成 **byte-ordered trie**
- 数值索引换成 **block-oriented balanced tree**（BBTree，类似 Lucene 的 BKD）
- 索引的 offset / token 信息**只在 SSTable 级存一份**，多列索引时省空间

本 demo 的重点是那句容易被忽略的事实：**SAI 的 postings 里存的是 SSTable 局部 rowID**，
跨 SSTable 合并时必须带上 SSTable 标识。

## 二、原理

### 2.1 SAI 是 local index，不是全局索引

> Storage-attached indexing is a column based **local** secondary index implementation for Cassandra.
> Global queries are implemented on the back of C* range reads.

所以：

- 查 `WHERE name = 'John'` 要发给**所有**节点，各节点用本地 SAI 找行，再汇总；
- 结果**只保证 token 序**（`Only token ordering of results is supported`），
  想按别的字段排序必须自己排。

### 2.2 两层组件：per-SSTable + per-column

`V1OnDiskFormat` 里写得很清楚，writer 与完成标记都分两级：

| 层 | writer | 完成标记 | 内容 |
| --- | --- | --- | --- |
| per-SSTable | `PerSSTableIndexWriter`（`SSTableComponentsWriter`） | `GROUP_COMPLETION_MARKER` | PrimaryKeyMap、token/offset |
| per-column | `PerColumnIndexWriter`（`SSTableIndexWriter` / `MemtableIndexWriter`） | `COLUMN_COMPLETION_MARKER` | trie / bbtree、postings |

建索引是"看起来像 compaction"的（官方：索引构建对运维可见为 compaction，跑在 compaction 线程上）。

### 2.3 RowMapping：PrimaryKey → rowID，只在 FLUSH 时建

```java
public static RowMapping create(OperationType opType) {
    if (opType == OperationType.FLUSH) return new RowMapping();
    return DUMMY;   // add/get 全是空操作
}
```

`RowMapping` 内部是 `InMemoryTrie`（OFF_HEAP）。flush 时先把 term → PrimaryKeys（memtable index）
和 PrimaryKey → rowID 对齐，产出 term → postings（rowID 列表）。

`PrimaryKeys` 有两种形态，按 `hasClustering()` 选：

```java
indexDescriptor.hasClustering() ? new WidePrimaryKeyMap.Factory(...) : new SkinnyPrimaryKeyMap.Factory(...)
```

### 2.4 rowID 是 SSTable 局部的（本 demo 的核心）

每个 SSTable 都从 0 开始编号 rowID。SSTable A 的 rowID 1 和 SSTable B 的 rowID 1 是**两行不同的数据**。
因此全局查询必须把 `(sstable, rowID)` 二元组解析成 PrimaryKey，之后才能合并。

本 demo 里：

- A: rowID 0 → pk_a0(token 40)，rowID 1 → pk_a1(token 20)
- B: rowID 0 → pk_b0(token 5)，rowID 1 → pk_b1(token 30)
- `John` 在 A 命中 rowID 1、在 B 命中 rowID 0
- 正确结果：`[pk_b0(5), pk_a1(20)]`（按 token 升序）
- 把 rowID 当全局的错误实现：`[pk_a0(40), pk_a1(20)]` —— 完全错了

### 2.5 多列索引共享一套 rowID

同一 SSTable 上给多个列建索引时，它们共用同一份 PrimaryKeyMap，
这就是 README 里说的 "related offset and token information stored only once at the SSTable level"。

### 2.6 写路径：不需要 read-before-write

> Index updates are synchronous with mutations and do not require any kind of read-before-write.

memtable index 在写入时就同步维护 term → PrimaryKeys，flush 时再转成 postings。

## 三、对比

| | 传统 2i | SASI | SAI |
| --- | --- | --- | --- |
| 索引粒度 | 每节点本地 | 每 SSTable | 每 SSTable，两层组件 |
| 字符串结构 | 隐藏实现 | 旧格式 | byte-ordered trie |
| 数值结构 | 不支持范围 | 旧格式 | block-oriented balanced tree |
| read-before-write | 需要 | 需要 | **不需要** |
| 结果顺序 | token 序 | token 序 | token 序 |
| 构建可见性 | — | — | 以 compaction 形式呈现 |

## 四、环境

- Python 3.8+（仅标准库）；Go 1.21+（仅标准库）
- 无需 Cassandra

## 五、运行

```bash
cd python && python selfcheck_sai.py   # 35 条断言
cd python && python main.py
cd go     && go run .
```

## 六、关键代码

| 文件 | 内容 |
| --- | --- |
| `python/sai.py` | `PrimaryKey` / `RowMapping` / `MemtableIndex` / `SSTableIndex.build` / `search` / `naive_search` |
| `python/selfcheck_sai.py` | 35 条断言，含正确 vs 错误实现的成对对比 |
| `python/main.py` | 六个场景演示 |
| `go/sai.go` + `go/main.go` | 同模型 Go 转写 |

## 七、性能边界

- 索引是 per-SSTable 的：**SSTable 越多，一次查询要合并的 postings 列表越多**
- 全局查询要 scatter 到所有节点，代价随节点数线性增长
- 索引构建占用 compaction 线程，且受全局 segment 内存上限约束
  （源码里 `SEGMENT_BUILD_MEMORY_LIMIT` 注册为 metric）
- 只支持 token 序 —— 需要其他顺序只能在客户端排
- 索引文件可以随整个 SSTable 一起 stream（官方：entire SSTable streaming 开启时组件可整体流式传输）

## 八、坑

1. **把 rowID 当全局 ID**：这是本 demo 演示的头号错误，跨 SSTable 合并必错。
2. **以为 SAI 是全局索引**：它是 local index，查询会广播到全部节点。
3. **依赖结果顺序**：只有 token 序是有保证的。
4. **以为 flush 之外也有 RowMapping**：`create()` 在非 FLUSH 时返回 DUMMY，`get()` 恒为 -1。
5. **skinny/wide 混用**：有 clustering 列的表走 `WidePrimaryKeyMap`，无 clustering 走 `SkinnyPrimaryKeyMap`，
   两者的 PrimaryKey 编码不同。
6. **忽略完成标记**：per-SSTable 与 per-column 各有自己的 completion marker，
   只检查一个会误判索引已可用。
7. **本模型是结构模型，不是性能模型**：postings 用 Python list / Go slice 表示，
   真实的 trie 与 bbtree 是分块压缩的，别拿这里的内存占用当真实值。

## 九、参考资料（均为本轮实际读取）

- `apache/cassandra@trunk` `src/java/org/apache/cassandra/index/sai/README.md` ——
  local index 定位、trie / bbtree 磁盘结构、offset 与 token 只在 SSTable 级存一份、
  无 read-before-write、只支持 token 序、索引构建以 compaction 形式呈现：
  https://github.com/apache/cassandra/blob/trunk/src/java/org/apache/cassandra/index/sai/README.md
- `src/java/org/apache/cassandra/index/sai/disk/RowMapping.java` ——
  `create(OperationType)` 只在 FLUSH 时建真映射、`InMemoryTrie(OFF_HEAP)`、term → postings 的合并过程：
  https://github.com/apache/cassandra/blob/trunk/src/java/org/apache/cassandra/index/sai/disk/RowMapping.java
- `src/java/org/apache/cassandra/index/sai/disk/v1/V1OnDiskFormat.java` ——
  两层 writer、`GROUP_COMPLETION_MARKER` / `COLUMN_COMPLETION_MARKER`、
  `hasClustering()` 决定 Wide/Skinny PrimaryKeyMap、`SEGMENT_BUILD_MEMORY_LIMIT`：
  https://github.com/apache/cassandra/blob/trunk/src/java/org/apache/cassandra/index/sai/disk/v1/V1OnDiskFormat.java
- `src/java/org/apache/cassandra/index/sai/disk/v1/bbtree/` 目录 ——
  `BlockBalancedTreeWriter` / `BlockBalancedTreeReader` / `NumericIndexWriter` / `LeafOrderMap`：
  https://github.com/apache/cassandra/tree/trunk/src/java/org/apache/cassandra/index/sai/disk/v1/bbtree
