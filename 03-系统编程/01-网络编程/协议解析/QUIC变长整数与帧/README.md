# QUIC 变长整数与帧解析

QUIC 把「长度」塞进第一个字节的最高两位,换来 1/2/4/8 字节四档弹性;
代价是**同一个值有多种合法编码**,于是协议对帧类型单独加了一条最短编码要求。

## 一、变长整数(RFC 9000 §16)

```
   The QUIC variable-length integer encoding reserves the two most
   significant bits of the first byte to encode the base-2 logarithm of
   the integer encoding length in bytes.  The integer value is encoded
   on the remaining bits, in network byte order.
```

| 2MSB | 长度 | 可用位 | 取值范围 |
| --- | --- | --- | --- |
| 00 | 1 | 6 | 0–63 |
| 01 | 2 | 14 | 0–16383 |
| 10 | 4 | 30 | 0–1073741823 |
| 11 | 8 | 62 | 0–4611686018427387903 |

即 **`length = 1 << prefix`**。附录 A.1 给的解码伪码:

```
ReadVarint(data):
  v = data.next_byte()
  prefix = v >> 6
  length = 1 << prefix
  v = v & 0x3f
  repeat length-1 times:
    v = (v << 8) + data.next_byte()
  return v
```

原文附的四个数(本 demo 逐条断言过):

| 字节序列 | 值 |
| --- | --- |
| `0x25` | 37 |
| `0x7bbd` | 15,293 |
| `0x9d7f3e7d` | 494,878,333 |
| `0xc2197c5eff14e88c` | 151,288,809,941,952,652 |

## 二、非最短编码是合法的——除了帧类型

```
Values do not need to be encoded on the minimum number of bytes
necessary, with the sole exception of the Frame Type field; see
Section 12.4.
```

所以原文才会写「the single byte 0x25 decodes to 37 (as does the two-byte
sequence 0x4025)」。本 demo 里 `is_shortest_encoding()` 就是这条判据,
`0x4025` 判定为**非最短**。

§12.4 对帧类型的原文:

```
To ensure simple and efficient implementations of frame parsing, a frame
type MUST use the shortest possible encoding.
```

## 三、帧与帧类型(RFC 9000 §12.4)

```
The payload of a packet that contains frames MUST contain at least one
frame, and MAY contain multiple frames and multiple frame types.  An
endpoint MUST treat receipt of a packet containing no frames as a
connection error of type PROTOCOL_VIOLATION.  Frames always fit within a
single QUIC packet and cannot span multiple packets.
```

另两条容易忽略的:

- **未知帧类型** → `FRAME_ENCODING_ERROR`(不是「跳过」,与 TLS 扩展、HTTP/2
  的未知帧处理相反)。
- 「All frames are idempotent in this version of QUIC」——重复收到同一个
  合法帧不应产生副作用或错误。

表 3 里本 demo 用到的片段(完整表见 RFC 9000 §12.4):

| Type | 名称 | Pkts |
| --- | --- | --- |
| 0x00 | PADDING | IH01 |
| 0x01 | PING | IH01 |
| 0x02–0x03 | ACK | IH_1 |
| 0x04 | RESET_STREAM | __01 |
| 0x06 | CRYPTO | IH_1 |
| 0x08–0x0f | STREAM | __01 |
| 0x1c–0x1d | CONNECTION_CLOSE | ih01 |

`PADDING` 与 `PING` 都**没有内容**,各占 1 字节。

## 四、ACK 帧的区间算术(§19.3 / §19.3.1)

```
ACK Frame {
  Type (i) = 0x02..0x03,
  Largest Acknowledged (i),
  ACK Delay (i),
  ACK Range Count (i),
  First ACK Range (i),
  ACK Range (..) ...,
  [ECN Counts (..)],
}
```

第一段:`smallest = largest − first_ack_range`。

后续每一段由 `(Gap, ACK Range Length)` 递归推出,原文给的公式:

```
      largest = previous_smallest - gap - 2
```

以及:

```
The number of packets in the gap is one higher than the encoded value of
the Gap field.
```

即 **Gap 字段编码值 = 洞里的包数 − 1**;`−2` 里的另一个 `−1` 是「跳过洞之后
再往左一格」。算出负数 → `FRAME_ENCODING_ERROR`。

本 demo 的算例:

| largest | first | ranges | 解出的区间 |
| --- | --- | --- | --- |
| 10 | 2 | [(1,1)] | [8,10]、[4,5](gap=1 → 洞里有 6、7 两个包) |
| 10 | 2 | [(0,1)] | [8,10]、[5,6](gap=0 → 洞里只有 7) |
| 20 | 1 | [(1,0),(2,3)] | [19,20]、[16,16]、[9,12] |
| 2 | 0 | [(1,0)] | `largest = 2−1−2 = −1` → FRAME_ENCODING_ERROR |

另外两个点:

- `ACK Delay` 要**乘** `2^ack_delay_exponent`(由发送方的传输参数给出),
  用指数换分辨率。
- 类型 0x03 才带 ECN Counts;`ECN Counts` 由三个变长整数组成。

## 五、代码

| 文件 | 说明 |
| --- | --- |
| `python/quic_varint.py` | 变长整数编解码、最短编码判定、ACK 区间、帧切分 |
| `python/selfcheck_quic.py` | 76 条断言(实跑全绿) |
| `python/main.py` | 八张对照表 |
| `go/quic_varint.go` + `go/main.go` | Go 转写与同构断言 |

## 参考资料(2026-09-24 10:00 槽实际抓取并阅读)

- RFC 9000《QUIC: A UDP-Based Multiplexed and Secure Transport》
  §12.4 帧与帧类型(表 3)、§16 变长整数编码、§19.1 PADDING、§19.2 PING、
  §19.3 / §19.3.1 ACK 帧与 ACK Ranges、§19.3.2 ECN Counts、
  附录 A.1 样例 — https://www.rfc-editor.org/rfc/rfc9000.txt
- RFC 9002《QUIC Loss Detection and Congestion Control》
  (同槽抓取,用于对照 ACK 的消费方式) — https://www.rfc-editor.org/rfc/rfc9002.txt

## 口径说明

- 本 demo 只覆盖**帧层以下**的编码:不涉及头部保护、包号编解码(附录 A.2)、
  TLS 握手与流控。
- `parse_frames()` 只建模无内容的 `PADDING` / `PING`;遇到其余**已知**类型时
  本 demo 会**报错**而不是假装解析——因为它没有这些帧的字段定义,
  静默跳过会变成「通过 ≠ 验到」。
- QUIC 版本协商、无状态重置、重试包**不含帧**,本 demo 未涉及。
