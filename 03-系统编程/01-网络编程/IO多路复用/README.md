# IO 多路复用

> 与 [01-游戏开发/01-服务端/网络编程/IO多路复用/](../../01-游戏开发/01-服务端/网络编程/IO多路复用/) 内容互补：通用 IO 模型实现沉淀在这里。

## 对比

| 模型 | 平台 | 复杂度 | 备注 |
| --- | --- | --- | --- |
| `select` | 全平台 | O(n) | POSIX 最低标准 |
| `poll` | POSIX | O(n) | 无 1024 上限 |
| `epoll` | Linux | O(1) | 边缘触发 + 非阻塞 |
| `kqueue` | BSD/macOS | O(1) | |
| `IOCP` | Windows | O(1) | 完成端口，真正异步 |

## demo 索引

- ✅ [select](../../01-游戏开发/01-服务端/网络编程/IO多路复用/select/) — C / Python / Go
- ⏳ epoll
- ⏳ kqueue