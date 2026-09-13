# Socket 基础

## 核心 API

- `socket()` / `bind()` / `listen()` / `accept()`
- `connect()` / `send()` / `recv()` / `close()`

## 阻塞 vs 非阻塞

- 阻塞：调用线程挂起直到 IO 完成（编程简单，但 C10k 不可行）
- 非阻塞：调用立即返回，需配合 IO 多路复用

## 已完成 demo

- ✅ [Nagle算法/](./Nagle算法/) — RFC 896 Nagle 算法 vs `TCP_NODELAY`;
  `getsockopt(TCP_INFO).tcpi_segs_out` 段计数对比;100 次 1 字节 send
  在 Nagle on / off 下分别产生 ~8 vs 100 段
- ✅ [TCP-Echo-Server/](./TCP-Echo-Server/) — 最小阻塞 TCP Echo 服务;socket→bind→listen→accept→recv/send 回环;SIGPIPE/EINTR/partial write 三件套规避;C/Python/Go
- ✅ [TCP-Keepalive/](./TCP-Keepalive/) — TCP Keepalive 四件套 SO_KEEPALIVE+TCP_KEEPIDLE/INTVL/CNT;默认 2h+75s×9 ≈ 2h11m → 本 demo 改 3+2×3 ≈ 9s 加速验证 dead-peer 检测
- ✅ [SO-REUSEADDR/](./SO-REUSEADDR/) — SO_REUSEADDR(跳过 TIME_WAIT 重启)/SO_REUSEPORT(Linux 3.9+ 多 socket 同端口内核 hash 分发);自动跑 A/B/C 三组实验

## 待研究

- [ ] 非阻塞 Socket 编程
- [ ] 紧急数据 (`MSG_OOB`) 与 SIGURG
- [ ] scatter/gather IO (`readv` / `writev` / `sendmsg`)
- [ ] TCP_CORK / TCP_QUICKACK / TCP_USER_TIMEOUT
- [ ] Unix domain socket 与文件描述符传递(SCM_RIGHTS)