# TCP 粘包 / 拆包 — 长度前缀与 TLV 流式解析

## 简介

TCP 是**字节流**协议,不维护消息边界,内核保证"按序到达"但不保证"按 send 边界到达"。
应用层必须自行分隔消息,这叫**应用层帧定界**(Framing)。

- **粘包**(sticky packet):Nagle 算法、内核 buffer 聚合导致 `recv()` 一次返回多个 send 的数据
- **拆包**(split packet):一个消息被 `recv()` 切成多段;IP 分片、TCP 拥塞窗口等都是原因

游戏服务器常用的解决方案:**长度前缀**(Length-Prefixed),本质 TLV(Type-Length-Value)。

- **关键概念清单**
  - **粘包 / 拆包**:TCP 字节流 vs 应用层消息语义的鸿沟
  - **固定长度帧**:每条消息相同长度,简单但浪费带宽
  - **分隔符**:`\r\n` 等,文本协议(Redis/RESP、HTTP/1)常用
  - **长度前缀 + TLV**:工业界标准,Protobuf/HTTP/2/gRPC 都用
  - **状态机解析器**:`WAITING_LEN → WAITING_PAYLOAD → COMPLETE`
  - **环形缓冲区**(ring buffer):避免 `memmove` 拷贝,见 Netty `ByteToMessageDecoder`

## 原理详解

### 帧格式(本 demo 采用)

```
+--------+--------+----------------+
| Magic  | Length |   Payload      |
| 4 B    | 4B BE  |  Length B      |
| "GAME" | u32 BE |  variable      |
+--------+--------+----------------+
```

- **Magic** `GAME`(4 B):过滤乱码、检测协议错配,做 resync 时也作为锚点
- **Length** u32 big-endian:声明 payload 字节数(网络序)
- **Payload**:消息体,可含业务层 protobuf / JSON / 私有二进制

四个可选字段:Type(1B) → Magic(4B) → Length(4B) → Payload。`Length` 字段本身的字节序/大小需发送与接收方一致。

### 为什么必须用状态机

```
       (粘包:N>1 完整消息挤在一次 recv)
                              ┌──────────────┐
                              ▼              │
  recv() ──► Feed(buf) ──► state machine ──► take() ──► handle(frame)
                              │              │
                              ▼              │
                         (拆包:半个消息缓存在 parser.buf)
```

每个连接保留一个解析器,buffer 容量 ≥ `HDR_LEN + MAX_FRAME`,流式状态机处理:
1. **WAITING_HEADER**:攒满 8B → 解码 `length` → 校验 ≤ `MAX_FRAME`
2. **WAITING_BODY**:攒满 `length` B → 拷贝 body 给业务
3. **handle**:执行后写回响应,不必等到全部消息收齐

### 错误恢复:resync

一旦 magic 不匹配,丢弃 1 字节重试。这样能容忍**半连接**场景(Half-Open Connection:
对端崩溃但本端 TCP 还没收到 RST),代价是 ~25% 的扫描开销(4 字节平均匹配)。

```python
while True:
    if len(self.buf) < HDR_LEN: break
    if self.buf[:4] != MAGIC:
        del self.buf[0]      # resync
        continue
    body_len = struct.unpack("!I", self.buf[4:8])[0]
```

## 对比 / 选型

| 方案 | 实现难度 | 带宽效率 | 适用 | 代表 |
| --- | --- | --- | --- | --- |
| 固定长度帧 | 极简 | 低(平均浪费大) | MCU/PLC 指令集 | Modbus |
| 分隔符 `\n` | 简单 | 中 | 文本协议 | Redis RESP、HTTP/1、syslog |
| **长度前缀 + TLV** | 中等 | 高 | **现代二进协议 (推荐)** | **Protobuf + 长度前缀、gRPC、Kafka、MongoDB Wire** |
| 自描述长度前缀 + 校验 | 中高 | 中高 | 易错环境 | Protobuf + CRC32 |
| 整流协议(per message stream ID) | 高 | 中 | 多路复用 | QUIC、HTTP/2 |
| 完全长度前缀 + 压缩 | 高 | 极高 | 内部 RPC | Cap'n Proto、FlatBuffers |

## 环境准备

- **C**:Linux(`epoll`)
- **Python**:3.8+,纯 stdlib
- **Go**:1.18+

## 运行方式

### C(Linux)

```bash
gcc -O2 -Wall -Wextra c/tcp_framing.c -o tcp_framing
./tcp_framing 9000
# 用 netcat 发送多包验证粘包/拆包:
printf 'GAME\x00\x00\x00\x05helloGAME\x00\x00\x00\x05world' | nc 127.0.0.1 9000
```

### Python

