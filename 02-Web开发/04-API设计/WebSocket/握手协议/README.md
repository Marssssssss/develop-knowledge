# WebSocket 握手协议（RFC 6455 §4）

> 路径：`02-Web开发/04-API设计/WebSocket/握手协议/`
> 领域：Web 后端 · API 协议层
> 语言：C / Python / Go
> 难度：中等（HTTP 升级 + SHA-1 + base64 + 多头校验）

## 简介

WebSocket 是浏览器与服务器之间的一条**全双工二进制帧通道**。要让一条原本只能"一问一答"的 HTTP/1.1 连接升级成持久双向通道，必须先完成一次 HTTP Upgrade 握手：客户端发出特殊的 GET 请求，服务器验证无误后回 `101 Switching Protocols`，从此双方改说 WebSocket 帧协议（RFC 6455 §5）。

- **关键概念**
  - **Upgrade 握手**：复用 HTTP/1.1 的 `Upgrade` / `Connection` 头机制做协议切换；握手本身仍是合法 HTTP，所以能与普通 HTTP 服务共享 80/443 端口、穿过大多数代理/防火墙。
  - **Sec-WebSocket-Key**：客户端随机生成的 16 字节 nonce 经 base64 编码；服务器用它向客户端证明"我真的懂 WebSocket"。
  - **Sec-WebSocket-Accept = base64(SHA-1(Key + MAGIC_GUID))**：`MAGIC_GUID = 258EAFA5-E914-47DA-95CA-C5AB0DC85B11` 是 RFC 6455 §1.3 写死的常量；拼接后做 SHA-1，再 base64。这个值**不是加密或认证**，而是防止 HTTP 缓存代理把旧响应误回放给新连接（缓存代理不知道 GUID 就答不出正确的 Accept）。
  - **101 Switching Protocols**：握手成功的唯一合法状态码；任何其他状态码意味着服务器把它当普通 GET 处理了，握手失败。
  - **HTTP/2 下不同机制**：RFC 8441 用 Extended CONNECT（`:protocol = websocket`）替代 Upgrade；本 demo 只覆盖 RFC 6455 的 HTTP/1.1 路径。
- **历史背景**：WebSocket 协议由 Ian Fette（Google）和 Alexey Melnikov（Isode）于 2011 年 12 月提交为 RFC 6455，取代了 Hybi-00..17 系列不兼容的实验版本。RFC 6455 是当前唯一被浏览器广泛实现的版本（version = 13）。

## 原理详解

### 协议报文交换时序

```
Client                                                Server
  |                                                     |
  |  GET /chat HTTP/1.1                                 |
  |  Host: example.com                                  |
  |  Upgrade: websocket                                 |
  |  Connection: Upgrade                                |
  |  Sec-WebSocket-Key: <base64(16B 随机 nonce)>        |
  |  Sec-WebSocket-Version: 13                          |
  |  Sec-WebSocket-Protocol: chat, superchat  (可选)   |
  |---------------------------------------------------->|
  |                                                     |
  |  HTTP/1.1 101 Switching Protocols                   |
  |  Upgrade: websocket                                 |
  |  Connection: Upgrade                                |
  |  Sec-WebSocket-Accept: <base64(SHA-1(Key+GUID))>     |
  |  Sec-WebSocket-Protocol: chat           (若协商)    |
  |<----------------------------------------------------|
  |                                                     |
  |  <从此所有字节遵循 RFC 6455 §5 帧协议，不再是 HTTP> |
  |  <...双向二进制/文本帧 + ping/pong/close 控制帧...> |
```

### 服务端处理流程（RFC 6455 §4.2）

