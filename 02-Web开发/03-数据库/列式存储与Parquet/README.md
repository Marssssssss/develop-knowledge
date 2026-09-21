# 列式存储与 Parquet（Dremel 记录切分与 RLE 混合编码）

## 一、简介

OLAP 与 OLTP 最根本的分歧在存储布局：OLTP 一行挨着一行存（行式），OLAP 一列挨着一列存（列式）。列式的好处是分析查询往往只碰少数几列，且同列数据相似度高、压缩率惊人；难点是**嵌套结构怎么切成列、还能原样拼回去**。

Parquet 的解法来自 Google 的 Dremel 论文：**record shredding and assembly**（记录切分与装配）。核心只有两个整数 ——

- **repetition level（r）**：这个值是**在路径上哪一级 repeated 字段**发生了重复；
- **definition level（d）**：路径上有多少个 optional/repeated 字段**实际被定义**。

本 demo 按 `apache/parquet-format` 官方规范与 Dremel 论文 Figure 3 复现了完整的切分/装配流程，并实现了 `Encodings.md` 里的 RLE/Bit-Packing 混合编码，52 条断言逐条钉死。

## 二、原理详解

### 2.1 schema 决定最大层级

```
message Document {
  required int64 DocId;
  optional group Links {
    repeated int64 Backward;
    repeated int64 Forward;
  }
  repeated group Name {
    repeated group Language {
      required string Code;
      optional string Country;
    }
    optional string Url;
  }
}
```

两条推导规则：

- **max definition level** = 路径上 `optional` 与 `repeated` 节点的个数（`required` 不计）；
- **max repetition level** = 路径上 `repeated` 节点的个数。

于是：`DocId` (0, 0)、`Links.Backward` (2, 1)、`Name.Language.Code` (2, 2)、`Name.Language.Country` (3, 2)、`Name.Url` (2, 1)。注意 `repeated` 字段为空时也算「未定义」，所以它同时贡献 d 与 r。

### 2.2 切分：Dremel Figure 3 复现

用论文 Figure 2 的两篇文档做输入，`Name.Language.Country` 这一列切出来是：

| 值 | r | d | 说明 |
| --- | --- | --- | --- |
| `"us"` | 0 | 3 | 文档开头，Name/Language/Country 全都定义了 |
| `NULL` | 2 | 2 | 第二个 Language（在 r=2 这一级重复），但它没有 Country |
| `NULL` | 1 | 1 | 第二个 Name 完全没有 Language，故 Country 未定义 |
| `"gb"` | 1 | 3 | 第三个 Name 的 Language 有 Country |
| `NULL` | 0 | 1 | 第二篇文档的 Name 没有 Language |

**required 字段也会在列里出现 NULL**：`Name.Language.Code` 的 `Code` 是 required，但祖先 `Language` 缺失时，这一列照样要写一条 NULL（d=1）。「required」只保证**它自己**不会缺失，不保证**祖先**不缺失。

### 2.3 r 的口径最容易写错

r 记的是**最深的那个「新开了一次出现」的 repeated 层级**，而不是「所有新出现的层级里最深的那个」也不是最浅的：

- 第三个 Name 的第一个 Language（`gb`）：新开的是 Name（第 1 级）→ r=1；
- 第一个 Name 的第二个 Language：新开的是 Language（第 2 级）→ r=2；
- 每篇文档的第一个值：r=0（**r=0 同时是文档边界**）。

装配时反过来：`r = k` 表示第 k 个 repeated 节点开了新出现，于是保留前 k−1 层、更深的层重建。

### 2.4 数据页布局

官方 README 的 Data Pages 一节：页内三段**背靠背、无填充**：

```
[ repetition levels ][ definition levels ][ values ]
```

- 列不嵌套（路径长度为 1）→ **不写 rep 段**；
- 列是 required → **不写 def 段**；
- 所以「非嵌套 + required」的列，页里**只有 values**。

NULL 只体现在 def level 上，**不占 values**。官方给的例子：1000 个 NULL 的非嵌套列，def 段就是一条 `(0, 1000)` 的 RLE run，values 段为空。

### 2.5 RLE / Bit-Packing 混合编码（RLE = 3）

levels 用这种编码，文法（官方 Encodings.md）：

```
rle-bit-packed-hybrid: <length> <encoded-data>
run               := <bit-packed-run> | <rle-run>
bit-packed-run    := varint((run_len/8) << 1 | 1) <bit-packed-values>
rle-run           := varint(run_len << 1) <repeated-value>
```

三个要点：

1. **header 的最低位是标志位**：1 = bit-packed run，0 = RLE run；
2. **bit-packed 总是按 8 个一组**，header 里存的是 `长度/8`（不足 8 个要补 0）；
3. **位打包是 LSB-first**：值从每个字节的最低位往高位塞，每个值内部的位序仍是 MSB→LSB。

官方给了可直接对拍的例子：bit width 3 打包 `0..7` 得到 `0x88 0xC6 0xFA`（本 demo D1 断言逐字节一致）。

`varint-encode()` 是标准 ULEB-128。

