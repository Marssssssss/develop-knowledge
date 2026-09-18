# Loki 日志存储与 LogQL

## 简介

Loki 是 Grafana 的日志聚合系统，官方定位是「像 Prometheus，但是针对日志」。核心取舍只有一条：
**只索引标签，不索引日志正文**。正文按流压进 chunk 顺序存储，查询时先用流选择器定位 chunk，再解压逐行扫。

由此派生两个后果：索引体积正比于**标签基数**、与日志量几乎无关，单位成本极低；但任何按正文找日志的
查询都必须**扫过命中流的全部正文**，是线性扫描而非倒排检索 —— 于是「怎么切标签」直接决定查询成本，
`max_label_names_per_series` 这类限额不是形式主义。

本 demo 把写路径（distributor → ingester → chunk → WAL）、查询语义（LogQL 三层 + 锚定规则）与配置
边界（limits / schema）拆成可实跑的最小实现。

## 原理详解

### 1. 组件与职责边界

写路径：**distributor**（校验限额、算哈希、按副本因子分发，无状态）→ **ingester**（内存攒 chunk、
定期刷对象存储，有状态，靠 WAL 崩溃重放）。读路径：**query-frontend**（拆分/排队/并行化，无状态）→
**querier**（取 ingester 内存中未刷写的 chunk + 从存储拉历史 chunk）→ **index-gateway**（代理索引查询）。
**compactor** 合并索引并按保留期删除，单实例运行。

三种部署模式共用同一份代码：**单二进制**（`-target=all`）、**simple scalable**（拆成 read / write /
backend 三组）、**微服务**（每个组件独立）。simple scalable 的分组不是按「进程」而是按「读写路径」切的。

### 2. 时序与去重的三条规则（G 组）

同一个流内：

1. 时间戳严格小于最后一条 → **拒行并回错**；
2. 时间戳与正文**都**相同 → **静默忽略**，既不计错也不计数（客户端重试的正常结果）；
3. 时间戳相同但正文不同 → **接受**。同一纳秒出现多行日志是合法的，不是 bug。

第 3 条最容易被想成「同时间戳一律去重」，进而写出错误的去重键。

### 3. chunk 滚动的三条件与编码

chunk 在满足任一条件时被切掉并刷写：

| 条件 | 默认值 | 说明 |
| --- | --- | --- |
| `chunk_idle_period` | 30m | 自**最后一次写入**起算的空闲期 |
| `chunk_target_size` | 1572864（1.5 MiB） | 按**压缩后**大小算，不是未压缩 |
| `max_chunk_age` | 2h | chunk 存活上限 |

另有 `chunk_block_size` 262144（256 KiB，未压缩）决定 block 粒度、`chunk_retain_period` 默认 0s。
编码 `chunk_encoding` **默认 gzip**，而官方最佳实践**推荐 snappy** —— 默认值 ≠ 推荐值，两者要按
「配置参考手册写的 default」与「最佳实践页写的推荐」分别引用，不能互相替代。

### 4. LogQL 的分层与两种相反的锚定语义

一条查询自左向右：**流选择器** → **行过滤** → **解析/标签过滤** → **unwrap/范围聚合**。
关键分歧在于同一个 `~` 在两层的语义**相反**：

- 流选择器的 `=~`/`!~` **完全锚定**（等价自带 `^...$`），`{app=~"api"}` 不匹配 `api-server`；
- 行过滤的 `|~`/`!~` 是**搜索语义**、非锚定，`|~ "api"` 能命中含 `api` 的任意行。

另两条反直觉规则：**缺失标签 ≡ 空字符串** —— 所以 `{env!="prod"}` 会命中**根本没有** `env` 标签的流，
`{env!~".+"}` 同样命中（空串不匹配 `.+`，取反成立）；**`|=` 是子串包含且大小写敏感**。

### 5. `__error__` 与指标查询的门禁（E 组）

解析失败**不丢行**，只在标签集里写 `__error__`；多个错误用逗号累加。想丢弃必须显式写
`| __error__ = ""`。更关键的是**指标查询不允许携带错误**：只要范围聚合前还残留 `__error__`，
整条查询报错而不是忽略那几行 —— 这正是 `| unwrap ...` 后必须补 `| __error__ = ""` 的全部原因。

### 6. unwrap 会**消费**被 unwrap 的标签

`unwrap` 取到数值后必须把该标签从标签集删掉。否则每个取值都会裂成一个独立序列、每序列只有 1 个
样本，`avg`/`stddev`/`quantile_over_time` 全部退化成恒等运算，结果看着「对」其实毫无意义。

### 7. 范围聚合的分工

