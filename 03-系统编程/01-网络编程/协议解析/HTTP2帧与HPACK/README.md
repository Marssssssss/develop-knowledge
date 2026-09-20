# HTTP/2 二进制分帧与 HPACK 头部压缩

HTTP/1.1 的解析器要跟文本打交道（见同目录 `HTTP11-Parser/`），HTTP/2 则完全相反：**一切都是有固定字节布局的结构体**。这个 demo 把「9 字节帧头」和「HPACK 整数前缀编码」拆到字节级，重点在几个**边界恰好踩在 `2^N-1` 上**的地方。

## 一、原理详解

### 1.1 帧头：9 个字节，且不计入 Length

RFC 9113 §4.1 的 ABNF：

```
HTTP Frame {
  Length (24),            # 3 字节大端
  Type (8),
  Flags (8),
  Reserved (1),           # 恒 0
  Stream Identifier (31), # 与 Reserved 共用一个 32 位字
  Frame Payload (..),
}
```

三条容易写错的规则：

| 规则 | 原文 | 后果 |
| --- | --- | --- |
| Length 口径 | *"The 9 octets of the frame header are not included in this value."* | 缓冲区至少要 9 字节才能判断「这一帧收完没有」 |
| Length 上限 | 未经 SETTINGS 放宽前 `MUST NOT` 超过 **2^14 = 16384** | 超了回 `FRAME_SIZE_ERROR`，不是静默截断 |
| Reserved 位 | 发送时 `MUST remain unset`，接收时 `MUST be ignored` | 收到 `0x80000001` 必须解析成 stream 1，**不能**报错 |

**未知帧类型**：`Implementations MUST ignore and discard frames of unknown types`。注意顺序 —— 得**先读 Length** 才能知道该丢弃多少字节，所以「忽略」不等于「不解析」。

**未定义 flags**：`MUST be ignored on receipt and MUST be left unset (0x00) when sending`。给 DATA 帧置 `0x40`（对 DATA 未定义）不是错误，接收方必须照常处理。

**Stream 0** 保留给连接级帧（SETTINGS / PING / GOAWAY / WINDOW_UPDATE）。

`SETTINGS_MAX_FRAME_SIZE` 区间是 **2^14 .. 2^24-1**：下界不能更小（否则兼容性没了），上界受 24 位 Length 限制。

### 1.2 HPACK 整数：边界是「严格小于」

RFC 7541 §5.1 的核心一句：

> *"If the integer value is small enough, i.e., **strictly less than** 2^N-1, it is encoded within the N-bit prefix."*

所以对 7 位前缀（`N=7`，阈值 `127`）：

| 值 | 编码 | 说明 |
| --- | --- | --- |
| 126 | `FE` | 1 字节，落在前缀内 |
| **127** | **`FF 00`** | **不是 `FF`！** 127 不小于 127，前缀填满后还要补一个续字节 `0x00` |
| 128 | `FF 01` | 前缀 127 + 余数 1 |

这个「等于阈值反而要两个字节」是 HPACK 最经典的实现坑。解码侧的写法是把**前缀读满就进入循环**：

```python
v = buf[pos] & limit
if v < limit:
    return v, pos + 1        # 提前返回，不进循环
...                          # v == limit 时才继续读续字节
```

续字节用最高位当继续标志（1 继续、0 结束），低位按 7 位一组**小端拼接**（先读到的在低位）。demo 覆盖了 0 / 1 / 10 / 126 / 127 / 128 / 1337 / 65535 / 2^20 的往返。

### 1.3 四种表示 + 一种控制

首字节的高位模式决定语义：

| 模式 | 位模式 | 低位含义 | 是否入动态表 |
| --- | --- | --- | --- |
| Indexed | `1xxxxxxx` | 7 位索引 | 否（只引用） |
| Literal + incremental | `01xxxxxx` | 6 位名索引 | **是** |
| Literal without indexing | `0000xxxx` | 4 位名索引 | 否 |
| Literal never indexed | `0001xxxx` | 4 位名索引 | 否 |
| Dynamic table size update | `001xxxxx` | 5 位新上限 | — |

两条硬规则：

1. **indexed 表示中索引 0 是解码错误**（§6.1 原文 *"It MUST be treated as a decoding error"*）—— 但字面量表示里索引 0 是**合法**的，含义是「名字也是字面量」。同一个数字，两种表示下的合法性相反。
2. `never indexed` 与 `without indexing` 对**动态表**的效果一样（都不入表），区别在于前者还要求中间节点**永不允许**把它索引进自己的动态表 —— 这是给「敏感头部」用的，压缩侧不该替它做决定。

### 1.4 动态表：FIFO + 逐出 + 32 字节开销

- 条目大小 `= len(name) + len(value) + 32`，那 32 字节是每条的固定记账开销（§4.1）。
- **索引 62 是最新条目**，数值越大越旧。取最大索引等价于取最新。
- 新增前先逐出：`evict until size <= max_size - new_entry_size`（§4.4），逐出方向是**尾部（最旧）**。
- **新条目一旦大于 max_size，整表清空，且「不算错误」**（原文 *"It is not an error to attempt to add an entry that is larger than the maximum size"*）。demo 断言了 `add()` 返回 `False` 且 `dyn == []`。

