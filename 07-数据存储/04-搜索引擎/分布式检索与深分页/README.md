# 分布式检索两阶段（scatter-gather）、深分页与 search_type

## 一、简介

Elasticsearch 的一个搜索请求不是"在一台机器上跑一次查询"，而是协调节点把请求 **scatter** 到索引的每个分片、各自算完再 **gather** 回来归并。所有关于分页、打分不准、部分结果的疑问都出在这个结构里。

本 demo 用纯标准库把这条链路拆开：query 阶段每片回什么、fetch 阶段去哪几片、深分页为什么贵、`search_after` + PIT 为什么能规避、`search_type` 两种取值为什么会排出不同顺序。

## 二、原理详解

### 2.1 两阶段

```text
                ┌──────────── query 阶段（scatter） ────────────┐
协调节点 ──────▶│ shard 0 本地取 top (from+size) → 回 doc id + sort values
                │ shard 1 同上
                │ shard 2 同上
                └───────────────────────────────────────────────┘
       归并成全局有序 → 截取 [from, from+size)
                ┌──────────── fetch 阶段（gather） ────────────┐
                │ 只向**贡献了命中**的分片发 multi-get 取 _source
                └──────────────────────────────────────────────┘
```

关键点：

- query 阶段**不回 `_source`**，只回排序所需的 `sort values` 与文档标识，所以网络量小；
- **每个分片都要本地算出 `from + size` 条**（不是 `size` 条），这是深分页代价的根源；
- fetch 阶段只去命中分片——本 demo 断言 top3 全部来自分片 0 时，fetch 只触及 `[0]`，不是 3 片。

### 2.2 深分页：`from/size` 的成本是乘法

官方原文：搜索请求通常跨多个分片，**每个分片必须把请求的 hits 以及前面所有页的 hits 都装进内存**，深分页会显著增加内存与 CPU，甚至导致节点故障。

```text
from=0     size=10   ⇒ 候选    30 条（3 片 × 10）
from=100   size=10   ⇒ 候选   330 条（3 片 × 110）
from=1000  size=10   ⇒ 候选  3030 条（3 片 × 1010）
from=9900  size=100  ⇒ 候选 30000 条（3 片 × 10000）
```

并且默认情况下 **不能用 `from/size` 翻过 10000 条**——这是 `index.max_result_window` 的保护值；超过直接报错（`from + size must be less than or equal to: [10000]`）。

### 2.3 `search_after` + PIT

`search_after` 用**上一页最后一条的 sort values** 当游标取下一页：

- 每个分片的成本恒为 `size`，不累积历史页；
- 要求 `query` 与 `sort` 在整趟翻页中**保持不变**；
- 用 PIT 时 `from` 必须是 **0（默认）或 -1**；PIT 的 `keep_alive` 可以在每次搜索时续期；
- 排序**必须带唯一 tiebreaker**（官方示例是 `_id` 的一份开了 `doc_values` 的拷贝），否则同值文档会漏或重复。

官方明确：**不再推荐用 scroll 做深分页**，改用 `search_after` + PIT。

### 2.4 `search_type`：本地 IDF 与全局 IDF

`search_type` 的合法值只有两个：

| 取值 | 打分依据 | 特点 |
| --- | --- | --- |
| `query_then_fetch`（默认） | 分片**本地**的 term/document frequency | 更快，但各片语料不均衡时会偏 |
| `dfs_query_then_fetch` | **跨分片全局**频率 | 更准，但要多一轮预查询，更慢 |

构造一个反例（两片各 5 篇，词 `t` 在片 0 的 df=1、片 1 的 df=5，全局 df=6）：

```text
query_then_fetch     : docA(tf=1,片0)=1.3863   docB(tf=3,片1)=0.2610  ⇒ docA 在前
dfs_query_then_fetch : docA=0.5261             docB=1.5783            ⇒ docB 在前
```

**排序翻转**。这就是"本地频率"偏差的直观形态：小分片里的稀有词 IDF 被高估。

### 2.5 部分结果与并发

