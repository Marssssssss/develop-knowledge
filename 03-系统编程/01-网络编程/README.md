# 网络编程（系统层视角）

面向**通用**网络服务（Web 服务器、代理、数据库驱动），与游戏场景的区别见 [01-游戏开发/01-服务端/网络编程/](../01-游戏开发/01-服务端/网络编程/)。

## 子领域

- [Socket基础/](./Socket基础/) — BSD Socket API、阻塞/非阻塞
- [IO多路复用/](./IO多路复用/) — `select` / `poll` / `epoll` / `kqueue` / `IOCP`
- [协议解析/](./协议解析/) — TCP 粘包、HTTP、TLV、二进制协议

## 待研究

- [ ] Socket 选项（SO_REUSEADDR、TCP_NODELAY）
- [ ] 非阻塞 IO 与 IO 缓冲区
- [ ] HTTP/1.1 状态机解析
- [ ] TLS 握手流程