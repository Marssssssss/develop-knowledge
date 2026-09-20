# HTTP/2 帧层、流控与 HPACK（RFC 9113 / RFC 7541）

HTTP/2 的复杂度几乎全在**帧层**与**头部压缩**这两块：一条连接上跑 N 条流、每条流各有自己的流控窗口、头字段靠静态表 + 动态表 + 霍夫曼压成字节。本 demo 把帧头编解码、SETTINGS 初始值、流控算术和 HPACK 整数/字面量表示做成可执行模型，全部拿 RFC 附录里的**官方向量**做字节级对拍。

## 一、帧头：固定 9 字节

```text
+-----------------------------------------------+
|                 Length (24)                   |
+---------------+---------------+---------------+
|    Type (8)   |   Flags (8)   |
+-+-------------+---------------+-------------------------------+
|R|                 Stream Identifier (31)                      |
+=+=============================================================+
|                   Frame Payload (0..)                        |
+---------------------------------------------------------------+
```

- **`Length` 不含帧头那 9 字节**——原文「The 9 octets of the frame header are not included in this value」。
- `R` 是 1 位保留位：发送时必须为 0，**接收时必须忽略**（不能因为它非 0 就报错）。
- `Stream Identifier` 是 31 位；取 0 表示这一帧作用于**整条连接**（SETTINGS / PING / GOAWAY / 连接级 WINDOW_UPDATE）。
- 类型码：`0x0 DATA 0x1 HEADERS 0x2 PRIORITY 0x3 RST_STREAM 0x4 SETTINGS 0x5 PUSH_PROMISE 0x6 PING 0x7 GOAWAY 0x8 WINDOW_UPDATE 0x9 CONTINUATION`；**未知类型必须忽略并丢弃**。

## 二、帧大小：默认 16 KB，区间 [2¹⁴, 2²⁴−1]

§4.2：所有实现必须能收下 `2^14` 字节 + 9 字节帧头。超过对端通告的 `SETTINGS_MAX_FRAME_SIZE` → `FRAME_SIZE_ERROR`；而 `SETTINGS_MAX_FRAME_SIZE` 本身落在区间外 → `PROTOCOL_ERROR`（实测 16383 与 16777216 都被判越界）。

实践中**不要**把帧打满：大帧会阻塞 `RST_STREAM`、`WINDOW_UPDATE`、`PRIORITY` 这类时效敏感的帧。

## 三、SETTINGS 的六个参数与初始值（§6.5.2）

| 标识 | 参数 | 初始值 |
| --- | --- | --- |
| 0x01 | SETTINGS_HEADER_TABLE_SIZE | 4096 字节 |
| 0x02 | SETTINGS_ENABLE_PUSH | 1 |
| 0x03 | SETTINGS_MAX_CONCURRENT_STREAMS | 无限制（建议 ≥100） |
| 0x04 | SETTINGS_INITIAL_WINDOW_SIZE | 65535（2¹⁶−1） |
| 0x05 | SETTINGS_MAX_FRAME_SIZE | 16384（2¹⁴） |
| 0x06 | SETTINGS_MAX_HEADER_LIST_SIZE | 无限制 |

两条容易踩的：`ENABLE_PUSH` **服务端只能发 0**（客户端收到服务端发来的 1 是 `PROTOCOL_ERROR`）；`MAX_CONCURRENT_STREAMS` 是**方向性**的——它限制的是「本端允许对端创建多少条流」。

## 四、流控：官方那个 −44 KB 的例子

流控有两级：**连接窗口** + **每条流各自的窗口**，发送量受两者共同限制。

§6.9.2 原文：

> if the client sends 60 KB immediately on connection establishment and the server sets the initial window size to be 16 KB, the client will recalculate the available flow-control window to be **-44 KB**

复算过程（实测可复现）：

```text
默认初始窗口 65535，先发掉 60 KB(61440) → 流窗口剩 4095
收到 SETTINGS_INITIAL_WINDOW_SIZE = 16384
规范口径是「按新旧初始值之差调整」：4095 + (16384 − 65535) = −45056 = −44 KB
窗口为负时 MUST NOT 再发流控帧，直到 WINDOW_UPDATE 把它拉回正数
```

**`SETTINGS` 帧改不了连接窗口**（原文「A SETTINGS frame cannot alter the connection flow-control window」）——连接窗口只能靠 `WINDOW_UPDATE`。实测：改初始窗口后连接窗口纹丝不动，流窗口按差值变化。

任何窗口超过 `2^31-1` 都是 `FLOW_CONTROL_ERROR`。

## 五、HPACK 整数表示（§5.1）

小于 `2^N - 1` 直接放进 N 位前缀；否则前缀置全 1，剩余值按 **128 进制**续写，每个续字节最高位置 1，最后一字节不置。