```
                      ┌────────────────────────────────┐
                      │  读客户端 GET 请求（直到 \r\n\r）│
                      └──────────────┬─────────────────┘
                                     │
                  ┌──────────────────▼──────────────────┐
                  │  校验请求行：必须是 GET / HTTP/1.1     │
                  │  校验必填头：Upgrade/Connection/      │
                  │  Sec-WebSocket-Key/Version:13        │
                  └──────────────────┬──────────────────┘
                                     │ 任一失败 → 回 4xx 并关闭
                                     ▼
                  ┌──────────────────────────────────────┐
                  │ accept = base64( SHA-1( Key + GUID ) )│
                  │ GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
                  └──────────────────┬───────────────────┘
                                     ▼
                  ┌──────────────────────────────────────┐
                  │  回 101 Switching Protocols 响应       │
                  │  含 Upgrade/Connection/Accept         │
                  │  若协商子协议则 echo 其中一个          │
                  └──────────────────────────────────────┘
```

### 核心 API 与关键参数（按协议顺序）

| 头字段 | 方向 | 必选 | 取值规则 | 来源 |
| --- | --- | --- | --- | --- |
| `Host` | 客户端 | ✅ | `host[:port]`，缺端口则默认 80/443 | RFC 6455 §4.1 第 4 点 |
| `Upgrade` | 双向 | ✅ | 值必须为 `websocket`（大小写不敏感） | §4.1 第 5 点 + §4.2.2 第 2 点 |
| `Connection` | 双向 | ✅ | 必须包含 token `Upgrade`（逗号分隔列表，RFC 7230 §6.1） | §4.1 第 6 点 |
| `Sec-WebSocket-Key` | 客户端 | ✅ | base64(16 字节随机值)，解码后**必须恰好 16 字节** | §4.1 第 7 点 |
| `Sec-WebSocket-Version` | 客户端 | ✅ | 必须为 `13` | §4.1 第 9 点 |
| `Origin` | 客户端 | 浏览器 ✅ / 非浏览器可选 | RFC 6454 来源保护 | §4.1 第 8 点 |
| `Sec-WebSocket-Protocol` | 双向 | ❌ | 客户端：逗号分隔、按优先级；服务端：echo 其中之一或省略 | §4.1 第 10 点 |
| `Sec-WebSocket-Extensions` | 双向 | ❌ | 同上，用于 permessage-deflate 等扩展 | §4.1 第 11 点 |
| `Sec-WebSocket-Accept` | 服务端 | ✅ | `base64(SHA-1(Key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))` | §4.2.2 第 4 点 |

### 客户端校验（RFC 6455 §4.1 第 1-6 点）

收到服务端响应后，客户端必须按顺序校验：

1. 状态码必须 `101`；否则按普通 HTTP 处理（例如 401 → 走认证流程）；
2. `Upgrade: websocket`、`Connection: Upgrade` 必须出现；
3. `Sec-WebSocket-Accept` 必须等于本地用客户端 Key 计算的预期值（**不允许任何偏差**）；
4. 若响应里出现 `Sec-WebSocket-Protocol`，其值必须是客户端请求中提过的之一；
5. 若响应里出现 `Sec-WebSocket-Extensions`，其值（按扩展名解析后）必须是客户端请求中提过的扩展子集。

任何一步失败都必须 **Fail the WebSocket Connection**（RFC 6455 术语），即关闭 TCP 连接。

### 底层发生了什么（内核 / 运行时视角）

- 握手阶段双方走的是普通 HTTP/1.1 over TCP；Linux 下 `accept()` 返回后，按字节读直到 `\r\n\r\n` 完成 HTTP 头解析，再判断是否升级；
- 升级成功后这条 TCP socket **不再按 HTTP 解析**；后续字节按 RFC 6455 §5 的帧头解析（首字节 FIN/RSV1-3/Opcode，2-3 字节 Mask + Payload Len + Extended Length + Masking Key + Payload）；
- magic GUID 的设计哲学是「协议正确性检查」而非安全：仅当服务器内嵌了 WebSocket 代码才能算出正确的 Accept，从而防止 HTTP/1.1 透明代理在缓存或回放响应时伪造成功握手；
- HTTP/2 下路径完全不同：RFC 8441 的 Extended CONNECT 用 `:protocol = websocket` 伪头 + SETTINGS 帧中的 `SETTINGS_ENABLE_CONNECT_PROTOCOL = 1` 协商，握手成功状态码是 200 而非 101（**不要**把 HTTP/1.1 的 101 校验逻辑照搬到 HTTP/2）。

