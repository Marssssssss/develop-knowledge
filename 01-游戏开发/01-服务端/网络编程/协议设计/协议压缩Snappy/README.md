# 协议压缩:Snappy 块格式(zstd 对比)

## 简介

- 网游协议消息(状态快照、聊天、背包同步)有大量重复字段结构,压缩能直接省带宽;但游戏要的是**确定性的低延迟**,所以业界选的是 Snappy/LZ4 这类「无熵编码阶段的 LZ77 变体」——固定字节编码、解压极快、最坏情况可预估,而不是xz那类高压缩比方案。
- 本 demo 按 google/snappy 官方 `format_description.txt` 实现**完整解码器 + 贪心编码器**(纯标准库,无第三方依赖),并用 zstd 官方 README 的基准数据做选型对比。
- 关键概念:
  - **preamble 变长整数**:流开头以小端 varint 存未压缩长度(低 7 位数据、高位续位标志),任意长度最多 5 字节。
  - **literal / copy 两种元素**:tag 低 2 位分类型 `00 literal`、`01 copy1`、`10 copy2`、`11 copy4`;literal 直存原文,copy 是回引(offset+length)。
  - **RLE 语义**:copy 长度允许大于 offset,逐字节回引即游程编码(`"xababab"` = literal `"xab"` + copy(offset=2,len=4))。
  - **无熵编码后端、无成帧层**:官方文档明言,这决定了它的速度与比值特性。

## 原理详解

### 完整元素编码(tag 低 2 位定类型)

| 类型 | tag 布局 | 长度范围 | offset 范围 | 约束 |
| --- | --- | --- | --- | --- |
| literal | 高 6 位 = len-1(len≤60);len>60 时高 6 位=60/61/62/63,后跟 1~4 字节小端(len-1) | 1~2^32-1 | — | 流不能以 copy 开头 |
| copy1 | bits[2..4]=(len-4),bits[5..7]=offset 高 3 位,后 1 字节=offset 低 8 位 | 4~11 | 0~2047 | offset=0 非法 |
| copy2 | 高 6 位=(len-1),后 2 字节小端 offset | 1~64 | 0~65535 | offset 不得超出已输出长度 |
| copy4 | 同 copy2,后 4 字节小端 offset | 1~64 | 0~2^32-1 | 同上 |

- 解码器必须校验:offset > 0、offset ≤ 当前已输出字节数、最终长度 == preamble 声明值。
- 官方压缩器按 **32 KB 块**工作且不做跨块匹配(所以现网 offset ≤ ~32768),但解码器**不应依赖**这一点(文档明言)。

### 贪心编码器(demo 实现)

- 哈希表记录每个 4 字节序列**最近**出现位置;游标处查表命中 → 求 RLE 语义的最长匹配(封顶 64,copy 长度上限)→ 回引;
- copy 拆分:4≤len≤11 且 offset<2048 用 copy1(省 1 字节);offset≤65535 用 copy2、否则 copy4;len>64 拆多段(尾段 1~3 也合法,copy2/copy4 支持 len=1);
- literal 段按 60/61/62/63 四档长度前缀输出。

### zstd 是怎么往上加档的(官方 README + RFC 8878)

- zstd = **match finder + 快熵编码后端(FSE/Huffman)**,格式稳定、成文于 RFC 8878;
- 官方 Silesia 基准(Core i7-9700K,gcc 14.2.0,lzbench):**zstd -1 比值 2.896(压 510 MB/s / 解 1550 MB/s),snappy 1.2.1 比值 2.089(压 520 MB/s / 解 1500 MB/s),lz4 比值 2.101(675/3850 MB/s)** —— 压缩速度同级,比值高 ~39%;
- **小数据是另一回事**:官方明言「数据越小越难压,因为算法要靠历史数据学出模式」;zstd 的解法是**字典训练模式**(`zstd --train` 生成 dictionary,压解双方都要加载,对 ~1KB 的游戏消息提升显著)。Snappy 没有字典机制,但也没有熵头开销——单条几百字节的消息里,熵表头本身可能吃掉收益。

## 对比 / 选型(游戏协议场景)