- `allow_partial_search_results=true`：有分片超时/失败时**返回部分结果**；`false`：**报错且不返回部分结果**。
- `max_concurrent_shard_requests` 默认 **5**（限制单节点并发分片请求数，保护集群）。
- `batched_reduce_size`：协调节点一次归并多少个分片结果。
- `preference`：默认用 **adaptive replica selection**（并考虑 allocation awareness）挑副本；也支持 `_only_local` / `_local` / `_only_nodes` / `_prefer_nodes` / `_shards:<id>` / 自定义串（同串路由到同样的分片顺序）。
- `routing`：把操作固定路由到特定分片。

## 三、对比：三种翻页方式

| 方式 | 单页成本 | 上限 | 是否保留索引快照 | 适用 |
| --- | --- | --- | --- | --- |
| `from` / `size` | 分片数 × (from+size) | 10000（`max_result_window`） | 否 | 浅分页、跳页 |
| `search_after` | 分片数 × size | 无 | 否（可配 PIT） | 深分页、顺序翻页 |
| `search_after` + PIT | 分片数 × size | 无 | **是**（`keep_alive` 可续期） | 导出、深翻 + 一致性 |
| `scroll` | — | — | 是 | **官方已不推荐** |

## 四、环境

- Python 3.9+（仅标准库）；Go 1.21+；C（C99）。无第三方依赖。

## 五、运行方式

```bash
cd python && python scatter_gather.py    # 14 条断言，全部实跑通过
cd go     && go run .
cd c      && cc -std=c99 scatter_gather.c -lm -o a.out && ./a.out
```

> Go 静态检查用 `python _docs/tools/go_sanity.py --spec check=2 <file>`（`check` 为 2 参签名）。

## 六、关键代码

query 阶段每片只回排序所需的最小信息：

```python
def query_phase(self, frm, size):
    return [{"shard": self.sid, "doc": h[0], "sort": [h[1], h[0]]}
            for h in self.hits[:frm + size]]     # ← 取 from+size，不是 size
```

协调节点的窗口保护与部分结果开关：

```python
if frm + size > MAX_RESULT_WINDOW:
    raise ValueError("... from + size must be less than or equal to: [%d] ...")
...
if bad and not allow_partial:
    raise RuntimeError("Search rejected: shard failures on %s" % bad)
```

两种 `search_type` 的差别只在 IDF 用哪个 `n` 和 `df`：

```python
def idf(n, df):  return math.log(1.0 + (n - df + 0.5) / (df + 0.5))
# query_then_fetch : idf(分片内 n, 分片内 df)
# dfs_*            : idf(全局 n, 全局 df)
```

## 七、性能边界与注意事项

1. **分片数放大深分页成本**：3 片 × `from=9900,size=100` = 30000 条候选；20 片就是 20 万。分片数不是越多越好。
2. **`max_result_window` 是保护线不是性能线**：调到 100 万不等于能翻到 100 万，只是不再报错。
3. **`search_after` 必须配唯一 tiebreaker**，否则排序值相同的文档会在翻页中丢失或重复。
4. **`dfs_query_then_fetch` 不是"更准"的万能选项**：它多一轮 round-trip，且分片数多时代价线性上升；数据分布均匀时与默认值几乎无差。
5. **`preference` 用自定义串可以做"结果稳定"**：同一串路由到同样的分片顺序，避免副本间得分抖动造成的翻页错乱。
6. 本 demo 的 IDF 用 Lucene 概率版公式，tf 常量是人为给定的；重点是**两种频率口径的对比**，不是打分函数本身。

## 八、参考资料（均为本轮实际读过）

1. Search API（`search_type` / `allow_partial_search_results` / `batched_reduce_size` / `max_concurrent_shard_requests` / `preference` / `routing`）— <https://www.elastic.co/guide/en/elasticsearch/reference/current/search-search.html>
2. Paginate search results（`from/size` 深分页代价、`max_result_window`、`search_after`、PIT、tiebreaker、不再推荐 scroll）— <https://www.elastic.co/guide/en/elasticsearch/reference/current/paginate-search-results.html>