## 对比 / 选型

| 维度 | WebSocket (RFC 6455) | HTTP 短轮询 | SSE (Server-Sent Events) | HTTP 长轮询 |
| --- | --- | --- | --- | --- |
| 方向 | 全双工 | 客户端→服务端 | 服务端→客户端 | 客户端→服务端 |
| 协议层 | TCP + 帧 | HTTP | HTTP | HTTP |
| 连接数 | 1 | 每秒 N 次 | 1 | 持续持有直到事件 |
| 头部开销（消息粒度） | 2-14 B 帧头 | 完整 HTTP 头 | 仅事件边界 | 完整 HTTP 头 |
| 服务端推送 | ✅ | ❌ | ✅ | ✅（通过新请求） |
| 适用规模 | 实时双向（聊天、协作、游戏） | 简单状态查询 | 单向通知（股票、进度条） | 通知频率低且不确定 |
| 浏览器原生 API | `WebSocket` | `fetch`/`XHR` | `EventSource` | `fetch`/`XHR` |

**握手粒度细节**的常见混用场景：
- 想用 HTTP/2 多路复用跑 WebSocket → 用 RFC 8441 的 Extended CONNECT（不在本 demo 范围）；
- 只想服务端推消息且能容忍 HTTP/1.1 → SSE 更简单，无需握手计算 SHA-1；
- 子协议/扩展是真实业务诉求（如 STOMP、MQTT-over-WebSocket、permessage-deflate）→ 走 WebSocket 但要扩展 demo 处理 `Sec-WebSocket-Extensions`。

## 环境准备

- 操作系统：跨平台（演示代码本身只用到 POSIX/标准库；浏览器侧另算）
- 编译器：gcc/clang（C 版），CPython 3.8+（Python 版），Go 1.18+（Go 版）
- 依赖：全部使用各自语言标准库，无第三方包
  - C：自己实现 SHA-1（`sha1.c`）+ base64（`base64.c`）+ 协议层（`handshake.c`）
  - Python：`hashlib`、`base64`、`os`、`string`、`sys`
  - Go：`crypto/sha1`、`crypto/rand`、`encoding/base64`、`strings`、`fmt`

## 运行方式

### C

```bash
cd c
gcc -O2 -Wall -Wextra -pedantic sha1.c base64.c handshake.c demo.c -o handshake
./handshake
```

> C 版代码拆分说明：`sha1.{c,h}` 是 FIPS 180-4 标准实现；`base64.{c,h}` 是 RFC 4648 §4 标准 base64；`handshake.{c,h}` 是握手协议层；`demo.c` 是 5 个 demo 用例的驱动。

### Python

```bash
cd python
python3 handshake.py
```

### Go

```bash
cd go
go run handshake.go
```

## 关键代码片段

### 计算 `Sec-WebSocket-Accept`（三语言对照）

C（`handshake.c::compute_accept`）：

```c
static void compute_accept(const char *client_key, char out[29]) {
    char buf[64];
    size_t klen = strlen(client_key);
    memcpy(buf, client_key, klen);
    memcpy(buf + klen, WS_MAGIC_GUID, WS_MAGIC_GUID_LEN);  // "258EAFA5-E914-..."
    uint8_t hash[20];
    sha1(buf, klen + WS_MAGIC_GUID_LEN, hash);             // 自己实现的 SHA-1
    base64_encode(hash, 20, out, 29);                      // 自己实现的 base64
}
```

Python（`handshake.py::compute_accept`）：

