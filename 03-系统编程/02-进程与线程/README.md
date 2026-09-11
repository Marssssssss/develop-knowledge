# 进程与线程

## 进程

- `fork()` / `exec()`
- 进程间通信：管道、消息队列、共享内存、信号、socket

## 线程

- `pthread_create()`（POSIX）
- 同步原语：互斥锁、条件变量、读写锁、自旋锁、信号量

## 已完成 demo

- ✅ 哲学家就餐问题 —— 见 [哲学家就餐/](哲学家就餐/)（C / Python / Go）
  - Naive 高竞争复现死锁
  - Resource Hierarchy 资源分级破除循环等待
  - Tanenbaum 监视器方案（1 mutex + N condvar + state[]）

## 待研究

- [ ] 生产者-消费者最小实现（C / Go）
- [ ] 读者-写者问题
- [ ] 协程 vs 线程对比
- [ ] Linux CFS 调度器基础