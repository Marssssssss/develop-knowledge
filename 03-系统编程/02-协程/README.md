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

## 待研究

- [ ] Go 调度器 GMP 模型
- [ ] C++20 coroutine 状态机