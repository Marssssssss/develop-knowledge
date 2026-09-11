# Socket 基础

## 核心 API

- `socket()` / `bind()` / `listen()` / `accept()`
- `connect()` / `send()` / `recv()` / `close()`

## 阻塞 vs 非阻塞

- 阻塞：调用线程挂起直到 IO 完成（编程简单，但 C10k 不可行）
- 非阻塞：调用立即返回，需配合 IO 多路复用

## 已完成 demo

- ✅ [Nagle算法/](./Nagle算法/) — RFC 896 Nagle 算法 vs `TCP_NODELAY`；
  `getsockopt(TCP_INFO).tcpi_segs_out` 段计数对比；100 次 1 字节 send
  在 Nagle on / off 下分别产生 ~8 vs 100 段

## 待研究

- [ ] 最小 TCP Echo Server（C / Python）
- [ ] 非阻塞 Socket 编程
- [ ] `SO_REUSEADDR` 与 TIME_WAIT 状态
- [ ] 紧急数据 (`MSG_OOB`) 与 SIGURG
- [ ] scatter/gather IO (`readv` / `writev` / `sendmsg`)