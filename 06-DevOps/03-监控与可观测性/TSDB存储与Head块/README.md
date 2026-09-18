# TSDB 存储与 Head 块

## 简介

Prometheus 自带单机时序数据库（TSDB），把摄入的样本落成一套**不可变块 + 预写日志**的
结构。理解它才能回答三个运维问题：**盘要多大**、**保留期怎么配**、**崩溃后会丢多少数据**。

关键概念：

- **2h 块（block）**：样本按 2 小时窗口分组落盘，块一旦写完就不可变。
- **Head 块**：当前正在写入的块常驻内存，靠 WAL 保证崩溃可恢复。
- **WAL**：`wal/` 下每段 128 MB 的顺序日志，至少保留 3 段。
- **压缩（compaction）**：后台把小块合并成大块，跨度上限 `min(保留期 10%, 31 天)`。
- **倒排索引**：`label → 升序 series ref 列表`，多条件查询是若干有序表求交集。

## 原理详解

### 1. 磁盘布局

```text
./data
├── 01BKGV7JBM69T2G1BGBGM6KB12/     # 一个块，目录名是 ULID
│   ├── chunks/000001               # 样本，每段最大 512 MB
│   ├── index                       # 指标名/标签 → chunks 中的序列
│   ├── meta.json                   # 时间范围、统计、压缩层级
│   └── tombstones                  # 删除记录（不立即改写 chunk）
├── chunks_head/000001              # Head 的 mmap chunk
└── wal/
    ├── 000000002                   # 每段 128 MB
    └── checkpoint.00000001/00000000
```

**ULID 目录名**不是随意取的：48 位毫秒时间戳在前、80 位随机数在后，编码成 26 个
Crockford Base32 字符（`26×5 = 130` 位，最高 2 位恒为 0），因此**字典序等于时间序**，
列块、挑压缩候选都能直接按名字排序。

### 2. 写入与崩溃恢复

```text
scrape → Appender → 同步写 WAL（128MB/段） → 异步追加 Head（内存，≈2h）
                                    ↓ flush（约 2h 一次）
                              只读磁盘 Block → Compaction 合并 → Retention 删除
```

WAL 记录类型（`tsdb/record` 包）：`Series` / `Samples` / `Histograms` /
`FloatHistograms` / `Exemplars` / `Tombstones` / `Metadata` / `MmapMarkers`；乱序样本走
另一条 `wbl/` 日志。重放从最新 `checkpoint.NNNNNN`（NNNNNN 为该检查点覆盖到的段号）
之后的段开始，并按 `series ref` 分片并行（worker 数默认 `GOMAXPROCS`）。

### 3. 压缩与保留

- 压缩产物跨度上限 = `min(保留期 × 10%, 31 天)`。保留 15d → **36h**；90d → **9d**；
  365d 时 10% 已达 36.5d，被 **31d** 截断。
- 源块与新块**必须共存**（写完新块才删源块），所以磁盘占用会短暂超过 `retention.size`
  —— 这是官方要求把 `retention.size` 压到分配磁盘 **80~85%** 的原因。
- 保留策略：时间与容量**同时配置时先触发者生效**；过期块必须**整块过期**才删除，
  清理是后台行为、最多可能滞后 **2 小时**。

### 4. 容量公式

```text
needed_disk_space = retention_time_seconds × ingested_samples_per_second × bytes_per_sample
```

每样本平均只占 **1~2 字节**（同一序列内样本被压缩）。15d × 10 万样本/s：

| 每样本字节 | 需要的磁盘 |
| --- | --- |
| 1.0 B | 129.6 GB |
| 1.5 B | 194.4 GB |
| 2.0 B | 259.2 GB |

官方另给一条经验：**减少序列数比拉长抓取间隔更有效**。本 demo 把它量化——两条路线
各把样本总量减半，但「序列减半」还额外省下**一半的固定开销**（索引项 + chunk 头），
实测 1000 序列 × 256 B 开销的场景下差额恰为 `1000/2 × 256 = 128,000` 字节；若压缩率
随序列内样本数下降（1.5 → 1.6 B），「间隔翻倍」还要再多花 **4.32 MB**。

## 对比 / 选型

| 维度 | 本地 TSDB | 远程写（Remote Write） |
| --- | --- | --- |
| 扩展性 | 单节点，不集群不分片 | 交给远端（Mimir/Thanos/VictoriaMetrics） |
| 耐久性 | 依赖单机磁盘；NFS/EFS 等非 POSIX 文件系统**不受支持** | 取决于远端 |
| 查询位置 | 本地完成 | **远端读只取原始序列**，PromQL 仍在本地算（有扩展上限） |
| 备份 | 官方推荐 snapshot；直接拷目录须排除 `wal/`、`chunks_head/`、`wbl/` | 由远端负责 |
| 适用规模 | 单机可留数年数据 | 多租户、超大规模、长期存储 |

## 环境准备

- 操作系统：任意（纯标准库；无 NFS 依赖）
- Python 3.9+（本机 3.13.12 实测）
- Go 1.21+ / C99（本机无 go、gcc 工具链，两版走人工代码审查 + 结构校验）

## 运行方式

### Python（本机实跑，58 项断言全绿）

```bash
cd python && python demo.py
```

### Go

```bash
cd go && go run .
```

### C

```bash
cd c && gcc -O2 -Wall -Wextra -pedantic tsdb_demo.c -lm -o tsdb_demo && ./tsdb_demo
```

## 关键代码片段

