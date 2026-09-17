# Protobuf 编码

把 Protocol Buffers 的线格式拆成可跑的最小实现:varint / ZigZag / tag / TLV 记录 /
packed 重复字段 / 未知字段跳过 / 合并语义。Python 3 文件 + Go 7 文件,共 77 条断言,
全部对应 protobuf.dev 官方文档原文。

## 一、简介

Protobuf 的线格式只有一句话:**消息是一串 TLV(key-value 对)**。官方原文:

> When a message is encoded, each key-value pair is turned into a record consisting
> of the field number, a wire type and a payload. The wire type tells the parser how
> big the payload after it is. This allows old parsers to skip over new fields they
> don't understand.

**Tag-Length-Value** 这个结构同时买到三件事:① 字段名不进线格式(线上只有字段号,
名字靠两端各自的 `.proto` 映射,这是同等数据体积远小于 JSON 的主因);
② 未知字段可跳过 —— wire type 自描述 payload 长度,旧解析器遇到新字段能安全越过去,
这是"新旧版本混跑不炸"的机制;③ 可流式解析,不必把整条消息读进内存再定位。

## 二、原理详解

### 2.1 varint:一切的基础

> Each byte in the varint has a continuation bit ... This is the most significant bit
> (MSB) of the byte. The lower 7 bits are a payload.
> These 7-bit payloads are in **little-endian order**.

150 的编码(官方逐步演示的同一例):二进制 `10010110 00000001` → 去掉 MSB 得
`0010110` `0000001` → 转大端拼接 `0000001 0010110` → 解释为无符号数
`128 + 16 + 4 + 2 = 150`。所以 150 → `9601`,而 1 → `01`。

长度阶梯:127 → 1 字节,128 → 2 字节,`2^63-1` → 9 字节,`2^64-1` → **10 字节(官方上限)**。
第 10 个字节的最高位必须是 0,否则数据损坏 —— 这就是"十个字节"这个上限的直接来源。

### 2.2 tag:字段号与 wire type 打包

> The "tag" of a record is encoded as a varint formed from the field number and the
> wire type via the formula `(field_number << 3) | wire_type`.

低 3 位是 wire type,其余是字段号。官方示例 `08` → 去掉 MSB 得 `00001000`,
末 3 位是 wire type **0(VARINT)**,右移 3 位得字段号 **1**。