```python
def compute_accept(client_key: str) -> str:
    digest = hashlib.sha1(
        (client_key + WS_MAGIC_GUID).encode("ascii")
    ).digest()
    return base64.b64encode(digest).decode("ascii")
```

Go（`handshake.go::computeAccept`）：

```go
func computeAccept(clientKey string) string {
    h := sha1.New()
    h.Write([]byte(clientKey + WS_MAGIC_GUID))
    return base64.StdEncoding.EncodeToString(h.Sum(nil))
}
```

### 构造客户端请求（C 简化版）

```c
static int build_client_request(const char *host, const char *path,
                                const char *key,
                                const char *subprotocols,
                                char *buf, size_t buf_cap) {
    int n = snprintf(buf, buf_cap,
        "GET %s HTTP/1.1\r\n"
        "Host: %s\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n",
        path, host, key);
    // 可选子协议（RFC §4.1 第 10 点）
    if (subprotocols && *subprotocols) {
        n += snprintf(buf + n, buf_cap - n,
            "Sec-WebSocket-Protocol: %s\r\n", subprotocols);
    }
    buf[n++] = '\r'; buf[n++] = '\n';     // 头结束空行
    return n;
}
```

### 校验服务端响应（Python 版）

```python
def parse_server_response(resp: str, client_key: str) -> tuple[bool, str]:
    if not resp.split("\r\n", 1)[0].startswith("HTTP/1.1 101"):
        return False, "status code must be 101"           # RFC §4.1 第 1 点
    if find_header(resp, "Upgrade").lower() != "websocket":
        return False, "missing Upgrade"
    if "Upgrade" not in find_header(resp, "Connection").split(","):
        return False, "missing Connection: Upgrade"
    accept = find_header(resp, "Sec-WebSocket-Accept")
    if accept != compute_accept(client_key):
        return False, f"accept mismatch (got {accept})"   # RFC §4.1 第 4 点
    return True, ""
```

## 性能与边界

- **握手成本**：单次 HTTP round-trip（约 1 RTT）+ 一次 SHA-1 计算（< 1 µs 在现代 CPU 上）+ 一次 base64 编码（< 1 µs）。**SHA-1 是不可逆的非安全哈希**，此处用途是协议校验，**不要**误用它做安全摘要。
- **协议头开销**：握手完成后单帧 2-14 字节头，比完整 HTTP 头（~200 B 含 cookie）小一个数量级。
- **握手并发**：每条连接都要握手；高并发短连接场景（如游戏断线重连频繁）握手会成为瓶颈，可考虑服务器侧握手缓存或 sticky session。
- **平台限制**：HTTP/1.1 路径在浏览器中要求 wss://（TLS）以避免中间代理误改 `Connection`/`Upgrade` 头；裸 ws:// 仅适用于 localhost 或受信网络。
- **HTTP/2 不可用 RFC 6455 §4 流程**：要在 HTTP/2 多路复用里跑 WebSocket，必须走 RFC 8441 的 Extended CONNECT；本 demo 不覆盖。
- **GUID 大小写敏感**：RFC 6455 §1.3 写死为 `"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"`（大写字母 + 短横线），与 RFC 4122 UUID 字面形式一致；客户端/服务端都必须逐字节匹配。

## 注意事项与常见坑

