# HTTP 请求走私：CL.TE 与 TE.CL

HTTP/1.1 是**面向字节流**的协议：一条连接上可以连续排布多个请求，请求的边界由**消息体长度**决定。
当前端（反向代理 / CDN）与后端（应用服务器）对「这个请求有多长」算出不同的答案，多出来的字节就会
被后端当成**下一个请求的开头**——这就是请求走私（RFC 9112 §11.2）。

## 一、消息体长度的八条判定（RFC 9112 §6.3，按优先级顺序）

| # | 条件 | 结果 |
| --- | --- | --- |
| 1 | HEAD 的响应；1xx / 204 / 304 响应 | 首空行即结束，**不可能**有 body |
| 2 | CONNECT 的 2xx 响应 | 隧道，CL 与 TE 都忽略 |
| 3 | TE 与 CL **同时出现** | **TE 覆盖 CL**；原文明确点名「可能是请求走私尝试」 |
| 4 | TE 存在，chunked 是最终编码 | 按 chunked 解码 |
| 4' | TE 存在，chunked **不是**最终编码 | 请求 → **400 并关闭连接**；响应 → 读到连接关闭 |
| 5 | 无 TE，CL 非法 | 不可恢复错误；除非能按逗号列表解析、全部合法且**全部相同** |
| 6 | 无 TE，CL 合法 | 十进制定长 |
| 7 | 请求且以上都不成立 | 长度为 0 |
| 8 | 其余（响应） | 读到连接关闭 |

规则 3 是整个攻防的枢纽：它规定 TE 优先，但**只在接收方愿意遵守时才生效**。
一个「只看 CL」的前端配一个「只看 TE」的后端，分歧立刻产生。

## 二、CL.TE：前端信 CL，后端信 TE

```http
POST / HTTP/1.1
Host: a
Content-Length: 6
Transfer-Encoding: chunked

0

G
```

body 区共 6 字节 `0\r\n\r\nG`。

- **前端**按规则 6，取 CL=6 字节 → 认为请求到此结束，**原样转发这 6 字节**。
- **后端**按规则 3/4，看到 TE: chunked，`0` 是 last-chunk，读到 `0\r\n\r\n`（4 字节）就认为请求结束。
- 剩下的 **`G`** 留在后端连接上，成为下一个请求的前缀。

本 demo 实跑结果：`leftover == b"G"`。

## 三、TE.CL：前端信 TE，后端信 CL

```http
POST / HTTP/1.1
Host: a
Content-Length: 4
Transfer-Encoding: chunked

10
GPOST / HTTP/1.1
0

```

- **前端**按 chunked 解码：`10` 是十六进制 16，取 16 字节 `GPOST / HTTP/1.1`，再遇 last-chunk 结束。
- **后端**按 CL=4，只吃掉块长行 `10\r\n` 这 4 字节。
- 残留 `GPOST / HTTP/1.1\r\n0\r\n\r\n`（23 字节）成为走私请求前缀。

本 demo 实跑结果：`leftover == b"GPOST / HTTP/1.1\r\n0\r\n\r\n"`。

对比两种手法可见：**CL.TE 只能走私一个前缀，TE.CL 能把一整个块长行之外的内容全部走私**——
因为 chunked 的「块长行」本身正好是短小且定长的，天然适合当 CL 的诱饵。

## 四、chunked 的解析细节（RFC 9112 §7.1）

```abnf
chunked-body = *chunk last-chunk trailer-section CRLF
chunk        = chunk-size [ chunk-ext ] CRLF chunk-data CRLF
chunk-size   = 1*HEXDIG
last-chunk   = 1*("0") [ chunk-ext ] CRLF
```

- `chunk-size` 是**十六进制**，允许前导零（`0005` = 5），必须 `1*HEXDIG`（空串非法）。
- `chunk-ext` 以 `;` 起头，接收方 **MUST 忽略不认识的扩展**——所以 `5;a=b` 仍是 5 字节。
  这也意味着扩展是天然的混淆位。
- last-chunk 之后是 trailer section：`*( field-line CRLF ) CRLF`，空 trailer 就是紧跟一个空行。
- 原文特别提示：接收方必须**预备超大十六进制数**并防止整数溢出/精度丢失。

## 五、为什么「TE 不是最终编码」在请求侧必须 400

规则 4' 对请求与响应的处理是不对称的：

- 响应不是最终 chunked → 还可以「读到连接关闭」兜底（响应是最后一环）。
- 请求不是最终 chunked → **长度无法可靠确定**，必须 400 并关闭连接。

因为请求后面还有别的请求，靠「读到关闭」分帧会把后续请求一起吞掉。

## 六、防御口径

1. **中间件与后端统一**：要么全部只认 TE、并在 TE 缺失时才回退 CL；要么在入口把有歧义的请求直接 400。
2. 按规则 3 的要求，中介转发前 **MUST 先删掉收到的 `Content-Length`**，只保留 TE 语义。
3. 拒绝 `Transfer-Encoding` 出现两次、出现非法 token、或值里含 `chunked` 却不在末位。
4. 上游走 HTTP/2（自带分帧，没有 CL/TE 分歧）；下游回源仍要处理 HTTP/1.1。
5. 复用连接前先排干残留字节——但若 CL 本身不可信，残留字节也不可信，所以(1)才是根本。

## 代码结构

| 文件 | 内容 |
| --- | --- |
| `smuggle.py` | §6.3 八条判定 + §7.1.3 chunked 解码 + 两跳走私模拟器 |
| `smuggle.go` | 同模型的 Go 转写 |
| `selfcheck_smuggle.py` | Python 自检（实跑 **57** 断言） |
| `selfcheck_smuggle.go` | Go 自检（同套断言） |

`Parser(policy)` 的 `policy` 只有两种取值：`"cl"` 表示 CL 优先，`"te"` 表示 TE 优先。
`two_hop(buf, front, back)` 返回后端连接上的残留字节，即被走私的前缀。

## 参考资料（实际读过）

- RFC 9112《HTTP/1.1》— `https://www.rfc-editor.org/rfc/rfc9112.txt`（109913 B 全文实读）
  §6.3 Message Body Length、§7.1 Chunked Transfer Coding、§7.1.1 Chunk Extensions、
  §11.2 Request Smuggling
- RFC 9112 §11.1 Response Splitting（走私的姊妹问题，本 demo 未建模）
