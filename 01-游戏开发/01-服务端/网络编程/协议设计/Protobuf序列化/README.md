# Protobuf 序列化 — Wire Format 深入

## 简介

**Protocol Buffers**(Protobuf)是 Google 自 2001 年起的语言中立、平台中立二进制序列化格式;
驱动 gRPC、Kafka、Flink、etcd、Bazel 等大量分布式基础设施。Wire Format 是它最底层、最神奇的部分:

- **体积小**:字段名 → `field_number` 整数;序列化后只占必要字节
- **向前兼容**:未知字段通过 `wire_type` 跳过,新加字段不破坏老解析器
- **自描述** enough:无 `.proto` 时也能读出字段号 + 类型

- **关键概念清单**
  - **Tag**:`(field_number << 3) | wire_type`,单个 varint
  - **Varint**:1-10 字节变长整数,小值占 1 字节,MSB 是 continuation bit
  - **Six wire types**:VARINT(0)、I64(1)、LEN(2)、SGROUP(3)、EGROUP(4)、I32(5)
  - **ZigZag**:把 signed int 映射到正数,负数 varint 占 10 字节 → 1 字节
  - **Packed repeated**:连续重复值拼成单个 LEN 块
  - **Embed message**:用 LEN,递归

## 原理详解

### Wire types 表

| ID | 名称 | 适用类型 | 长度 |
| --- | --- | --- | --- |
| **0** | VARINT | int32/64、uint32/64、bool、enum | 1-10 B |
| **1** | I64 | fixed64、sfixed64、double | 8 B 小端 |
| **2** | LEN | string、bytes、embed message、packed repeated | 1-10 B length + N B |
| **3** | SGROUP | deprecated | 标记起点 |
| **4** | EGROUP | deprecated | 标记终点 |
| **5** | I32 | fixed32、sfixed32、float | 4 B 小端 |

### Tag 公式

```
Tag = (field_number << 3) | wire_type
     ^^^^^^^^^^^^^^^^^^^^^   ^^^^^^^^
      5-bit encoded            3 bits
```

例:`int32 a = 1` 字段值 150 → wire `0x08 0x96 0x01`
- 0x08 = tag varint,二进制 `0000 1000`,低 3 位 = 0(VARINT),字段号 = 1
- 0x96 0x01 = 150 varint(10010110 00000001 → 10010110 LSB-first → 0000001 0010110 = 128 + 16 + 4 + 2 = 150)

### Varint 编码

每个字节:
- MSB(高 7 位):`1` → 后续字节,`0` → 这是最后一字节
- 低 7 位:数据,小端(LSB first)

```python
def encode_varint(v: int) -> bytes:
    out = bytearray()
    while v > 0x7f:
        out.append((v & 0x7f) | 0x80)
        v >>= 7
    out.append(v & 0x7f)
    return bytes(out)
```

### ZigZag 编码

`sint32/sint64` 把负数映射到 varint 也占小字节:
```
0 → 0, -1 → 1, 1 → 2, -2 → 3, 2 → 4, -3 → 5
```

公式: `(n << 1) ^ (n >> 31)`(int32)/ `(n << 1) ^ (n >> 63)`(int64)
Python: `(n << 1) ^ (n >> 31)`

例:`-1` 的 ZigZag = `(−1 << 1) ^ (−1 >> 31) = (−2) ^ (−1) = 1` → varint `[0x01]`(1 字节)

### Embedded message

`LEN(2)` 既用于 `bytes/string`,也用于 embed message。区分方法:**尝试递归解析,如果解析通且刚好消费所有字节,就当 message**(`Message`)。

### Packed repeated(Ed 2023+ 默认)

`repeated int32 xs;` 字段如果有 3 个元素 [1,2,3]:
- unpacked:3 个 varint:[0x20 0x01 0x20 0x02 0x20 0x03] (tag + value ×3)
- packed:1 个 LEN,内容是 [0x01 0x02 0x03] = `[0x22 0x03 0x01 0x02 0x03]`(tag + len + bytes)

## 对比 / 选型

| 格式 | 体积 | 速度 | 前向兼容 | Schema | 跨语言 |
| --- | --- | --- | --- | --- | --- |
| **Protobuf** | 小 | 快 | **是** | .proto | 几乎所有主流语言 |
| JSON | 大 | 中 | 是 | 文本 schema | 几乎所有 |
| Thrift | 接近 Protobuf | 快 | 是 | .thrift | 主要语言 |
| Avro | 与 PB 接近 | 快 | 是(回退) | JSON | 大数据领域 |
| Cap'n Proto | **0 拷贝** | 极快 | 是 | schema | C++/Go |
| FlatBuffers | 0 拷贝读 | 极快 | 是 | .fbs | 大多语言 |
| MessagePack | 小 | 快 | 弱 | 二进制 type byte | 大多 |

