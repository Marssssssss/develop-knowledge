# Bolt 协议：握手 + 分块传输 + PackStream 编码

## 简介

Bolt 是 Neo4j 3.0（2015-2016）起引入的**二进制客户端-服务端协议**（默认端口 7687），替代 REST API：连接**有状态**（类似 HTTP 的请求-响应，但无连接开销）、数据按 **PackStream** 二进制格式编码。协议栈三层：**握手（版本协商）→ 分块传输（chunking）→ PackStream（值编码）**。本 demo 用 Python + Go 双版本实现三层最小内核，全部断言均取自官方文档的字节示例。

## 原理详解

### 1. 握手（Handshake Specification）

- 连接建立后客户端**必须立即**发起握手：先发 4 字节 Bolt 标识 **`60 60 B0 17`**（无需响应），再发**恰好 4 个**大端 32 位协议版本（按偏好排序，不足补 `0x00000000` 占位）。
- 服务器回复 1 个 32 位选中版本；**零值 = 无匹配**，服务器立即关闭连接。
- 服务器应假设版本按偏好顺序发送，**多个可匹配时选第一个匹配**。
- 握手本身不区分版本；协商成功后消息协议接管连接。官方示例：客户端发 `[3,2,1,0]`，服务器支持 1 和 2 → 回 `00 00 00 02`。

### 2. 分块传输（Chunking）

- 每条消息编码为 **1..n 个 chunk**：`2 字节大端无符号长度头 + 数据`，单 chunk 理论上限 **65535** 字节。
- 每条消息以**零大小 chunk（`00 00`）终止**——这是消息边界标记，让接收方无需解析即可完整收包，也允许跳过未知消息类型。
- 消息可跨多个 chunk（无需预先声明总长）；**一个 chunk 不能包含多条消息**。
- Bolt 4.1+ 的空 chunk 兼作 **NOOP**（保活/流控）。
- 官方示例：20 字节消息 → chunk `00 10 <16B>` + chunk `00 04 <4B>` + 结束标记 `00 00`。

### 3. PackStream

每个值以 **marker byte** 开头，marker 同时携带**类型 + 规模**信息：

| 类型 | marker | 说明 |
| --- | --- | --- |
| Null / true / false | `C0` / `C3` / `C2` | 单字节 |
| TINY_INT | `00-7F` / `F0-FF` | -16..+127 单字节直存 |
| INT_8/16/32/64 | `C8`/`C9`/`CA`/`CB` | 大端有符号，选最紧凑表示 |
| Float | `C1` | 8 字节 IEEE 754 大端 |
| String | `80-8F` / `D0`/`D1`/`D2` | <16B 直存；**size 按 UTF-8 字节数而非字符数** |
| List | `90-9F` / `D4`/`D5`/`D6` | size = 元素个数 |
| Dictionary | `A0-AF` / `D8`/`D9`/`DA` | `key,value` 平铺序列 |
| Structure | `B0-BF` + **tag byte** | 复合值：marker + 1 字节签名 + ≤15 个字段 |

官方字节示例（demo 全部断言）：`42 → 2A`；`1.23 → C1 3F F3 AE 14 7A E1 47 AE`；`[1,2,3] → 93 01 02 03`；26 字节字符串 → `D0 1A ...`。整数故意不含无符号 32 位 / float32——为跨客户端语言兼容的**设计决策**。

Bolt 消息即 Structure：如 `RUN`（tag `0x10`）= `B3 10 <query> <params> <extra>`，服务器以 `SUCCESS`（`0x70`）/`FAILURE`（`0x7F`）/`RECORD`（`0x71`）响应；FAILURE 后服务器对后续消息一律回 `IGNORED`，直到客户端 `RESET`（与 PostgreSQL 线协议的失败恢复同构）。

## 环境

- Python 3.8+ / Go 1.18+（用了 `binary.BigEndian.AppendUint32/16`，需 Go 1.18+/1.19+），无第三方依赖。

## 运行方式

```bash
python bolt.py
go run bolt.go
```

按用户约定不实际运行，逻辑经人工代码审查（含一个真实 bug 的修复：接收方在消息未收全时不得消费缓冲，否则丢已到达的 chunk）。

## 关键代码

```python
# 分块: 长度头 + 数据, 00 00 结束
out += struct.pack(">H", len(part)) + part
out += b"\x00\x00"
# 接收方: 先扫描确认完整边界, 才一次性消费
(size,) = struct.unpack(">H", self.buf[pos:pos+2])
if size == 0:
    del self.buf[:pos + 2]
    return bytes(msg)
```

## 性能边界

| 层 | 边界 |
| --- | --- |
| chunk | 单 chunk ≤ 65535 B；更大消息必须拆多 chunk |
| String/List/Dict | size 字段最大 32 位，且实际上限 2^31-1（有符号 32 位最大值） |
| Structure | 最多 15 字段（`B0-BF`），更多需扩展 marker `DC-DE` |

## 注意事项与常见坑

1. **String size 是 UTF-8 字节数**：多字节字符（中文/emoji）下"字符数 == 字节数"的假设会错位解码（本 Go demo 只处理 ASCII 并已注明）。
2. **`00 00` 双重语义**：跟在数据 chunk 后是消息结束标记，独立出现是 NOOP。
3. **未收全时不能消费缓冲**：`next_message()` 在消息不完整时必须保持缓冲原样（demo 的 ChunkReader 修复点）。
4. **版本协商失败即断连**：0x00000000 响应后服务器直接关闭 TCP，客户端应立即停止而非重试读取。
5. **大小端**：Bolt 一律**大端**（网络字节序），包括 chunk 长度头、握手版本号、整数、浮点。

## 参考资料（实际读过）

1. Neo4j Bolt Protocol — PackStream（marker 全表与字节示例）: https://neo4j.com/docs/bolt/current/packstream/
2. Neo4j Bolt Protocol — Handshake specification（60 60 B0 17 与版本协商）: https://neo4j.com/docs/bolt/current/bolt/handshake/index.html
3. Bolt 协议消息规范（中文镜像，chunking 细节与 16/20 字节示例）: https://neo4j.net.cn/docs/bolt/current/bolt/message/
4. Neo4j Developer Blog — Running Neo4j on a Commodore 64（Bolt 三层实现的极简实战佐证）: https://neo4j.com/developer-blog/neo4j-commodore-64
