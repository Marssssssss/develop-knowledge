# TLV 编解码器(Type-Length-Value,简化版 ASN.1 BER)

## 简介

- **TLV**(Type-Length-Value)是一种"自描述、自定界"的数据编码模式:每个数据项由 **类型** + **长度** + **值** 三部分组成,接收方可从未完成流中增量解码。
- 本 demo 实现简化版 ASN.1 BER(基本编码规则, ITU-T X.690):type 是 2 字节大端 uint16,length 支持短/长两种格式,value 是任意字节。
- 关键概念:**前向兼容**(未知 tag 跳过不报错)、**流式解码**(无需先知道总长度)、**自定界**(length 决定 value 边界,无需额外终止符)。
- 历史背景:TLV 概念最早源于 1984 年 ISO/ITU-T 的 ASN.1 标准的 BER(Basic Encoding Rules);后被 IEEE 802.1Q VLAN tag、IEEE 802.11 帧头、Protobuf wire format、JSON 替代格式(BSON、MessagePack)等多种协议借鉴。

## 原理详解

### 编码格式(本 demo 简化)

```
+--------+--------+----------------+----------------+
|  Type  | Length |      Value     |  (next TLV...) |
| 2B BE  | 1B/2B+ |  <Length> B    |                |
+--------+--------+----------------+----------------+
```

### Type 字段(本 demo 简化)

- 2 字节大端无符号整数(0..65535)
- 完整 ASN.1 BER 的 type 是 1-N 字节,首字节 bits 8-7 是 class(Universal/Application/Context/Private),bit 6 是 P/C(primitive/constructed),bits 5-1 是 tag number;多字节时 bit 8 = 1 表示后续,最后字节 bit 8 = 0
- 本 demo 跳过这些复杂度,直接 uint16

### Length 字段(完整 BER,本 demo 支持)

- **短格式**:`0x00..0x7F` = 0..127(单字节)
- **长格式**:首字节 `0x80 | num_bytes`,后面 `num_bytes` 个大端字节表示长度
  - 例:长度 300 → `0x82 0x01 0x2C`(首字节 0x82 = 0x80 | 2,后续 2 字节 BE = 300)
- **不定长**:首字节 `0x80`,由后续 `0x00 0x00` 标记结束;本 demo 不实现

### Value 字段

- `length` 个字节原始数据
- 若是 "constructed"(ASN.1 概念,本 demo 跳过),value 内嵌其它 TLV;本 demo 所有 TLV 都是 primitive

### 完整 ASN.1 BER 与本 demo 对照

| ASN.1 特性 | BER 行为 | 本 demo |
| --- | --- | --- |
| Class + tag 编号 | 多字节 tag,class bits | 简化 2 字节 uint16 |
| 长/短 length | 支持 | ✓ 支持 |
| 不定长 length | 支持(0x80 + 末块) | ✗ 简化 |
| Constructed | 内嵌 TLV 列表 | ✗ 简化 |
| 真实编码函数 | `encode_tlv(tag, value)` | ✓ |
| 真实解码函数 | `decode_tlv(buf, pos)` | ✓ |

### 关键 API(本 demo)

```python
# Python
tlv = TLV(tag=1, value=b"alice")  # dataclass
blob = encode_tlv(tag=1, value=b"alice")   # b'\x00\x01\x05alice'
tlvs = decode_all(blob)                    # [TLV{tag=1, value=b"alice"}, ...]
```

```go
// Go
blob := EncodeTLV(1, []byte("alice"))     // [0 1 5 'a' 'l' 'i' 'c' 'e']
tlvs, err := DecodeAll(blob)                // []TLV{Tag:1, Value:[]byte("alice")}
```

### 底层发生了什么

- `encode_length(n)`:若 `n < 128`,返回 `[n]`;否则按 8-bit 分组从低到高写入 `[0x80|num_bytes, b1, b2, ...]`。
- `decode_length(buf, pos)`:读首字节,若 `< 0x80` 直接返回;否则按长格式拼接后续字节。
- `decode_tlv(buf, pos)`:读 2 字节 tag,读 length,slice 出 value,返回新 pos。

## 对比 / 选型

| 编码 | 紧凑 | 自描述 | 流式解码 | 前向兼容 | 用途 |
| --- | --- | --- | --- | --- | --- |
| **TLV/BER**(本 demo) | ✗(有冗余) | ✓ | ✓ | ✓ | ASN.1/SNMP/X.509 |
| Protobuf | ✓✓ | 部分(需 .proto) | ✗ | 弱 | gRPC |
| JSON | ✗(UTF-8 文本) | ✓ | ✗ | ✓ | REST API |
| MessagePack | ✓ | 弱 | ✗ | 弱 | Redis 内部 |
| CBOR | ✓✓ | ✓ | ✓ | ✓ | IoT/CoAP |
| FlatBuffers | ✓✓✓ | ✗(需 schema) | ✓ | 弱 | 游戏/移动 |
| SBE | ✓✓✓ | ✓ | ✓ | ✓ | 金融低延迟 |