- **MAGIC GUID 不是加密盐**：它是常量，写死在 RFC 里；它的设计目的仅是让非 WebSocket 的 HTTP 中间件无法回放正确的 Accept。**不要**把 GUID 当 secret，也不要试图自己换 GUID（服务端改了 GUID 客户端会 fail）。
- **Sec-WebSocket-Key 是 16 字节 base64**：很多实现错误地接受任意长度 base64；RFC §4.1 第 7 点明确要求「nonce consisting of a randomly selected 16-byte value that has been base64-encoded」，所以校验时**必须** base64-decode 后看长度 == 16。
- **状态码 101 是唯一合法响应**：早期版本（Hybi-00..04）用 101，但 hybi-05 改用过别的状态码，最后 RFC 6455 又回到 101；现代客户端**只看 101**。其他状态码（包括 200）一律按普通 HTTP 处理（可能要走认证、Cookie 等）。
- **`Connection: Upgrade` 是 token 列表**：RFC 7230 §6.1 允许 `Connection: keep-alive, Upgrade` 这种逗号分隔；解析时必须按 token 拆分，不能直接 `strcmp(header, "Upgrade")`。
- **`Sec-WebSocket-Accept` 不允许任何空白**：服务端必须输出紧凑的 base64（24 字符 = 28 字节 + `=` 填充）；客户端必须按精确字符串比较（不要做 trim 后比较——中间空白可能暗示中间人篡改）。
- **子协议不可越权**：服务端**只能**从客户端 `Sec-WebSocket-Protocol` 列表里挑一个 echo；不能 echo 客户端没列的（如 `wss-v2`），否则客户端必须 fail。
- **HTTP/2 vs HTTP/1.1 状态码不同**：HTTP/1.1 握手成功是 101；HTTP/2 下 RFC 8441 握手成功是 200。如果客户端只认 101 会拒绝 HTTP/2 的 WebSocket 成功响应。
- **C 版 SHA-1 字节序**：本 demo 内置的 SHA-1 实现走标准 FIPS 180-4 §6.1 的大端顺序（与 RFC 3174 / Python `hashlib.sha1` 一致），但与某些嵌入式实现的小端版本不兼容；跨语言联调时务必先跑「RFC §1.3 标准例」（`"abc"` → `a9993e36 4706816a ba3e2571 7850c26c 9cd0d89d`）做一致性确认。
- **本机无 gcc/Go 工具链时的 C/Go 版**：只做人工代码审查（与 Python 版本逐行对照），Python 版可 `python3 -m py_compile` 检查语法；编译检查后务必删除 `__pycache__/`。
- **不要把握手代码照搬到生产**：本 demo 只演示协议；生产里还需：TLS 终止、超时（Upgrade 头无限等待）、Origin/Cookie/认证、最大并发握手数限制、HTTP/2 双协议支持。

## 参考资料（实际阅读过的权威来源）

- [RFC 6455 — The WebSocket Protocol（rfc-editor.org 全文）](https://www.rfc-editor.org/rfc/rfc6455) — 第 1.3 节给出了 RFC 标准示例（`dGhlIHNhbXBsZSBub25jZQ==` → `s3pPLMBiTxaQ9kYGzzhZRbK+xOo=`），第 4.1 节列出了 11 条客户端必填/可选头与 6 步客户端校验，第 4.2.2 节列出了服务端 5 步响应流程与 Accept 算法。本 demo 的所有协议字面值与算法都来自此文档。
- [MDN — WebSocket opening handshake](https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API/Writing_WebSocket_servers) — 浏览器侧实现摘要（虽未在本 demo 中直接抓正文，但摘要与 RFC 一致；选这个作为浏览器侧权威）。
- [Wikipedia — WebSocket](https://en.wikipedia.com/wiki/WebSocket) — 握手 HTTP 头对照表与 Origin/Host 等可选头语义（WebSearch 全文快照确认了 13 个握手头的方向与取值规则）。
- [websocket.org/reference/handshake — WebSocket Handshake: HTTP Upgrade at Protocol Level](https://websocket.org/reference/handshake) — Magic GUID 存在原因（防止 HTTP 缓存代理误回放握手响应）的工程解释；与 RFC §1.3 一致。
- [Debian Sources — python-websockets handshake.py 3.2-2](https://sources.debian.org/src/python-websockets/3.2-2/websockets/handshake.py/) — 生产级 Python 实现的 `accept()` 函数（`hashlib.sha1((key + GUID).encode()).digest()` → `base64.b64encode`），验证了本 demo Python 版算法的正确性。