```bash
python3 python/tcp_framing.py 9000
```

### Go

```bash
cd go && go build -o tcp_framing tcp_framing.go && ./tcp_framing 9000
```

## 关键代码片段

### C(状态机片段,完整见 `c/tcp_framing.c`)

```c
for (;;) {
    if (p->state == 0) {                       /* WAITING_HEADER */
        if (p->filled < HDR_LEN) break;
        if (memcmp(p->buf, MAGIC, 4) != 0) {
            memmove(p->buf, p->buf + 1, p->filled - 1);   /* resync */
            p->filled -= 1; continue;
        }
        p->body_len = (p->buf[4]<<24)|(p->buf[5]<<16)|(p->buf[6]<<8)|(p->buf[7]);
        memmove(p->buf, p->buf + HDR_LEN, p->filled - HDR_LEN);
        p->filled -= HDR_LEN;
        p->state = 1;
    }
    if (p->state == 1) {                       /* WAITING_BODY */
        if (p->filled < p->body_len) break;    /* partial */
        /* emit body, then back to header */
    }
}
```

### Python 解析器

```python
def feed(self, data: bytes) -> None:
    self.buf.extend(data)
    while True:
        if self.state == 0:
            if len(self.buf) < HDR_LEN: return
            if self.buf[:4] != MAGIC:               # resync
                del self.buf[0]; continue
            self.body_len = struct.unpack("!I", self.buf[4:8])[0]
            del self.buf[:HDR_LEN]
            self.state = 1
        if self.state == 1:
            if len(self.buf) < self.body_len: return  # partial
            return  # body complete; let take() consume
```

### Go 解析器

```go
func (p *Parser) Take() []byte {
    if p.inHeader || len(p.buf) < int(p.bodyLen) {
        return nil
    }
    body := p.buf[:p.bodyLen]
    p.buf = p.buf[p.bodyLen:]
    p.inHeader = true
    p.need = hdrLen
    return append([]byte(nil), body...)
}
```

## 性能与边界

- **memmove 复杂度** = O(n) 在每次 `memmove`。Netty 用 `ByteBuf` + `readerIndex/writerIndex` 避免拷贝
- **解析 O(1)** 每就绪事件(handler 是 O(payload_size))
- **buffer 上限** = `HDR_LEN + MAX_FRAME`(本 demo = 8 + 1 MiB)
- **resync 复杂度** 平均扫描 ~4B / 误用字节;最坏是 `N` B 全错,需拉满整个 buffer
- **MTU 影响** 1500B 一帧,大消息会触发 IP 分片 → TCP 重组 → 应用层一次 `recv` 全到

## 注意事项与常见坑

| 现象 | 原因 | 规避 |
| --- | --- | --- |
| 缓冲区占内存暴涨 | 恶意小 header(超 MAX_FRAME)被反复喂入 | 设 `MAX_FRAME` 阈值,超出 reset |
| 解析卡死 | 客户端循环发 0 长度帧 | 把 `length==0` 视为协议错,reset |
| GB 级大消息阻塞 IO | 一次 send/rev 满 GB | 用流式 chunk + backpressure(滑动窗口) |
| Magic 不匹配 | 字节序/字符序不匹配 / 协议错配 | 多端用统一 magic (`"GAME"` + 长度),加 CRC32 |
| Half-Open Connection | 对端崩溃但 TCP 未 RST | 用心跳包(15-30s),保活探测 |
| Nagle 拖慢小消息 | Nagle + ACKnowledge 延迟 | `TCP_NODELAY`(见 §Nagle demo) |
| `recv` 切到一半 | 内核分片重组时机 | 状态机缓存,绝不用 `peek(MSG_WAITALL)` 假设 |
| 业务发送跟内部 buffer 不一致 | 半写场景 | 用 response queue + EPOLLOUT + writev |

## 参考资料

- [TCP 粘包与拆包 - feixiang.net](https://www.feixiang.net/tcp/sticky-and-split-packet.html) — 中文方案对比 + 长度前缀 Java 示例
- [互联网游戏服务中解决TCP粘包问题的有效策略 - 越赞网络](http://yxtmm9.com/product/12.html) — 游戏场景 TLV + Protobuf 实战
- [TCP粘包问题:如何准确区分多个连续发送的消息边界? - CSDN](https://ask.csdn.net/questions/9475355) — 三维评估矩阵 + Python 伪代码
- [TCP粘包/拆包问题:如何准确识别消息边界? - CSDN 问答](https://ask.csdn.net/questions/9448313) — 工业级解决方案选型
- [c/c高频面试:TCP粘包三种解决方案 - CSDN](https://blog.csdn.net/2302_81966227/article/details/158773219) — TLV 解析步骤