demo 的逐出实测：`max=100`、每条 34 字节，插到第三条时先腾空间到 `100-34=66`，于是最旧的 `("a","b")` 被挤掉，表仍是 2 条 68 字节。

### 1.5 压缩到底省在哪

第一次请求把 `cookie: sid=8` 以 incremental 形式发出（约 17 字节），**第二次只发一个索引字节 `0xBE`**。demo 断言 `len(blk2) == 1`。这才是 HPACK 的价值：把重复的头部名和值变成单字节引用。

代价是**状态耦合** —— 编码器和解码器的动态表必须严格同步。所以 HTTP/2 规定：**动态表大小更新只能出现在头部块的最前面**（否则解码器的表状态会先被字面量污染），且 `CONTINUATION` 帧保证一个头部块不被其他帧打断。

## 二、与 HTTP/1.1 解析的对比

| | HTTP/1.1（`HTTP11-Parser/`） | HTTP/2（本 demo） |
| --- | --- | --- |
| 定界方式 | 文本 + CRLF + Content-Length/chunked | 9 字节帧头里的 24 位 Length |
| 头部 | 明文、大小写不敏感、可合并 | HPACK 二进制、有状态、区分大小写（必须小写） |
| 并发 | 靠多条连接 / pipelining | 单连接多路复用，靠 31 位 Stream ID |
| 错误处理 | 尽量容错 | 未知类型/未定义 flags 必须忽略，越界必须报 error code |

**共同点**：两者都必须先确定「这一帧/这一段的边界」再谈语义。HTTP/1.1 靠找 `\r\n\r\n`，HTTP/2 靠读 9 字节头。

## 三、环境要求

- Python 3.8+（仅标准库）
- Go 1.18+（本机无 Go 工具链，人工审查 + 结构校验；运行用 `go run .`，两个 `.go` 同属 `package main`）

## 四、运行方式

```bash
cd HTTP2帧与HPACK
python selfcheck_h2.py    # 61 项断言
go run .                  # Go 版 27 项断言
```

## 五、关键代码

```python
def encode_int(value, n, prefix):
    limit = (1 << n) - 1
    if value < limit:                     # strictly less than 2^N-1
        return bytes([prefix | value])
    out = bytearray([prefix | limit])
    v = value - limit
    while v >= 128:
        out.append((v & 0x7F) | 0x80)
        v >>= 7
    out.append(v)
    return bytes(out)
```

```python
def decode(ctx, block):
    while pos < len(block):
        b = block[pos]
        if b & INDEXED:
            idx, pos = decode_int(block, pos, 7)
            if idx == 0:
                raise HPackError("indexed 表示中索引 0 必须判为解码错误")
            out.append(ctx.lookup(idx))
        elif b & INCREMENTAL:  ...   # 6 位名索引，入表
        elif b & SIZE_UPDATE:  ...   # 5 位新上限，改表
        else:                  ...   # 4 位名索引，不入表
```

## 六、性能边界

- **帧头 9 字节的固定开销**：对 1 字节 payload 的帧，有效率只有 10%。所以小数据必须合并（HEADERS 配合 CONTINUATION，DATA 尽量填满 16 KB）。
- **默认 16 KB 帧大小**对高 BDP 链路偏小；`SETTINGS_MAX_FRAME_SIZE` 可以调到 16 MB，但接收方必须愿意一次性缓冲这么多。
- **动态表默认 4096 字节**：能放约 120 个小头部。调大压缩率上升、内存上升，且**每次改动都要用 size update 通知对端**。
- **Huffman 编码**（本 demo 未实现码表）能再压 20~30%，但对已经随机的值（如 base64 token）反而会**膨胀** —— 规范因此保留 H 位让编码器逐字段选择。
- 本 demo 只验证编码布局与状态机，**不涉及真实 TCP 时序**。

## 七、注意事项与常见坑

1. **`2^N-1` 本身要两个字节**：「小于阈值走前缀」里的「小于」是严格小于。
2. **indexed 里索引 0 非法，字面量里索引 0 合法** —— 同一个数字两种待遇，靠首字节模式区分。
3. **Length 不含 9 字节头**，按总长减去 9 来算 payload 会少读 9 字节。
4. **必须先读 Length 才能丢弃未知帧**，「忽略未知类型」不等于「跳过 9 字节」。
5. **Reserved 位收到 1 不能报错**；反过来发送时必须写 0。
6. **同名同值的动态表条目是合法的**，规范明说 `duplicate entries MUST NOT be treated as an error` —— 不要按「去重」思路实现。
7. **一个头部块不能被其他帧打断**，跨帧必须用 CONTINUATION，且中间不允许出现任何其它类型帧。
8. **头部名必须小写**：HTTP/2 把大写头部名视为协议错误，这点与 HTTP/1.1 的大小写不敏感完全相反。
9. 本 demo 的 `decode_str` **只识别 H 位、不解 Huffman 码表**，因此不能直接用于生产解码；要完整实现需引入 RFC 7541 Appendix B 的 257 项码表。

## 八、参考资料

- RFC 9113《HTTP/2》§4.1 / §4.2 — https://www.rfc-editor.org/rfc/rfc9113.txt
- RFC 7541《HPACK: Header Compression for HTTP/2》§4.1 / §4.3 / §4.4 / §5.1 / §6.1 / Appendix A — https://www.rfc-editor.org/rfc/rfc7541.txt