| 维度 | Snappy | lz4 | zstd -1 | gzip |
| --- | --- | --- | --- | --- |
| Silesia 比值 | 2.089 | 2.101 | 2.896 | ~2.74(zlib -1) |
| 解压速度 | 1500 MB/s | 3850 MB/s | 1550 MB/s | 390 MB/s |
| 熵编码后端 | 无 | 无 | FSE+Huffman | Huffman |
| 小消息(几百字节) | 无表头开销 | 无表头开销 | 需字典才有收益 | 表头重 |
| 生态 | LevelDB/RocksDB/MySQL/Kafka;gRPC 官方 `grpc-encoding` 列表含 snappy | 内核 squashfs 等 | RFC 8878,多实现 | 最通用 |

结论:帧同步快照这类**连续大流量**优先 zstd(或字典化 zstd);**高频小消息**优先 snappy/lz4;gRPC 内部调用可直接 `grpc-encoding: snappy`(规范枚举值之一)。

## 环境准备

- OS 任意;Python 3.8+(纯标准库);Go 1.18+(纯标准库)。

## 运行方式

```bash
python3 python/main.py   # 断言全绿:含规范原文示例逐字节比对
go run go/main.go
```

## 关键代码片段(Python)

```python
def decompress(data):
    n, pos = decode_varint(data, 0)      # preamble:小端 varint 未压缩长度
    out = bytearray()
    while pos < len(data):
        tag, t = data[pos], data[pos] & 3
        pos += 1
        if t == 0:                        # literal:高 6 位或 60..63 扩展长度
            h = tag >> 2
            if h >= 60:
                extra = h - 59            # 60/61/62/63 -> 1~4 字节小端 (len-1)
                length = 1 + int.from_bytes(data[pos:pos + extra], "little")
                pos += extra
            else:
                length = h + 1
            out += data[pos:pos + length]; pos += length
        else:                             # copy:RLE 语义允许 len > offset
            ...                           # copy1/2/4 分别解 offset
            if offset == 0 or offset > len(out):
                raise SnappyError("非法回引 offset")
            for _ in range(length):
                out.append(out[-offset])  # 逐字节回引 = 游程编码
    if len(out) != n:
        raise SnappyError("声明长度不符")
    return bytes(out)
```

## 性能与边界

- demo 编码器为教学级(每位置查一次 4 字节哈希、贪心最长匹配),非官方优化(Snappy 官方压缩器有启发式跳步);解压路径与规范语义一致。
- 最坏膨胀:literal 全不可压时每 60 字节多 1 字节 tag + preamble,上界约 +1/60;copy1 下 4 字节回引压成 2 字节。

## 注意事项与常见坑

- **tag 高 6 位是 len-1 不是 len**(literal 与 copy2/copy4 都是);copy1 例外是 len-4——三种 copy 的长度基线不同,写解码器时最容易混。
- **copy1 的 offset 是 11 位,高 3 位挤在 tag 里**(bits[5..7]),低 8 位在后续字节——位域横跨 tag,拼装顺序别写反。
- **offset=0 可编码但非法**;`offset > 已输出长度` 也非法(回引越过流头),解码器必须当错误处理,否则可以构造越界读。
- **不要假设 offset ≤ 32768**:官方压缩器当前按 32KB 块工作,文档明言「解码器不应依赖,未来可能变」;copy4 编码就是为长回引预留的。
- **压缩上下文不跨消息**(gRPC 规范也要求每条消息独立压缩)——把多条消息攒在一个压缩上下文里,丢一条就全废。

## 参考资料(实际阅读过的权威来源)

- [Snappy compressed format description](https://github.com/google/snappy/blob/master/format_description.txt) — 官方格式规范(Zeev Tarantov 撰),本 demo 全部位域/约束出处。
- [zstd README(基准表与字典训练)](https://github.com/facebook/zstd) — zstd 官方仓库:Silesia 对比数据、FSE/Huffman 熵后端、RFC 8878、小数据字典模式。
- [gRPC over HTTP/2:PROTOCOL-HTTP2.md](https://github.com/grpc/grpc/blob/master/doc/PROTOCOL-HTTP2.md) — `grpc-encoding: snappy` 官方枚举、压缩上下文不跨消息的规范要求。