游戏服务器:**Protobuf** 仍是主流;FlatBuffers 在移动端因 0-copy 读格外受欢迎。

## 环境准备

- **Python**:3.8+ 纯 stdlib
- **Go**:1.18+(本 demo 无第三方依赖)

## 运行方式

### Python

```bash
python3 python/protobuf_wire.py
# 输出 self-test PASS
```

### Go

```bash
cd go && go run protobuf_wire.go
# 输出 ALL self-tests PASS
```

## 关键代码片段

### Python — 完整 self-tests

Protonbuf 文档示例 `int32 a = 1; a = 150` 应序列化 `08 96 01`:

```python
m = Message()
m.add(1, 150)
assert m.encode() == b"\x08\x96\x01"
m2, _ = decode_message(b"\x08\x96\x01")
assert m2.fields[0].value == 150
```

字符串 `b = "testing"` 应序列化 `12 07 74 65 73 74 69 6e 67`:

```python
m.add(2, "testing")
want = b"\x12\x07testing"
assert m.encode() == want
```

嵌套:`Test3.Test1.a = 150` 编码 `1a 03 08 96 01`(tag 3, LEN; len=3; nested message)。

### Go — Varint & ZigZag

```go
func writeVarint(w *bytes.Buffer, v uint64) {
    for v >= 0x80 {
        w.WriteByte(byte(v) | 0x80)  // MSB=1,继续
        v >>= 7
    }
    w.WriteByte(byte(v))
}
func zigzag32(n int32) uint32 { return uint32(n<<1) ^ uint32(n>>31) }
func unzigzag32(v uint32) int32 { return int32(v>>1) ^ -int32(v&1) }
```

## 性能与边界

- **varint 优势**:1 占 1B;300 占 2B;`2^21`(2,097,152) 占 3B
- **valuint 劣势**:负 `int32` 占 10B,务必用 `sint32` 配 ZigZag
- **FIXED32/64**:固定 4/8 字节;超过 ~28 亿/约 5.7e15 时 varint 反而更长,此时固定类型更优
- **packed** 阈值:repeated 元素多才赢,1 个元素还能亏;Protonbuf 实现自动选
- **消息体积上限** = 2 GiB(Protobuf 实现拒绝更大)
- **128-位字段号最大有效**:tag 中 field_number 5 bit + varint 后续 = 2^29 - 1,即 536,870,911

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 负 int 序列化 10B | int32 用补码 | 用 `sint32` 走 ZigZag |
| 新加字段不兼容 | 字段号冲突 | 把已删字段号加入 `reserved` |
| packed 反序列化失败 | 老版本当 unpacked | 解析器必须两种都接受 |
| Group 语法还能解码 | 历史包袱 | 不用 group,SGROUP/EGROUP 当 deprecated |
| 大端序要求与 JSON 不同 | protobuf 用小端存 fixed64 | 实现用 `binary.LittleEndian` |
| field number 不能 0 | 0 是非法(暗示缺字段) | proto3 后端生成器主动跳过 |
| 默认值会丢 | 0/false/"" 与缺字段等价 | 用 `optional` 或 `oneof` 区分 |

## 参考资料

- [Encoding - protobuf.dev](https://protobuf.dev/programming-guides/encoding) — **官方权威**,含 varint/tag 公式 + wire types 表 + packed 语法
- [编码 | Protocol Buffers 文档](https://protobuf.com.cn/programming-guides/encoding/) — 中文同等权威镜像
- [つくって学ぶ Protocol Buffers エンコーディング - Mercari Engineering](http://engineering.mercari.com/blog/entry/20210921-ca19c9f371) — Go 自实现手把手 + 完整 varint/FIXED32/LEN 编码
- [Google.Protobuf.WireFormat - C# API](https://protobuf.dev/reference/csharp/api-docs/class/google/protobuf/wire-format) — 官方另一种语言实现参考
- [Protobuf Debugging: Reading Wire-Format Errors - DevFlow Guides](https://wtool.dev/guides/protobuf-debugging-wire-format-errors) — 实战生产 schema drift 处理 + ZigZag 推导
