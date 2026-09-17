# KV 存储引擎 LSM(LevelDB / RocksDB)

把 LSM-Tree 存储引擎拆成可跑的最小模型:一条**写路径**(WAL → memtable → 不可变 memtable → L0),
一条**后台路径**(flush → 分层 compaction),再加上两个真实磁盘格式(WAL 32KB 分块、SSTable footer)。
Python 5 文件 + Go 6 文件,共 64 条断言,全部对应官方文档原文。

## 一、简介

LSM-Tree(Log-Structured Merge Tree)是当代 KV 存储的默认答案:LevelDB、RocksDB、Cassandra、
HBase、TiKV、ScyllaDB 都建立在同一套结构上。核心取舍只有一句话:**把随机写变成顺序写,
代价是读要合并多层、空间要等压缩回收**。官方对三大基本构件的描述
(rocksdb wiki/RocksDB-Overview.md):

> The three basic constructs of RocksDB are **memtable**, **sstfile** and **logfile**.
> ... new writes are inserted into the *memtable* and are optionally written to the
> *logfile* (aka. Write Ahead Log(WAL)). When the memtable fills up, it is flushed
> to a *sstfile* on storage and the corresponding logfile can be safely deleted.

LevelDB 另外给了一套具体参数:log 约 **4MB** 转 sorted table;young 文件超过 **4 个**就与 L1
重叠文件合并;level-L 超过 **10^L MB** 时向 L+1 下沉;每个新 L1 文件 **2MB** —— 全部做成可断言常量。

## 二、原理详解

### 2.1 写路径:一次 Put 走四步

| 步骤 | 动作 | 本 demo 对应 |
|---|---|---|
| 1 | 写 WAL(顺序追加) | `wal_batches` + `lsm_formats.LogWriter` |
| 2 | 写 memtable(内存有序表) | `MemTable` |
| 3 | memtable 满 → 冻结为**不可变 memtable**,新开 memtable + 新 WAL | `rotate_memtable()` |
| 4 | 后台把不可变 memtable 落成 L0 文件,旧 WAL 可删 | `flush_one()` |

第 3 步是关键设计:冻结与落盘**解耦**,写入不必等磁盘。代价是不可变 memtable 会堆积,
于是有了写停顿(见 2.5)。

### 2.2 WAL 的 32KB 分块格式

官方原文:"The log file contents are a sequence of 32KB blocks." 每条物理记录是
`checksum:uint32 / length:uint16 / type:uint8 / data[]`,**头部 7 字节**。类型四种:
`FULL=1 FIRST=2 MIDDLE=3 LAST=4`。

两条容易被忽略的规则:

1. **"A record never starts within the last six bytes of a block"** —— 不足 7 字节的尾部
   零填充为 trailer,读者必须跳过。
2. **剩余恰好 7 字节时**,写一条**零字节用户数据的 FIRST** 把 trailing 7 字节填满,
   数据全部推到后续块 —— 这条 aside 是"块边界 + 定长头部"逼出来的边界情况。

官方示例可以直接用模型复现:记录 A(1000)/B(97270)/C(8000) 依次写入后 —
A 是第 1 块 FULL;B 切成 FIRST(占满第 1 块剩余)/MIDDLE(独占第 2 块)/LAST(第 3 块前缀),
第 3 块正好空出 **6 字节**当 trailer;C 落到第 4 块 FULL。断言精确到"trailer == 6"。

### 2.3 SSTable 文件格式

官方布局:`[data block 1..N][meta block 1..K][metaindex block][index block][Footer]`

* 所有内部指针叫 **BlockHandle** = `{offset: varint64, size: varint64}`;
* `index` 块每个 data block 一条:key 是**该块最后一个 key 之后的字符串**,value 是
  BlockHandle —— 这是"层内二分查找"的前提;
* **Footer 定长**,`starts at file_size - sizeof(Footer)`,含 metaindex/index 两个
  BlockHandle、零填充到 40 字节,末尾是 `fixed64` magic:

```python
FOOTER_MAGIC = 0xDB4775248B80FB57     # little-endian
FOOTER_SIZE  = 2 * 20 + 8             # 40 == 2*BlockHandle::kMaxEncodedLength
```

