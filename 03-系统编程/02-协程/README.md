# 协程

## 实现模型

- **有栈协程**：ucontext / boost.coroutine，每个协程独立栈
- **无栈协程**：C++20 / Rust async，独立状态机
- **语言原生**：Go goroutine、Lua coroutine、Kotlin

## 调度

- 协作式：调用方主动 yield
- 抢占式：调度器强制切换（Go 早期 1.13+ 引入）

## 待研究

- [ ] 最小有栈协程实现（C）
- [ ] Go 调度器 GMP 模型
- [ ] C++20 coroutine 状态机