# 协程

## 实现模型

- **有栈协程**：ucontext / boost.coroutine，每个协程独立栈
- **无栈协程**：C++20 / Rust async，独立状态机
- **语言原生**：Go goroutine、Lua coroutine、Kotlin

## 调度

- 协作式：调用方主动 yield
- 抢占式：调度器强制切换（Go 早期 1.13+ 引入）

## 已完成 demo

- ✅ 最小有栈协程实现（C：ucontext）—— 见 [最小有栈协程/](最小有栈协程/)（C / Python / Go）
  - getcontext 快照 → uc_stack 挂栈 → uc_link 后继 → makecontext(int 参数) 四件套
  - swapcontext 双向切换；uc_link=NULL 即线程退出；POSIX.1-2008 移除史
  - 有栈 vs 无栈对照：helper 的 yield 不经 yield-from 不传导；goroutine 可增长栈

- ✅ Go 调度器 GMP 模型 —— 见 [GoGMP调度器/](GoGMP调度器/)
  - P 是并行度（= GOMAXPROCS），M 是线程且进 syscall 会把 P 交出去（handoffp）
  - 本地 `runq[256]` + `runnext`（继承时间片、旧的被踢到队尾）；满则搬 129 个到全局；每 61 tick 查一次、批量 128
  - 窃取取「靠头的一半」并先跑这批里最新的那个；sysmon 20us 起步、idle>50 翻倍、封顶 10ms
  - `forcePreemptNS=10ms`；协作式安全点只在函数调用处（紧循环为 0），异步抢占可停任意点但绕过 unsafe-point
- ✅ C++20 coroutine 状态机 —— 见 [C++20协程/](C++20协程/)
  - 无栈：状态在堆上，参数按值拷贝/按引用悬垂；`initial_suspend` 定惰性、`final_suspend` 定谁负责 destroy
  - `await_suspend` 四种返回（void / true / false / handle）；对称转移栈深恒 1，写成 `resume()` 递归则线性增长
  - `await_resume` 无论是否挂起都会调用；`await_suspend` 期间协程已完全挂起，句柄可交给别的线程

## 待研究

- [ ] Rust async/await 状态机与 Pin/!Unpin
- [ ] Lua 协程（对称 vs 非对称）