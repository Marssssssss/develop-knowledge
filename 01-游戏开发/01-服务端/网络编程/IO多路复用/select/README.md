# IO 多路复用 · `select`

> 知识点：让单个线程同时监听多个文件描述符（socket），实现 C10k 级别的并发服务端基础。

## 一、简介

`select()` 是 POSIX 标准定义的**同步 IO 多路复用**系统调用：调用方传入一组 fd，内核返回"其中哪些 fd 已经就绪可读/可写"。

关键概念：

- **`fd_set`**：位图（bitmask），每位代表一个 fd 是否被关注
- **`FD_ZERO / FD_SET / FD_CLR / FD_ISSET`**：操作 fd_set 的四个宏
- **三态触发**：readfds / writefds / exceptfds
- **每次调用前必须重建 fd_set**：内核会**修改**传入的 fd_set，下次调用前要重新赋值

## 二、对比

| 模型 | 时间复杂度 | FD 上限 | 平台 | 典型场景 |
| --- | --- | --- | --- | --- |
| `select` | O(n) | 默认 1024（可改） | 全平台 | 跨平台原型 |
| `poll` | O(n) | 无硬限 | POSIX | 需要更多 fd 但又不想上 epoll |
| `epoll` | O(1) | 数十万 | Linux | Linux 生产环境首选 |
| `kqueue` | O(1) | 数十万 | BSD / macOS | macOS / FreeBSD |
| `IOCP` | O(1) | — | Windows | 高性能 Windows 服务 |

**选型建议**：`select` 适合 demo / 教学 / 小型工具；生产服务用 `epoll`（Linux）或 `kqueue`（BSD）。

## 三、环境准备

| 语言 | 依赖 | 备注 |
| --- | --- | --- |
| C | GCC 或 MSVC | Windows 需 `ws2_32.lib` |
| Python | Python ≥ 3.6 | 仅标准库 |
| Go | Go ≥ 1.18 | 跨 POSIX/Windows |

无需安装第三方包。

## 四、运行方式

### C（POSIX / Linux / macOS）

```bash
cd c
gcc -Wall -Wextra -O2 select_echo.c -o select_echo
./select_echo 9000
```

### C（Windows / MSVC）

```bat
cd c
cl /W4 /O2 select_echo.c ws2_32.lib
select_echo.exe 9000
```

### C（Windows / MinGW）

```bash
cd c
gcc -Wall -Wextra -O2 select_echo.c -o select_echo.exe -lws2_32
./select_echo.exe 9000
```

### Python

```bash
cd python
python3 select_echo.py 9000
# 启动时会打印选用的后端，如 selectors.EpollSelector / KqueueSelector / SelectSelector
```

### Go

```bash
cd go
go build -o select_echo select_echo.go
./select_echo 9000
```

### 测试客户端

任选其一：

```bash
# 1) nc（Linux/macOS 自带，Windows 10+ 自带 ncat）
nc localhost 9000
# 输入任意文本，回车后会回显

# 2) Python 小客户端
python3 python/client.py

# 3) 多个并发连接
for i in 1 2 3 4 5; do (echo "hello $i"; sleep 2) | nc localhost 9000 & done
```

## 五、关键代码片段

### C 版本核心循环

```c
rset = allset;                                  // 必须每次重建！
struct timeval tv = {1, 0};                     // 1 秒超时
int nready = select(maxfd + 1, &rset, NULL, NULL, &tv);

for (int fd = 0; fd <= maxfd; fd++) {           // O(n) 扫描
    if (!FD_ISSET(fd, &rset)) continue;
    if (fd == srv) {
        int cfd = accept(srv, ...);             // 新连接
        FD_SET(cfd, &allset);
    } else {
        int n = recv(fd, buf, sizeof buf, 0);   // 客户端数据
        if (n <= 0) close(fd);
        else send(fd, buf, n, 0);               // 回显
    }
}
```

### Python 版本核心循环

```python
events = sel.select(timeout=1.0)        # 默认 selector 自动选 epoll/kqueue/select
for key, mask in events:
    if key.data is None:
        accept(key.fileobj, mask)        # 新连接
    else:
        read(key.fileobj, mask)          # 已有客户端
```

### Go 版本核心循环

```go
fdSet := &syscall.FdSet{}
fdSet.Bits[srvFd/64] |= 1 << (uint(srvFd) % 64)   // 置位
n, _ := syscall.Select(maxFd+1, fdSet, nil, nil, tv)
```

## 六、注意事项

1. **`maxfd` 维护**：新增连接时如果 cfd > maxfd 必须更新，否则 `select()` 不会扫描到。
2. **重置 `rset`**：内核会改写 fd_set，必须每次循环开头重新赋值 `rset = allset`。
3. **fd 关闭后清理**：必须 `FD_CLR(fd, &allset)`，否则下次 `select()` 会触发 EBADF。
4. **单 fd 默认 1024**：Linux 通过 `FD_SETSIZE` 定义，编译期常量；超过会编译失败。需更多 fd 应改用 `epoll`。
5. **水平触发（LT）**：`select` 只支持水平触发，只要 fd 可读就会一直触发；高并发下可能惊群。
6. **Node.js / TypeScript 不包含**：Node.js 通过 libuv 在 Linux 上自动用 epoll，不暴露 `select()` 系统调用；强行"翻译"会误导。

## 七、参考资料

- [Linux man page: select(2)](https://man7.org/linux/man-pages/man2/select.2.html)
- [POSIX select() specification](https://pubs.opengroup.org/onlinepubs/9699919799/functions/select.html)
- [Python selectors module](https://docs.python.org/3/library/selectors.html)
- [Go syscall.Select](https://pkg.go.dev/syscall#Select)
- [C10K problem 原始论文](http://www.kegel.com/c10k.html)

## 八、相关 demo

- ⏳ `epoll/` — Linux 专属 O(1) 实现
- ⏳ `kqueue/` — BSD/macOS 专属 O(1) 实现
- ⏳ `poll/` — POSIX 通用但无 1024 上限