| 输入类型 | 可用函数 |
| --- | --- |
| 日志范围向量 | `rate` `count_over_time` `bytes_rate` `bytes_over_time` `absent_over_time` |
| unwrap 后的数值范围向量 | `sum` `avg` `max` `min` `first` `last` `stddev` `stdvar` `quantile_over_time` |

`count_over_time` **不接受** unwrap 后的数值。两个数值口径：`stddev`/`stdvar` 用**总体**方差
（除以 N，不是 N−1）；`quantile_over_time` 用**线性插值**，rank = φ·(N−1)，所以 `[10,20,30,40]`
的 0.5 分位是 **25**，既不是 20 也不是 30。

### 8. 限额与「均摊」（A/B 组）

`ingestion_rate_mb`（默认 4）、`ingestion_burst_size_mb`（默认 6）、`ingestion_rate_strategy` 默认
`global`。`global` 下速率额度按 ring 里**健康实例数均摊**，而 **burst 不均摊**、始终是每个 distributor
的本地阈值。于是扩容会**降低**每实例速率阈值却不动 burst —— 这正是「滚动重启期间 429 与限流同时出现」
的成因。`local` 策略不做均摊，集群实际总额被放大 N 倍。

### 9. schema 与存储

推荐且事实上强制的组合是 `store: tsdb` + `schema: v13`：structured metadata 与原生 OTLP 摄入默认开启，
两者都要求 tsdb + v13+，否则 Loki **拒绝启动**并抛 `CONFIG ERROR:`。`boltdb-shipper` 已弃用、将在 4.0
移除；tsdb 的 `index.period` 必须是 **24h**，`row_shards` 自 schema v10 起默认 **16**。新增 schema 段的
`from` 日期方向也反着：**追加**新段要在**未来**、**全新安装**要在**过去**，写反了数据不可读。

## 对比 / 选型

| 维度 | Loki | Elasticsearch / ELK |
| --- | --- | --- |
| 索引对象 | 仅标签 | 正文分词后的倒排索引 |
| 正文检索 | 命中流内线性扫描 | 倒排检索（快得多） |
| 索引体积 | 与标签基数相关，与日志量弱相关 | 与日志量近似线性 |
| 查询语言 | LogQL（PromQL 风格 + 日志管线） | Lucene / KQL |
| 多租户 | `X-Scope-OrgID` header + 存储路径前缀隔离 | 索引/别名隔离 |
| 适合 | 已知标签、按服务/环境排查、成本敏感 | 全文检索、复杂聚合、字段自由 |

## 环境准备

- 操作系统：任意（纯标准库，无第三方依赖）
- Python 3.9+（本机 3.13.12 **实跑**，209 项断言全绿）
- Go 1.21+ 与 C99（**本机无 go / gcc / cc / clang，均未实跑**）：走人工审查 + 机械核查（见上）

## 运行方式

```bash
cd python && python demo.py          # 本机实跑：PASSED 209  FAILED 0
cd go && go run .                    # 需自备工具链
cd c && cc -std=c99 -O0 -o loki_demo loki_demo.c -lm && ./loki_demo
```

## 关键代码片段

```python
# logql_ast.py —— 同一个 ~ 在两层语义相反：选择器锚定、行过滤不锚定
cand = labels.get(self.label, "")           # 缺失标签按空串处理，不是"不匹配"
if self.op == "=~":
    return self._compiled.fullmatch(cand) is not None   # 流选择器：完全锚定
# LineFilter.accepts 则是 re.search(line) is not None    # 行过滤：搜索语义，非锚定
```

```python
# logql_eval.py —— unwrap 成功必须删掉被消费的标签，否则每条取值裂成独立序列
value = _to_float(entry.labels.get(stage.field))
if value is None:
    entry.append_error(SAMPLE_EXTRACTION_ERR)        # 不丢行，只打错误标签
else:
    entry.value, entry.has_value = value, True
    del entry.labels[stage.field]                    # 关键的一步
```

```python
# loki_limits.py —— global 均摊 rate，但 burst 不均摊（官方原文如此）
def distributor_rate_mb(rate_mb, instances, strategy):
    return rate_mb / instances if strategy == "global" else rate_mb

def distributor_burst_mb(burst_mb, instances, strategy):
    return burst_mb
```

## 性能与边界

- **单条流的正文检索是线性扫描**：`|~ "..."` 只能边解压边扫，流越宽越慢。这是「标签切太粗」的直接代价。
- **指标查询遇到 `__error__` 整条失败**，不是丢掉出错的行 —— 这一条决定了 `unwrap` 之后的写法。
- **`quantile_over_time` 是插值而非取序位**（`[10,20,30,40]` 的 0.5 分位是 25，样本里没有 25）；
  **`stddev` 是总体口径**（除以 N），样本量小时与「样本标准差」差异明显。