## 三、对比：行式 / 列式 / 半列式

| | 行式（B-tree 堆表） | 列式（Parquet） | 半列式（ORC / ClickHouse MergeTree） |
| --- | --- | --- | --- |
| 擅长的查询 | 点查、整行读写 | 少数列的大范围扫描 | 两者兼顾（分块内的迷你列存） |
| 压缩率 | 低 | 高（同列同质） | 中高 |
| 嵌套结构 | 天然支持 | 需要 def/rep levels | 同样需要（ORC 用 present stream） |
| 写入 | 行级追加、易更新 | **不可原地更新**，整文件重写 | 分块写入，块内不可变 |
| 典型场景 | OLTP | 数据湖、离线分析 | 实时 OLAP |

## 四、环境

- Python 3.8+（仅标准库）
- Go 1.21（无外部依赖）

## 五、运行方式

```bash
cd python && python selfcheck_parquet.py    # 52 条断言
cd go     && go run .
```

`python/main.py` 是 schema 与切分/装配，`python/encoding.py` 是 RLE 与数据页布局（按 300 行上限拆开）。

## 六、关键代码

**切分时 r 的取值**（`python/main.py`）：

```python
k = ridx[i]                     # 这是路径上第几个 repeated 节点
for j, item in enumerate(items):
    r = k if j > 0 else cur_rep  # 只有「非首次出现」才抬高 r
```

**装配时按 r 截断重建**（`python/main.py`）：

```python
if r == 0:
    docs.append({}); rep_stack = []
else:
    rep_stack = rep_stack[: r - 1]      # 前 r-1 层沿用，更深的层重建
```

**LSB-first 位打包**（`python/encoding.py`）：

```python
cur |= (v & mask) << bits
bits += width
while bits >= 8:
    out.append(cur & 0xFF); cur >>= 8; bits -= 8
```

## 七、性能边界

- **列数的爆炸**：深层嵌套的 schema 会让列数线性增长（每条叶子路径一列），宽表（数千列）下 footer 元数据本身就有 MB 级。
- **def/rep levels 的位宽很小**（通常 1~3 bit），所以 RLE 编码后几乎不占空间；真正占空间的是 values 段。
- **RLE 对「连续相同」依赖极强**：levels 通常高度重复（大量 d=max），所以压缩效果好；如果 levels 频繁跳变（嵌套稀疏），bit-packed run 会占主导，收益下降。
- **页是压缩与解码的最小单位**：页太大则随机读放大，太小则元数据占比高。常见页大小 8 KB~1 MB。
- **`dictionary encoding` 是默认值友好型**：低基数字符串列靠字典 + RLE 索引能压到极小，但字典过大时会**回退**到 PLAIN。
- **不可原地更新**意味着 Parquet 适合「一次写、多次读」；需要 update/delete 的场景要么整文件重写，要么上 Iceberg/Delta 这类表格式（用删除向量与合并读）。

## 八、注意事项与常见坑

1. **required 不等于列里没有 NULL**：祖先缺失时照样写 NULL（本 demo B6）。
2. **`r=0` 是文档边界**，不是「第 0 层重复」；装配时它表示开新文档。
3. **bit-packed run 必须是 8 的倍数**，尾部不足要补 0 —— 解码时靠 `count` 截断，别把补的 0 当数据。
4. **header 最低位才是标志位**，别把 `run_len << 1` 当长度直接除 2 之前忘了判奇偶。
5. **位打包是 LSB-first**，与直觉（MSB-first）相反；照 MSB 写会解出完全不同的值，而且只在部分数据上出错，极难发现。
6. **非嵌套列不写 rep 段**：路径长度为 1 时 rep 恒为 0，写了反而违反规范。
7. **NULL 不占 values**：数 values 的个数不能当行数，行数要看 def levels 里等于 max 的个数。
8. **Go 版要显式区分 nil 与「键不存在」**：`map[string]any` 里 `nil` 既是 JSON 的 null 也可能是缺键，本 demo 用 `Entry.IsNull` 显式表达。
9. **Python 版的 numeric 精度**：本 demo 只判 IEEE 双精度溢出，未做 PG numeric 的精确量级判定（口径已在代码注释里写明）。

## 九、参考资料（本轮实际读过）

- apache/parquet-format — README.md（Nested Encoding / Nulls / Data Pages 三节）
  <https://github.com/apache/parquet-format/blob/master/README.md>
- apache/parquet-format — Encodings.md（RLE / Bit-Packing Hybrid (RLE = 3) 的文法与位打包示例）
  <https://github.com/apache/parquet-format/blob/master/Encodings.md>
- apache/parquet-format — LogicalTypes.md（逻辑类型与物理类型的映射）
  <https://github.com/apache/parquet-format/blob/master/LogicalTypes.md>
- Dremel 论文的 Figure 2 / Figure 3（Document schema 与切分后的 (r, d) 序列），
  Parquet 官方 README 指向的实现说明：
  <https://github.com/julienledem/redelm/wiki/The-striping-and-assembly-algorithms-from-the-Dremel-paper>
