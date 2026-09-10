# IO 多路复用

让单个线程同时监听多个文件描述符（socket）是否有事件就绪，避免「一连接一线程」的资源消耗。

## 实现对比

| 系统调用 | 平台 | 时间复杂度 | 关键限制 |
| --- | --- | --- | --- |
| `select` | 全平台（POSIX） | O(n) | 单进程默认 FD 上限 1024；每次调用需重传 fd_set |
| `poll` | POSIX | O(n) | 无 1024 上限，但仍需遍历全部 fd |
| `epoll` | Linux | O(1) | 仅 Linux；水平/边沿触发两种模式 |
| `kqueue` | macOS / BSD | O(1) | 仅 BSD 系 |
| `IOCP` | Windows | O(1) | 异步 IO，模型与上面完全不同 |

## demo 索引

- ✅ [select/](./select/) — C / Python / Go 三语言最小实现
- ✅ [epoll/](./epoll/) — C / Python / Go 三语言（LT 模式 + ET 语义详解，依据 man7 手册）

## 选型建议

- 跨平台原型 → `select`（最小公分母）
- Linux 生产环境 → `epoll`（边缘触发 + 非阻塞 IO）
- macOS/BSD → `kqueue`
- 高性能 Windows 服务器 → `IOCP`