> 选型建议:协议字段经常变动、需要前向兼容 → TLV/ASN.1/CBOR;极致性能 + 稳定 schema → Protobuf/SBE。

## 环境准备

- Python ≥ 3.8
- Go ≥ 1.18

## 运行方式

### Python

```bash
cd python
python3 main.py test        # 跑 6 个自测 + Person 序列化 demo
```

### Go

```bash
cd go
go run . test               # 跑自测 + Person demo
go run . demo               # 只跑 Person demo
```

## 关键代码片段

Python 编码核心(`python/main.py`):

```python
def encode_tlv(tag: int, value: bytes) -> bytes:
    if tag < 0 or tag > 0xFFFF:
        raise ValueError("tag must fit in uint16")
    return struct.pack(">H", tag) + encode_length(len(value)) + value

def encode_length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])                  # 短格式
    bs = []
    while n > 0:                            # 长格式:BE 字节数 + 0x80|len
        bs.insert(0, n & 0xFF)
        n >>= 8
    return bytes([0x80 | len(bs)]) + bytes(bs)
```

Go 解码核心(`go/main.go`):

```go
func DecodeTLV(buf []byte, pos int) (TLV, int, error) {
    tag := binary.BigEndian.Uint16(buf[pos:pos+2])
    pos += 2
    length, pos, err := decodeLength(buf, pos)
    if pos+length > len(buf) {
        return TLV{}, 0, fmt.Errorf("%w: value length %d out of range", ErrTLV, length)
    }
    return TLV{Tag: tag, Value: append([]byte(nil), buf[pos:pos+length]...)}, pos + length, nil
}
```

## 性能与边界

- **短 length 上限**:`< 0x80` 即 127 字节;超过即切长格式
- **长 length 字节数**:1..127(`0x81 0xXX` 至 `0xFF 0xXX...`),即最大 `2^(8*127) - 1` 字节,理论足够
- **总编解码开销**:O(n),每个字节至少看一次
- **Person 业务示例**:`name = "张三"` → 6 字节 UTF-8;`age = 30` → 2 字节 BE;`email = "alice@example.com"` → 17 字节;3 条 TLV 总 `2 + 1 + 6 + 2 + 1 + 2 + 2 + 1 + 17 = 34` 字节,实际略多(总长度字段占位)

## 注意事项与常见坑

- ❌ **字节序错误**:TLV 默认 big-endian(network byte order);ARM/PPC big-endian 旧硬件或 Go `binary.LittleEndian` 会写反。
- ❌ **混淆 BER / CER / DER**:三者是 ASN.1 的不同编码规则,BER 灵活、CER 规范 indefinite、DER 严格 definite;选错会导致严格验证器(如 OpenSSL)拒绝。
- ❌ **构造类型(Constructed)**:完整 BER 中 SEQUENCE/SET 等类型 value 内嵌 TLV 列表;本 demo 不实现,生产中常需。
- ❌ **不定长 + 末块标记**:如果选择实现 indefinite length,必须能正确识别 `0x00 0x00` 作为 constructed 终止符,否则会无限循环。
- ❌ **不验证 type 语义**:编码器不检查 tag 是否合法(如 SNMP 的 PDU tag 范围),应用层必须自行验证。
- ❌ **大 value 内存**:TLV 全在内存,处理 GB 级数据需切片流式处理;本 demo 不演示。

## 参考资料(实际阅读过的权威来源)

- [ITU-T X.690 — ASN.1 encoding rules: BER, CER, DER](https://en.m.wiki2.org/wiki/Basic_Encoding_Rules) — TLV 原始规范,本 demo 直接对应 §7 编码结构
- [ASN.1 — Wikipedia](https://en.wikipedia.org/wiki/ASN.1) — ASN.1 标准综述,各类(class)与编号体系
- [A Layman's Guide to a Subset of ASN.1, BER, and DER](http://luca.ntop.org/Teaching/Appunti/asn1.html) — Burton S. Kaliski 1993 经典入门
- [RFC 1157 — SNMP(Simple Network Management Protocol)](https://datatracker.ietf.org/doc/html/rfc1157) — 工业级 TLV 应用:BER 编码的 SNMP 消息
- [ITU-T X.680 — ASN.1 syntax notation](https://www.itu.int/rec/T-REC-X.680) — ASN.1 完整 BNF 与类(class)定义
- [golang.org/x/text/internal/format — 字节序工具](https://pkg.go.dev/encoding/binary) — Go 标准库字节序读写
- [Protobuf Encoding 规范](https://protobuf.dev/programming-guides/encoding/) — 与 TLV 的对比,字段 tag + wire type + value 是另一种"TLV 变体"
- [Codello.dev asn1/tlv — Go TLV 流式编解码](https://pkg.go.dev/codello.dev/asn1/tlv) — 工业级 BER 实现参考