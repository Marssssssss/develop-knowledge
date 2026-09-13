# HTTP/1.1 请求解析器(状态机, RFC 7230)

## 简介

- HTTP/1.1 消息文本由 ASCII 字节流组成,核心结构:**Request-Line** + **Header Fields** + **空行** + **可选 body**。
- 本 demo 实现一个**状态机式**增量解析器,支持 RFC 7230 §3 的 `Request-Line` / `Header field` / `message-body length` 三大规则,以及 chunked transfer-coding 解码。
- 关键概念:**Header 名大小写不敏感**、**同名多行用逗号合并**、**行尾必须 CRLF**(老 HTTP/1.0 容许裸 LF)、**空行(CRLF)终结 header section**、**Content-Length / Transfer-Encoding 决定 body 长度**。
- 历史背景:RFC 1945(HTTP/1.0,1996)→ RFC 2068(1.1,1997)→ RFC 2616(1999,合并修订)→ **RFC 7230–7235**(2014,拆分为 5 篇);新 RFC 9110(2022)统合。本 demo 跟随 RFC 7230 §3。

## 原理详解

### 完整报文结构(BNF, RFC 7230 §3)

```
HTTP-message   = start-line *( header-field CRLF ) CRLF [ message-body ]
start-line     = request-line / status-line
request-line   = method SP request-target SP HTTP-version CRLF
header-field   = field-name ":" OWS [ field-value ] OWS
field-name     = token
field-value    = *( field-content / OWS )
field-content  = *( WSP / VCHAR / obs-text )
OWS            = *( SP / HTAB )            ; optional whitespace
CRLF           = CR LF                     ; \r\n
message-body   = 取决于 Content-Length 或 Transfer-Encoding=chunked
```

### 状态机

```
                ┌──── \r\n (CRLF) ─── 跳过(可选)
                │
[WAIT_REQ] ────► [READ_REQ_LINE] ─ split on SP → method/target/version
                       │
                       │ 没遇到 \r\n → 累积,继续读
                       ▼
                [READ_HEADERS] ─── 遇到空行(CRLF)
                       │
                       ▼
                [HEADERS_DONE] ── 解析 Content-Length / Transfer-Encoding
                       │
            ┌──────────┼──────────────┐
            ▼          ▼              ▼
       [BODY_LEN]  [CHUNKED]      [NO_BODY]
       read N bytes  解析 chunk  size CRLF data CRLF ... 0 CRLF CRLF
```

### Header 合并(§3.2.2)

> A recipient MAY combine multiple header fields with the same field name into one "field-name: field-value" pair, without changing the semantics of the message, by appending each subsequent field value to the combined field value in order, separated by a comma.

例:`X-Multi: a\r\nX-Multi: b\r\n` 解析后 `X-Multi: a, b`(逗号 + 单空格)。

### Body 长度决定(RFC 7230 §3.3)

1. 有 `Transfer-Encoding` → 按 chunked(或其它 transfer-coding)解码
2. 否则有 `Content-Length` → 字节数 = 该值
3. 否则 → 连接关闭即 body 结束(本 demo 不演示)

### 关键 API(本 demo)

| 入口 | 用途 |
| --- | --- |
| `parse_request_head(buf)` / `ParseRequestHead(buf)` | 解析单条 HTTP 请求头,返回 `{method, target, version, headers, body_start, body_len}` |
| `decode_chunked(r)` / Go 同名函数 | 流式解码 chunked body,返回 `bytes` / `io.Reader` |
| 内置 mini server | 演示真实 TCP 流上读 + 解析 + 响应 |

### 容错点(RFC 7230 §3.5)

- 接受裸 LF 终止行(老 HTTP/1.0 客户端)
- 接受 Request-Line 前多个空行
- Header 名大小写不敏感(统一转小写)
- 非法 Request-Line 返回 400 Bad Request

## 对比 / 选型

| 方案 | 适用 | 优缺点 |
| --- | --- | --- |
| **手写状态机**(本 demo) | 教学、协议验证、嵌入式 | 几十行可控;边界由自己把握 |
| 标准库 `http.Header` / `http.ReadRequest` | 生产 Go 服务 | 工业级健壮;源码 ≈ 1.5k 行 |
| `nginx http parser` | 高性能 C 服务 | 单文件 < 1500 行,内存复用优化 |
| `h11` / `h2` (Python) | 异步框架 | 第三方完整实现,支持 HTTP/1.1 增量 |
| `httptools` (Rust) | 零依赖 | 安全的零拷贝解析 |

> 真实工业解析器还需处理:**fold header**(legacy)、**obs-fold**(行首 SP 续行)、**trailer headers**、**100-continue expect** 等等。

## 环境准备

- Python ≥ 3.8
- Go ≥ 1.18

## 运行方式

### Python

