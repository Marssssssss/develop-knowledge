# 网络编程（系统层视角）

面向**通用**网络服务（Web 服务器、代理、数据库驱动），与游戏场景的区别见 [01-游戏开发/01-服务端/网络编程/](../01-游戏开发/01-服务端/网络编程/)。

## 子领域

- [Socket基础/](./Socket基础/) — BSD Socket API、阻塞/非阻塞、SOCKET 选项（Nagle / Delayed ACK）
- [IO多路复用/](./IO多路复用/) — `select` / `poll` / `epoll` / `kqueue` / `IOCP`
- [协议解析/](./协议解析/) — TCP 粘包、HTTP、TLV、二进制协议

## 已完成 demo

| 知识点 | 路径 | 语言 | 摘要 |
| --- | --- | --- | --- |
| Nagle 算法 vs `TCP_NODELAY` | [Socket基础/Nagle算法/](./Socket基础/Nagle算法/) | C / Python / Go | 100× 1-byte send 在 Nagle on → ~8 段 vs `TCP_NODELAY=1` → 100 段；`getsockopt(TCP_INFO).tcpi_segs_out` 实测 |

## 待研究

- [ ] TLS 握手流程（04-API设计 已先做 WebSocket 握手；TLS 与 OpenSSL/BoringSSL 在 03-系统编程 视角下补）
- [ ] 协议解析中的 HTTP/1.1 状态机
- [ ] TCP keepalive 与 `SO_KEEPALIVE` 行为差异