```python
def max_compacted_span(retention_s):
    """压缩后单块跨度上限 = min(保留期 10%, 31 天)。"""
    return min(0.10 * retention_s, MAX_COMPACTED_SPAN)

def wal_segments_for(seconds, samples_per_s, wal_bytes_per_sample):
    """覆盖 seconds 秒所需 WAL 段数；官方规定至少保留 3 段。"""
    raw = seconds * samples_per_s * wal_bytes_per_sample
    return max(MIN_WAL_SEGMENTS, math.ceil(raw / WAL_SEGMENT_SIZE))
```

```python
class InvertedIndex:
    def intersect(self, matchers):
        """有序 posting list 的 merge-join：O(n+m)，不是扫描全部序列。"""
        lists = sorted((self.postings.get(m, []) for m in matchers), key=len)
        cur, comparisons = list(lists[0]), 0
        for other in lists[1:]:
            merged, i, j = [], 0, 0
            while i < len(cur) and j < len(other):
                comparisons += 1
                if cur[i] == other[j]:
                    merged.append(cur[i]); i += 1; j += 1
                elif cur[i] < other[j]:
                    i += 1
                else:
                    j += 1
            cur = merged
        return cur, comparisons
```

## 性能与边界

- **2h 块 + 512 MB chunk 段**：1 GB 级块会切成 2 段；WAL 段固定 128 MB，至少 3 段
  （384 MiB）是**硬下限**，与负载无关。
- **WAL 比块大得多**：官方明确说 WAL 里是未压缩的原始数据，「significantly larger」。
  本 demo 的段数估算把「每样本字节」作为入参（示例取 2 B，属**建模假设**，官方只给定性
  描述），2h × 10 万样本/s → 11 段。
- **压缩期间磁盘峰值可达 2×**：源块与新块共存。分配 1 TiB 时 `retention.size` 应取
  0.80~0.85 TiB，留出的 15~20% 正是给这个峰值。
- **退化边界**：保留期 4h 时 `10% = 0.4h < 基础块 2h`，按文档公式**不存在可压缩组合**。
  本 demo 直接按文档公式实现，未引入 Prometheus 内部的额外下限，故把这个退化情形
  显式断言出来。
- 非 POSIX 文件系统不支持（NFS 与其变体、AWS EFS 均被点名）。

## 注意事项与常见坑

1. **不做快照直接拷目录会丢数据**：必须排除 `wal/`、`chunks_head/`、`wbl/` 才能得到一致
   备份，代价是丢掉 WAL 覆盖的时间范围。官方表述是「自上一个块创建以来（块通常每 2 小时
   创建）」，并给出**最近 3 小时**这个数字——它**严格大于**朴素的「head 持有 2h」口径，
   差额来自落盘/块创建的滞后。两种口径在本 demo 里同时保留，不合并。
2. **块必须整块过期**：一个块起点早于 cutoff、但末样本还没过期时不会删。2h 块意味着
   「保留期 < 2h」实际留不住东西。
3. **过期清理最多滞后 2 小时**：块刚过期不等于立刻消失，监控磁盘不要按「理论值」算。
4. **时间与容量保留同时配置时先触发者生效**：只想着「15 天」而忘了设了 100 GB 上限，
   会在大盘上被提前裁掉历史。
5. **`retention.size` ≤ 80~85% 的分配盘空间**：剩下的缓冲是压缩峰值的，不是浪费。
6. **远程读并不分担查询**：远端只返回原始序列，PromQL 求值仍在本地 Prometheus 完成，
   所以远端读有扩展上限——官方明确说「完全分布式求值在当下被认为不可行」。
7. **回填（backfill）有两个陷阱**：不能回填最近 3 小时（会与仍在变更的 Head 重叠）；
   重复运行会重复造块；且 OpenMetrics 无法表示原生直方图与 staleness 标记。
8. **`--storage.tsdb.wal-compression`**：2.11.0 引入、2.20.0 起默认开启，官方称 WAL 体积
   **减半**；一旦启用，降级回 < 2.11.0 必须先删 WAL。

## 参考资料（实际阅读过的权威来源）

- [Storage | Prometheus（`docs/storage.md` 官方设计文档）](https://raw.githubusercontent.com/prometheus/prometheus/main/docs/storage.md)
  — 磁盘布局与目录树、2h 块、chunk 段 512 MB、WAL 段 128 MB 与「至少 3 段」、压缩跨度
  `min(10%, 31d)`、源块与新块共存、`retention.size` 建议 80~85%、容量公式与每样本 1~2 字节、
  默认保留 15d、时间/容量先触发者生效、过期块清理最多 2h 且须整块过期、快照备份与
  「最近 3 小时」表述、NFS/EFS 不支持、远程读写协议与「PromQL 仍在本地求值」、回填工具的
  限制（本 demo B～F 组的直接依据）。
- [Storage | Prometheus（官网同页）](https://prometheus.io/docs/prometheus/latest/storage/)
  — 与上条同源，用于交叉核对目录树与 `--storage.tsdb.*` 各 flag 的默认值。
- [WAL and Durability | prometheus/prometheus DeepWiki](https://deepwiki.com/prometheus/prometheus/4.3-wal-and-durability)
  — WAL/WBL 双日志分工、`tsdb/record` 八种记录类型与编解码方法名、`loadWAL` 重放流程、
  按 `HeadSeriesRef` 分片到 `walSubsetProcessor` 的并行重放、`WALReplayConcurrency`
  默认 `GOMAXPROCS`、`checkpoint.NNNNNN` 命名与重放起点（本 demo E 组的依据）。
- [package record | godocs（prometheus/tsdb/record v0.300.0）](https://godocs.io/github.com/prometheus/prometheus/tsdb/record@v0.300.0)
  — `Encoder` / `Decoder` 的 `Series` / `Samples` / `HistogramSamples` /
  `FloatHistogramSamples` / `Exemplars` / `Tombstones` / `Metadata` / `MmapMarkers`
  方法清单，用于核对记录类型集合。