* **filter 元块**按 base = **2KB** 分区间:文件偏移落在 `[i*2048, (i+1)*2048-1]` 的所有 data block,
  其 key 一起生成第 i 个 filter;块尾是 4 字节偏移数组 + "数组起点" + 1 字节 `lg(base)`。
  这就是布隆过滤器能"按块定位"而非"按文件扫描"的原因。

### 2.4 分层与 compaction:两个口径

| | LevelDB / RocksDB 静态层级 | RocksDB 动态层级(`level_compaction_dynamic_level_bytes`) |
|---|---|---|
| 目标来源 | L1 = `max_bytes_for_level_base`,逐层 ×`multiplier` | 末层 = **末层实际大小**,逐层 ÷`multiplier` |
| 层结构 | 每层都可能被填满 | 目标 < `base/multiplier` 的层**保持空** |
| 收益 | 简单可预测 | 保证约 **90%** 数据在末层、9% 在倒数第二层 |
| 默认 | 旧默认 | **8.4 起为推荐/默认** |

挑层用**得分**:非 0 层 `层大小 / 目标大小`;L0 是
`max(文件数 / level0_file_num_compaction_trigger, 层大小 / max_bytes_for_level_base)`,
但**文件数没到触发数就完全不触发**,不论得分多高(官方明说)。得分最高的层先压。

官方那个例子在本 demo 里可以逐字复现:base = 1GB、末层实际 276GB 时,
L1..L6 的目标是 `0 / 0 / 0.276GB / 2.76GB / 27.6GB / 276GB`。

> **口径差异(必须说明)**:该示例在 wiki 里写作 `num_levels=6` 而层级标号是 L1..L6;
> 本项目按代码口径把 `num_levels` 解释为"**含 L0 的总层数**",因此断言里取 `num_levels=7`,
> 使非 0 层正好是 L1..L6、数值与官方示例一致。数值本身来自官方,标号解释是本项目的选择。

### 2.5 写停顿:三类触发条件

RocksDB 的写停顿(wiki/Write-Stalls.md)是 LSM 最真实的运维面。三类原因,**顺序即优先级**:

| 原因 | stall(减速) | stop(完全停) |
|---|---|---|
| 不可变 memtable 堆积 | `max_write_buffer_number > 3` 且数量 ≥ `max-1` | 数量 ≥ `max_write_buffer_number` |
| L0 文件堆积 | ≥ `level0_slowdown_writes_trigger` | ≥ `level0_stop_writes_trigger` |
| 待压缩字节 | ≥ `soft_pending_compaction_bytes` | ≥ `hard_pending_compaction_bytes` |

两个反直觉点:① `max_write_buffer_number > 3` 时**提前一个**开始 stall(官方"软刹车");
② 触发条件按列族(column family)计,但**停顿作用于整个 DB** —— 一个列族卡住,全库一起卡。
阻塞的写线程可用 `WriteOptions.no_slowdown = true` 换"立刻返回 `Status::Incomplete()`"。

### 2.6 删除标记什么时候能丢

删除在 LSM 里也是"写一条标记",它要一直活着直到**更深的层不再有更旧的值**。官方原文:

> They also drop deletion markers if there are no higher numbered levels that contain
> a file whose range overlaps the current key.

所以 `can_drop_delete(key, output_level)` 必须逐层检查 `output_level+1 .. num_levels-1` 的
key range。**这条规则写错的后果是"已删除的数据复活"**,与 Cassandra 墓碑是同一类 bug。

## 三、对比

| | LSM-Tree(RocksDB) | B+Tree(InnoDB) |
|---|---|---|
| 写放大 | 高(数据被反复重写,常见 >10×) | 低(就地更新,页级) |
| 空间放大 | 高(旧版本等压缩回收) | 低(≈1.0,碎片可控) |
| 读放大 | 高(要合并多层 + 布隆过滤器兜底) | 低(一次树查找) |
| 顺序写 | 唯一写路径,天然顺序 | 依赖 buffer pool 与 doublewrite |
| 压缩/回收 | 后台 compaction,可调策略 | 前台 purge + 空间复用 |
| 最适合 | 写密集、随机 key、时序 | 读密集、事务、范围扫描 |

## 四、环境

- Python **3.10+**(用到 `X | None`,无第三方依赖)
- Go **1.18+**(无第三方依赖)
- 本机无 Go 工具链时用 `syntax_sanity.py` + `bracket_check.py` 做结构体检 + 人工复核签名

## 五、运行方式