| ID | Name | 用于 |
|---|---|---|
| 0 | VARINT | int32/int64/uint32/uint64/sint32/sint64/bool/enum |
| 1 | I64 | fixed64/sfixed64/double |
| 2 | LEN | string/bytes/嵌入消息/**packed 重复字段** |
| 3 / 4 | SGROUP / EGROUP | group 开始/结束(已废弃) |
| 5 | I32 | fixed32/sfixed32/float |

字段号必须在 `1 .. 536,870,911`(官方上限 **2²⁹-1**),且 **19,000..19,999 被官方保留**
(写了编译器会报错,本 demo 在运行时也拦)。

### 2.3 官方最小示例,逐字节对齐

```proto
message Test1 { int32 a = 1; }     // a = 150  →  序列化结果 08 96 01
```

`08` 是 tag(字段 1 + VARINT),`96 01` 是 payload(varint 150)。
断言到这里就是 `encode_message(test1, {"a": 150}).hex() == "089601"`。

### 2.4 负数:int 的十个字节 vs sint 的两个字节

最容易踩的性能坑。官方原文:

> The `intN` types encode negative numbers as two's complement ... As a result, this
> means that **all ten bytes** must be used.

`int32 = -2` → `fe ff ff ff ff ff ff ff ff 01`(10 字节)。而 `sint32` 用 ZigZag:
正整数 `p` → `2p`,负整数 `n` → `2|n| - 1`,即 `(n << 1) ^ (n >> 31)`。
官方对照表:`0→0`、`-1→1`、`1→2`、`-2→3`、`0x7fffffff→0xfffffffe`、
`-0x80000000→0xffffffff`,以及 `-500 → 999`。

所以**只有确定会出现负数的整数字段才该用 `sint`** —— 其余场景它会让正数多花一位。

### 2.5 LEN 与 packed

LEN 记录的长度前缀是**紧跟 tag 的 varint**,string/bytes/嵌入消息都走这条路。
重复标量字段可以 packed:把 N 个 payload 首尾相连塞进**一条** LEN 记录。
官方的兼容要求是双向的:

> parsers must be able to parse repeated fields that were compiled as packed as if
> they were not packed, and vice versa.

本 demo 用「`5:{1 2}` + `4:{"hello"}` + `5:{3}` 解析出 `nums=[1,2,3]`」这条官方示例,
同时验证了重复字段跨记录拼接与 packed/非 packed 互换。

### 2.6 顺序不确定,合并有规则

> there is no guaranteed order for how its known or unknown fields will be written.
> ... **the default serialization is not deterministic.**

官方直接点名这几行可能失败:`foo.SerializeAsString() == foo.SerializeAsString()`、
以及对序列化结果求 Hash / CRC / 指纹。推论很硬:**不能把序列化字节当 key、当签名、当缓存键**。

解析端规则明确(Last One Wins):标量取**最后一个**;嵌入消息**递归合并**
(后者覆盖标量、repeated 拼接);repeated 字段拼接。

### 2.7 group:已废弃但必须能跳过

SGROUP/EGROUP 记录 **payload 为空**,只起标记作用。官方要求字段号配对:

> If we encounter `7:EGROUP` where we expect `8:EGROUP`, the message is mal-formed.

## 三、对比

| | Protobuf | JSON | 手写二进制 |
|---|---|---|---|
| 字段名 | 不传输(靠 schema) | 每次都传 | 不传输 |
| 长度自描述 | wire type 驱动 | 引号/括号天然 | 全靠约定 |
| 未知字段 | 可跳过 | 可忽略 | 无法跳过 |
| 自描述性 | 需要 .proto | 自描述 | 无 |
| 顺序稳定 | **不保证** | 保序 | 由实现决定 |
| 空间 | 小(varint + 无字段名) | 大 | 最小 |

## 四、环境

- Python **3.10+**(无第三方依赖,只用 stdlib `struct`)
- Go **1.18+**(无第三方依赖)
- 无 Go 工具链时用 `syntax_sanity.py` + `bracket_check.py` 体检 + 人工复核签名

## 五、运行方式

```bash
# Python 自检(77 条断言)
cd python && python pb_check.py

# Go 自检(同样 77 条断言)
cd go && go run *.go
```

## 六、关键代码

```python
# tag = (field_number << 3) | wire_type,再整体按 varint 编码
encode_tag(1, WIRE_VARINT)     # b"\x08"
encode_tag(2, WIRE_LEN)        # b"\x12"

# 官方最小示例:message Test1 { int32 a = 1; } 且 a = 150
encode_message(test1, {"a": 150}).hex()      # "089601"
```

```python
# 负数两条路:int 补码十字节 / sint ZigZag 两字节
encode_varint(-2).hex()               # "feffffffffffffffff01"  (10 字节)
encode_varint(zigzag_encode(-500))    # 999 → 2 字节
```

```go
// 未知字段靠 wire type 跳过 —— 旧解析器读得懂新消息的全部机制
if fd == nil {
    pos, err = SkipField(data, pos, wire)
    continue
}
```

## 七、性能边界

- **varint 不是定长**:`int32` 存 -1 要 10 字节,而 `uint32` 存 2³²-1 只要 5 字节 ——
  字段类型选错会让体积反向爆炸。
- **packed 只对可自定界标量成立**(VARINT/I32/I64),string/bytes/消息无法 packed。
- **无法随机访问单字段**:解析是线性的且字段顺序不保证,要做二分/索引必须先整体解析。
- **10 字节上限**让解码有确定的常数上界,不会被恶意构造的超长 varint 拖死
  (本 demo 在 11 字节处直接判损坏)。

## 八、注意事项与常见坑

1. **`uint64(-2)` 在 Go 里作为常量表达式是编译错误**("constant -2 overflows uint64"),
   必须先用 `int64` 变量中转 —— 无工具链时最容易漏的编译错误之一。
2. **多值返回不能随意直传**:`return DecodeMessage(...)` 只在返回类型**完全一致**时合法,
   `map[string]interface{}` 不等于 `interface{}`,必须拆成两条语句。
3. **wire type 与字段类型必须一致**,否则解析错位:同字段号但 wire type 不同的记录,
   解析器会按错误长度读 —— 这正是"必须按 wire type 定位 payload 边界"的意义。
4. **不要拿序列化字节做缓存键/哈希/签名**(官方明说默认序列化不确定)。
5. **字段号一旦上线就不能改**:它才是线上的名字。改名免费,改号等于换字段。
6. **19000..19999 是官方保留区**。
7. **拆文件后一定要补/删 import**:Python 的 `List[Entry]` 注解在 def 执行时求值;
   Go 的未用 import 是硬编译错误 —— 本轮这两类问题各踩了一次。

## 九、参考资料

- protobuf.dev `programming-guides/encoding/`:MSB 与低位在前、官方 `Test1` 的 `08 96 01`、
  六种 wire type 表、tag 公式、intN 补码 vs sintN ZigZag 对照表、Non-varint 的 IEEE754、
  Length-Delimited、packed 双向兼容、Field Order 的不确定性 Implications、Last One Wins
  与嵌入消息合并、SGROUP/EGROUP 字段号配对
- protobuf.dev `programming-guides/proto3/`:字段号范围 1..536,870,911、
  19,000..19,999 为官方保留区
- 均取自 `protobuf.dev` 官方站(HTML 抓取后用本地脚本转纯文本精读)