附录 C.1 的官方向量（本模型逐字节对拍通过）：

| 值 | 前缀 | 编码 |
| --- | --- | --- |
| 10 | 5 位 | `0x0A` |
| 1337 | 5 位 | `0x1F 0x9A 0x0A` |
| 42 | 8 位 | `0x2A` |

注意边界：5 位前缀下单字节能表示的最大值是 **30**（31 就已经要走续字节了，`0x1F 0x00`）。

## 六、附录 C.3.1：首个请求的官方字节序列

```text
8286 8441 0f77 7777 2e65 7861 6d70 6c65
2e63 6f6d
```

解码过程：

| 字节 | 含义 | 结果 |
| --- | --- | --- |
| `82` | 索引表示，idx=2 | `:method: GET` |
| `86` | 索引表示，idx=6 | `:scheme: http` |
| `84` | 索引表示，idx=4 | `:path: /` |
| `41` | 带索引字面量，**名字取索引 1** | `:authority` |
| `0f` | 字面值长度 15 | `www.example.com` |

解码后动态表：**`[1] (s = 57) :authority: www.example.com`**。

条目大小公式（§4.1）是 `len(name) + len(value) + 32`：`10 + 15 + 32 = 57`，与官方标注一致。这 32 字节的**固定开销**是很多人估算 HPACK 内存时漏掉的部分。

## 七、静态表：61 项，开箱可用

静态表（附录 A）前几项几乎覆盖了每次请求都要发的头：

```text
1 :authority   2 :method GET   3 :method POST   4 :path /
5 :path /index.html   6 :scheme http   7 :scheme https
8 :status 200  9 204  10 206  11 304  12 400  13 404  14 500
16 accept-encoding: gzip, deflate      ... 61 www-authenticate
```

索引 0 **非法**；动态表为空时索引 62 就越界。动态表超出 `SETTINGS_HEADER_TABLE_SIZE` 时从**最旧**一端驱逐。

> 本模型未实现霍夫曼编码（附录 B 的码表有 257 项），只处理原始字节字符串——这与附录 C.2 / C.3 的无霍夫曼示例口径一致。霍夫曼是**可选**的压缩层，编码器可以逐字符串决定用不用。

## 八、运行方式

```bash
python selfcheck_h2.py     # 52 项断言
```

## 九、关键代码

- `main.py` — `pack_frame` / `unpack_frame` / `FlowControl` / `hpack_encode_int` / `hpack_decode_int` / `HpackDecoder` / 静态表
- `h2.go` — Go 侧帧头与流控（`encoding/binary` 直接表达 24/31 位字段）
- `hpack.go` — Go 侧整数表示、静态表、动态表驱逐（与 `h2.go` 同包，需 `go run .`）
- `selfcheck_h2.py` — 全部断言拿 RFC 附录官方向量做字节级对拍

## 十、注意事项与常见坑

1. **`Length` 不含帧头**。手写解析器时差 9 字节会整体错位。
2. **保留位不能用来做校验**——规范明确要求忽略。
3. **`SETTINGS` 改不了连接窗口**，只能 `WINDOW_UPDATE`。
4. **窗口可以为负**，且为负时禁止发送（不是 clamp 到 0 就算了）。
5. **`SETTINGS_INITIAL_WINDOW_SIZE` 的语义是「按差值调整已有窗口」**，不是「把窗口直接设成新值」——这两者在已有在途数据时差出一个已发送量。
6. **HPACK 动态表是 FIFO 且带 32 字节/条目的开销**，估算内存要算进去。
7. **动态表是连接级的有状态压缩**，这也是 CRIME/BREACH 类攻击在 HTTP/2 上仍要防范的原因（敏感值不应与攻击者可控输入放在同一字段块里）。
8. **未知帧类型必须丢弃而不是报错**，这是协议可扩展性的基础。
9. `CONTINUATION` 必须紧跟在未置 `END_HEADERS` 的 `HEADERS`/`PUSH_PROMISE`/`CONTINUATION` 之后，否则是 `PROTOCOL_ERROR`。

## 十一、参考资料

- RFC 9113 — HTTP/2 — <https://www.rfc-editor.org/rfc/rfc9113.txt>（§4.1 帧格式、§4.2 帧大小、§5.1 流状态、§6.5.2 SETTINGS 参数、§6.9 WINDOW_UPDATE、§7 错误码）
- RFC 7541 — HPACK: Header Compression for HTTP/2 — <https://www.rfc-editor.org/rfc/rfc7541.txt>（§4.1 条目大小、§5.1 整数表示、§5.2 字符串字面量、附录 A 静态表、附录 C 示例）