```bash
# Python 自检(64 条断言)
cd python && python lsm_check.py

# Go 自检(同样 64 条断言)
cd go && go run *.go
```

## 六、关键代码

```python
# 写停顿:顺序即优先级,先看 memtable 再看 L0 再看待压缩字节
state, why = engine.write_stall_state()      # ("ok"|"stall"|"stop", 原因)
if state == "stop":
    raise WriteStopped(why)                  # 应用层能看到"为什么被卡"
```

```python
# 动态层级:末层锚定在"实际大小",向上逐层除以 10,低于 base/10 的层直接留空
targets = engine.dynamic_level_targets()
# {6: 276GB, 5: 27.6GB, 4: 2.76GB, 3: 0.276GB, 2: 0, 1: 0}
```

```go
// 删除标记只在更深层没有文件覆盖该 key 时才丢(LevelDB doc/impl.md 原文)
merged := CompactEntries(ents, func(k string) bool { return e.CanDropDelete(k, outLevel) })
```

## 七、性能边界

- **写放大**是本模型的核心代价:每次下沉都要把数据重写一遍。实测统计里
  `write_amplification() >= 1` 是硬下界,LCS 生产环境常见 10~30。
- **读放大随层数与 L0 文件数线性增长**:这就是 "Intra-L0 compaction" 存在的原因 ——
  牺牲 1 倍写放大,把多个 L0 小文件合成一个大文件,换取 L0 的读性能。
- **写停顿不是 bug 而是保护**:不设停顿的话,后果官方写得很直白 ——
  空间放大涨到爆盘、读放大涨到查询超时。
- **`max_write_buffer_number` 调大是诱饵**:停顿变少,但内存线性上升,
  崩溃恢复要重放的 WAL 也更多。

## 八、注意事项与常见坑

1. **"剩余恰好 7 字节"必须单独处理**。写成 `if left < 7: 换块` 是错的 —— 恰好 7 字节时
   要发一条**零字节 FIRST** 把块填满,否则读者会把这 7 字节当成 trailer 而丢数据。
2. **footer 的 `padding` 长度来自 `2*BlockHandle::kMaxEncodedLength`(=40)**,不是硬编码的 32;
   官方文档的 `char[40-p-q]` 已经把常量关系写明了。
3. **`level0_file_num_compaction_trigger` 是硬门槛**:文件数不到时,哪怕 L0 字节数
   远超 `max_bytes_for_level_base`,官方也**不触发**。把"得分 > 1"当成唯一条件是错的。
4. **动态层级与静态层级的 `num_levels` 语义要统一**,否则官方示例里的
   `0/0/0.276/2.76/27.6/276` 会对不上号(见 2.4 的口径差异说明)。
5. **拆分后必须补 import**:Python 用 mixin 搬方法、Go 同包多文件共享符号,两者都是
   零语义改动;但 Python 的 `compact_entries` 若排在 `Entry` 之前定义,`List[Entry]`
   注解会在 **def 执行时**抛 `NameError` —— 纯搬运最容易踩的坑。
6. **Go 无工具链时的三条人工检查**:① 同包顶层符号不得重名(同名**方法**在不同 receiver
   上合法,别误报);② 未用 import 是硬编译错误;③ 变参断言函数 `check(label, cond, detail ...string)`
   规避"实参个数写错"的隐形编译错误。**`syntax_sanity.py` 不查行数**,拆完单独 `wc -l`。

## 九、参考资料

- `google/leveldb`:`doc/impl.md`(4MB 阈值、L0 触发 4 个、10^L MB 层级、删除标记丢弃规则)、
  `doc/log_format.md`(32KB 块、7 字节头部、FIRST/MIDDLE/LAST、6 字节 trailer、恰好 7 字节 aside)、
  `doc/table_format.md`(BlockHandle、index 块 key 语义、footer 定长与 magic、filter 块 base=2KB)
- `facebook/rocksdb` wiki:`Leveled-Compaction.md`(L0 触发、得分公式、动态层级与 90% 末层结论、
  Intra-L0、TTL/periodic compaction)、`Write-Stalls.md`(三类停顿触发条件与日志原文)、
  `RocksDB-Overview.md`(memtable/sstfile/logfile 三构件、flush 行内 GC、压缩策略对比)
- 均为上述两个仓库的 raw 文档源(`doc/*.md`、wiki raw markdown)
