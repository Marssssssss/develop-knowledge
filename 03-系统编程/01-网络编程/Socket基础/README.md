# Socket 基础

## 核心 API

- `socket()` / `bind()` / `listen()` / `accept()`
- `connect()` / `send()` / `recv()` / `close()`

## 阻塞 vs 非阻塞

- 阻塞：调用线程挂起直到 IO 完成（编程简单，但 C10k 不可行）
- 非阻塞：调用立即返回，需配合 IO 多路复用

## 待研究

- [ ] 最小 TCP Echo Server（C / Python）
- [ ] 非阻塞 Socket 编程
- [ ] `SO_REUSEADDR` 的作用