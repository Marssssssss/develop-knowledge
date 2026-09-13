# 网络编程（系统层视角）

面向**通用**网络服务（Web 服务器、代理、数据库驱动），与游戏场景的区别见 [01-游戏开发/01-服务端/网络编程/](../01-游戏开发/01-服务端/网络编程/)。

## 子领域

- [Socket基础/](./Socket基础/) — BSD Socket API、阻塞/非阻塞、SOCKET 选项（Nagle / Delayed ACK）
- [IO多路复用/](./IO多路复用/) — `select` / `poll` / `epoll` / `kqueue` / `IOCP`
- [协议解析/](./协议解析/) — TCP 粘包、HTTP、TLV、二进制协议

## 已完成 demo

| 知识点 | 路径 | 语言 | 摘要 |
| --- | --- | --- | --- |
| Nagle 算法 vs `TCP_NODELAY` | [Socket基础/Nagle算法/](./Socket基础/Nagle算法/) | C / Python / Go | 100× 1-byte send 在 Nagle on → ~8 段 vs `TCP_NODELAY=1` → 100 段;`getsockopt(TCP_INFO).tcpi_segs_out` 实测 |
| 最小 TCP Echo Server | [Socket基础/TCP-Echo-Server/](./Socket基础/TCP-Echo-Server/) | C / Python / Go | BSD socket 标准服务端流程;socket→bind→listen→accept→recv/send 回环;SIGPIPE/EINTR/partial write 三件套规避 |
| TCP Keepalive 探测机制 | [Socket基础/TCP-Keepalive/](./Socket基础/TCP-Keepalive/) | C / Python / Go | SO_KEEPALIVE + TCP_KEEPIDLE/INTVL/CNT 四件套;默认 2h+75s×9 ≈ 2h11m → 本 demo 改 3+2×3 ≈ 9s 加速验证 dead-peer 检测;`kill -STOP` 触发 ETIMEDOUT |
| `SO_REUSEADDR` / `SO_REUSEPORT` / TIME_WAIT | [Socket基础/SO-REUSEADDR/](./Socket基础/SO-REUSEADDR/) | C / Python / Go | 自动跑 A/B/C 三组实验:无 REUSEADDR 重启 EADDRINUSE / REUSEADDR 跳过 TIME_WAIT / REUSEPORT 多 socket 同端口内核 hash 分发 |
| HTTP/1.1 请求解析器 | [协议解析/HTTP11-Parser/](./协议解析/HTTP11-Parser/) | Python + Go | RFC 7230 §3 状态机;Request-Line + Header Fields + 空行终结 + Content-Length/chunked body;header 大小写不敏感 + 同名合并 + 裸 LF 容错;mini server 实战 |
| TLV 编解码器(简化版 ASN.1 BER) | [协议解析/TLV-Codec/](./协议解析/TLV-Codec/) | Python + Go | Type 2B BE + Length 短/长格式 + Value bytes;流式解码前向兼容未知 tag 跳过;Person 业务示例展示多层结构 |

## 待研究

- [ ] TLS 握手流程(04-API设计 已先做 WebSocket 握手;TLS 与 OpenSSL/BoringSSL 在 03-系统编程 视角下补)
- [ ] 非阻塞 Socket 编程(`O_NONBLOCK` + `EAGAIN` + IO 模式)
- [ ] 紧急数据 (`MSG_OOB`) 与 SIGURG
- [ ] scatter/gather IO (`readv` / `writev` / `sendmsg`)
- [ ] TCP_CORK / TCP_QUICKACK / TCP_USER_TIMEOUT
- [ ] Unix domain socket 与文件描述符传递(SCM_RIGHTS)
- [ ] HTTP/2 二进制帧解析(HPACK 头部压缩)
- [ ] MQTT / CoAP 协议解析