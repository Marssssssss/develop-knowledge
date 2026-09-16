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
- ✅ fork 与僵尸进程 —— 见 [fork与僵尸进程/](fork与僵尸进程/)（C / Python / Go）
  - COW 复制语义、stdio 缓冲区复制双份输出（exit vs _exit）
  - 僵尸产生/清除、WNOHANG 三态、SIGCHLD=SIG_IGN→ECHILD、init 收养
  - wstatus 位编码（WEXITSTATUS 低 8 位截断：300→44）
- ✅ 生产者-消费者 —— 见 [生产者消费者/](生产者消费者/)（C / Python / Go）
  - mutex + 双条件变量 vs Go channel；while 谓词 vs if（虚假唤醒确定性证明）
  - 哨兵/close 广播关闭；channel happens-before 规则
- ✅ 读者-写者锁 —— 见 [读者写者锁/](读者写者锁/)（C / Python / Go）
  - 读者偏好 vs 写者偏好（POSIX implementation-defined 的策略自由度）
  - 读者重叠/写者独占不变量；tryrdlock EBUSY；递归读锁计数配对
- ✅ CFS 调度器 —— 见 [CFS调度器/](CFS调度器/)（C / Python / Go）
  - vruntime 记账（delta×NICE_0/weight）、最左选取、min_vruntime 放置
  - 权重比即 CPU 份额比；睡眠者反投机；6.6 起 EEVDF 取代说明

## 待研究

- [ ] 读写锁之外的自旋锁与信号量
- [ ] 进程间通信：管道 / 消息队列 / 共享内存
- [ ] Go GMP 调度器模型（跨 02-协程，见其待研究）