- chunk 目标大小按压缩后计，压缩比随正文重复度剧烈变化，故 `chunk_target_size` 到达时间不可预测，
  `max_chunk_age` 才是硬上界；本 demo 的压缩比是**说明性参考值**（业界常见 3~6 倍）非实测数据，
  指纹亦以 SHA-256 前 16 位代替 xxhash64，**不声称与真实 Loki 同值**。

## 注意事项与常见坑

1. **同时间戳不同正文会被接受**，不是去重。去重键是「时间戳 + 正文」两者。
2. **`{env!="prod"}` 命中没有 `env` 标签的流**；`{env!~".+"}` 也是。要表达「有这个标签」得写 `env=~".+"`。
3. **`=~` 完全锚定，`|~` 不锚定**。把 `{app=~"api"}` 当「包含 api」是最常见的误解。
4. **解析失败不丢行**，只打 `__error__`，多个错误逗号累加；要丢必须显式过滤。
5. **指标查询带 `__error__` 会整条失败** —— `unwrap` 后要补 `| __error__ = ""`。
6. **unwrap 不删标签会让聚合退化**：每个取值一条序列，每序列 1 个样本。
7. **`count_over_time` 不接受 unwrap 后的数值**；两类范围向量函数集不可互换。
8. **limits 默认值与最佳实践推荐值常不一致**：`chunk_encoding` 默认 gzip / 推荐 snappy；
   `max_label_names_per_series` 默认 30 / 推荐 15。引用时必须说清是哪一份文档。
9. **扩容会降低每实例速率阈值**（global 均摊）而 burst 不变；限流告警要看 per-distributor 指标。
10. **structured metadata 强制 tsdb + v13**，配置不对进程直接起不来，不是降级运行；**schema 段 `from` 方向相反**（追加在未来、全新安装在过去）。
11. 判据边界要写死：本 demo 用「标签数恰好 30 通过 / 31 被拒」「标签名恰好 1024 通过 / 1025 被拒」钉住 `>` 与 `>=`。

## 参考资料（实际阅读过的权威来源）

- [Loki overview](https://grafana.com/docs/loki/v2.2.x/overview/) — Loki 定位、「只索引标签」的核心
  取舍、三种部署模式（单二进制 / simple scalable / 微服务）。
- [Components](https://grafana.com/docs/loki/next/get-started/components/) — distributor / ingester /
  querier / query-frontend / index-gateway / compactor 的职责边界与哈希环、副本因子、读写路径划分。
- [Configuration](https://grafana.com/docs/loki/v2.6.x/configuration/) — 配置参考手册的逐项 `default =
  ...`：`chunk_idle_period` 30m、`chunk_target_size` 1572864（1.5 MB）、`chunk_block_size` 262144（256 KB）、
  `max_chunk_age` 2h、`chunk_encoding` gzip、`ingestion_rate_mb` 4、`ingestion_burst_size_mb` 6、
  `ingestion_rate_strategy` global、`max_line_size` 256KB、`max_label_names_per_series` 30、
  `max_label_name_length` 1024、`max_label_value_length` 2048（本 demo A/H 组断言的直接依据）。
- [Schema（Grafana Enterprise Logs）](https://grafana.com/docs/enterprise-logs/latest/manage/storage/schema/)
  — tsdb + v13 组合要求、`boltdb-shipper` 弃用与 4.0 移除、`index.period` 必须 24h、`row_shards` 默认
  16、schema 段 `from` 的两个相反方向、structured metadata 的强制要求与 `CONFIG ERROR:` 启动期行为。
- [Troubleshoot ingesting logs](https://grafana.com/docs/loki/v3.5.x/operations/troubleshooting/troubleshoot-ingest)
  — 摄入侧排障：限额触发、流数上限、时间戳乱序与重复行的处置口径（G 组依据）。
- [Configure best practices](https://grafana.com/docs/loki/v3.6.x/configure/bp-configure) — 推荐 snappy
  （与默认 gzip 不同）、标签数压到 15（与默认 30 不同）、低基数标签的切分原则。**「默认值 vs 推荐值」
  的双处标注即源于此页与配置参考手册的差异。**
- [LogQL query reference](https://grafana.com/docs/loki/latest/query/query_reference/) — 流选择器正则
  锚定、缺失标签等同空值、行过滤的搜索语义、`__error__` 传播与「指标查询不允许携带错误」、`unwrap`
  的标签消费、两类范围向量的函数集、`stddev`/`stdvar` 总体方差口径与 `quantile_over_time` 线性插值。

> `chunk_target_size` 官方注释为「1.5 MB」，本 demo 据此按 1024 进制换算；按 1000 进制理解该注释会有数值差异，分歧已在 README 与代码注释两处标注。