```bash
cd python
python3 main.py test                  # 跑自测 5 个 case
python3 main.py serve 9090           # 启动 mini server
# 测试:
#   curl -v http://127.0.0.1:9090/hello
#   printf 'POST /upload HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n' | nc 127.0.0.1 9090
```

### Go

```bash
cd go
go run . test                        # 跑自测
go run . serve 9090                  # mini server
```

## 关键代码片段

Python 状态机核心(`python/main.py`):

```python
# 1) 跳过前导空行(RFC 7230 §3.5)
while pos < n and buf[pos:pos+2] == CRLF:
    pos += 2
# 2) 读 Request-Line
line, pos = _read_line(memoryview(buf), pos)
method, target, version = line.rstrip(b"\r\n").split(b" ")
# 3) 读 header,直到空行
while pos < n:
    line, nxt = _read_line(memoryview(buf), pos)
    if line in (b"\r\n", b"\n"): break
    name, _, value = line.rstrip(b"\r\n").partition(b":")
    # 4) 多同名合并为逗号分隔
    headers.setdefault(name.lower(), [])
# 5) 决定 body 长度
body_len = int(headers["content-length"]) if "content-length" in headers else \
            (-1 if headers.get("transfer-encoding") == b"chunked" else 0)
```

Go 版核心(`go/main.go`):

```go
parts := bytes.Split(bytes.TrimRight(line, "\r\n"), []byte(" "))
if len(parts) != 3 { return ErrBadHTTP }
// ... headers ...
name := strings.ToLower(strings.TrimSpace(string(trimmed[:idx])))
if prev, ok := headers[name]; ok {
    headers[name] = prev + ", " + value  // §3.2.2 合并
}
bodyLen := 0
if cl := headers["content-length"]; cl != "" { bodyLen, _ = strconv.Atoi(cl) }
else if headers["transfer-encoding"] == "chunked" { bodyLen = -1 }
```

## 性能与边界

- **行长度上限**:`max_header_bytes = 16 KiB`(本 demo 防御值;nginx 默认 `large_client_header_buffers 4 8k`,即 32 KB)
- **状态机 O(n)**:每个字节走一遍,适合流式;**不要把整请求一次性读到内存再解析**(本 demo 出于教学简化)
- **HTTP/1.1 pipelining**:多个请求可共用一个 TCP 连接,顺序回响应;本 demo 用 `Connection: close` 一请求一连接,简化模型
- **HTTP/2 / HTTP/3**:完全不同的二进制帧模型,本 demo 不适用

## 注意事项与常见坑

- ❌ **忘记把 header 名转小写**:`Host` / `host` / `HOST` 是同一个字段;字符串相等比较会漏。
- ❌ **裸字符串 `split(' ')` 不去 OWS**:`field-name : value`(冒号后空格)若不 `strip()` → value 带前导空格。
- ❌ **忽略 chunked size 后的 `;ext=`**:chunk-size 可带扩展,例如 `5;foo=bar\r\nhello\r\n`,必须从分号处截断。
- ❌ **chunked size 不区分大小写**:RFC 7230 §4.1 chunk-size 是十六进制,但部分扩展(如 chunked-gzip)对扩展名大小写敏感。
- ❌ **忘记 trailer header**:chunked 末块后可能有 trailer header(`Trailer: Expires` 等),本 demo 简化跳过。
- ❌ **依赖 `Connection: keep-alive` 默认值**:HTTP/1.1 默认 keep-alive,1.0 默认 close;解析器不应假设。

## 参考资料(实际阅读过的权威来源)

- [RFC 7230 — HTTP/1.1 Message Syntax and Routing(2014)](https://datatracker.ietf.org/doc/html/rfc7230) — 完整 BNF、§3 消息格式、§3.3 body 长度、§3.5 解析鲁棒性
- [RFC 9112 — HTTP/1.1(2022 合并版)](https://datatracker.ietf.org/doc/html/rfc9112) — RFC 7230 的取代与合并版本
- [MDN — HTTP Messages](https://developer.mozilla.org/en-US/docs/Web/HTTP/Messages) — 浏览器视角的格式图解
- [nginx http_parser 源码](https://github.com/nginx/nginx/blob/master/src/http/ngx_http_parse.c) — 工业级 C 实现,内存复用
- [Python http.server 模块 — BaseHTTPRequestHandler](https://docs.python.org/3/library/http.server.html) — Python 标准库 HTTP 服务器
- [Go net/http — ReadRequest](https://pkg.go.dev/net/http#ReadRequest) — Go 标准库 HTTP 解析
- [HTTP/1.1 draft-ietf-httpbis-p1-messaging-18](https://www.ietf.org/archive/id/draft-ietf-httpbis-p1-messaging-18.xml) — RFC 7230 之前的工作草案,可读性更佳