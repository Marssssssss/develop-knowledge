# 最小有栈协程:ucontext 用户态上下文切换

## 简介

**有栈协程(stackful coroutine)**给每个协程配一块独立栈,切换即换栈+换寄存器——
与内核线程切换同构,只是全程在用户态、由代码**主动协作**触发。本 demo 用
System V 的 `ucontext` 家族(`getcontext` / `makecontext` / `swapcontext`)搭一个
最小协作式调度器,并与无栈协程(Python generator)对照;Go 的 goroutine 则展示
"有栈+可增长"的现代形态。

关键概念:

- **ucontext_t**:保存恢复点所需的一切(程序计数器、寄存器、信号掩码、栈)。
- **swapcontext(oucp, ucp)**:把当前上下文存进 `oucp`,激活 `ucp`——像"能回来
  的 goto",再次被切回时"看起来"返回 0。
- **uc_stack / uc_link**:前者挂独立栈;后者是协程函数 return 后的**后继上下文**。
- **有栈 vs 无栈**:能否从任意调用深度挂起。

## 原理详解

### 1. 三个系统调用(System V ABI 语义,man7 makecontext(3))

```text
getcontext(ucp)      把当前执行点快照进 ucp(先取快照,再改造)
makecontext(ucp,     在快照基础上"改写":func + argc 个 int 参数 + uc_stack
          func, argc, ...)   改完后 ucp 成为"待激活的协程入口"
swapcontext(oucp,ucp) 原子地:快照当前点进 oucp → 跳去激活 ucp
```

准备工作四件套(man 手册 EXAMPLE 的骨架):

1. `getcontext(&ctx)` 取快照;
2. `ctx.uc_stack.ss_sp/ss_size` 挂一块自备栈;
3. `ctx.uc_link = &main_ctx` 函数 return 后回主上下文(NULL 则线程退出);
4. `makecontext(&ctx, func, argc, ...)` 绑定入口。

### 2. 参数传递的硬限制

`makecontext` 的变参**只能是 int**:man 手册明文"passed the series of integer
(int) arguments"。指针只有在 int 与指针等宽的机器上"碰巧"可行(x86-64 上
glibc 2.8 起对部分 64 位架构开了口子),**按标准是未定义行为**——跨机器传结构
体的正规做法是全局变量或队列索引。

### 3. 协作式调度器(round-robin)

```text
main: swapcontext(&main_ctx, &co[0])   ── 激活 0 号协程
co[0]: 打印 → yield()                  ── swapcontext(&co[0], &co[1])
co[1]: 打印 → yield()                  ── swapcontext(&co[1], &co[2])
co[2]: 打印 → return                    ── uc_link 链回 main_ctx
main: 继续 / 激活下一轮
```

切换成本是**纯用户态**的几十条指令,无系统调用、无调度器介入——这是协程比
线程便宜数量级的根源;代价是任何一个协程不让出,整条执行流全部卡死。

### 4. 为什么被移出 POSIX.1-2008

man 手册 HISTORY:**SUSv2 / POSIX.1-2001 有 → POSIX.1-2008 移除**,理由是
可移植性问题,官方建议改用 POSIX 线程。但 glibc 一直保留实现,C 语言至今仍靠
它实现有栈协程(经典如 libtask 系谱);`ucontext_t` 里还存了信号掩码,切换语义
与 `sigprocmask` 联动,是 fcontext/asm 方案要显式甩掉的包袱。

### 5. 有栈 vs 无栈:挂起点的自由度

| 维度 | 有栈(ucontext/goroutine) | 无栈(generator/async) |
| --- | --- | --- |
| 挂起点 | **任意调用深度**(换栈即换世界) | 仅词法上写在 yield/await 处 |
| 每协程成本 | 一块独立栈(KB 级) | 一个状态机对象(百字节级) |
| 典型代表 | ucontext、goroutine、Lua | Python generator、C++20、Rust async |

Python 侧的对照实验:`def helper(): yield` 之后 `helper` 自己成了 generator,
外层 generator 调它**不会把挂起点传导上来**(得到的是 generator 对象而不是值)
——无栈协程"挂起点必须在词法上可见"的直接证据。

## 环境准备

- C:Linux + glibc(默认带 ucontext)+ gcc。栈大小 64 KiB(演示级;深递归会爆)。
- Python:3.8+,仅标准库(Windows 可跑,本仓库自检方式)。
- Go:1.21+,仅标准库。

## 运行方式

```bash
gcc -O2 -Wall -Wextra main.c -o ucontext_demo && ./ucontext_demo
python3 main.py
go run .
```

## 关键代码片段

C——yield:把"回来点"存进自己,激活下一个(原理 §3):

```c
static void yield_to(int from, int to)
{
    printf("co%d: switch to co%d\n", from, to);
    swapcontext(&co[from], &co[to]);   /* 回来时从这里"返回 0" */
}
```

Python——协程函数 return 后激活 uc_link(原理 §1 的状态机化):

```python
def _activate(self, ucp):
    ...
    if ctx.func_done:               # 函数 return 了
        nxt = ctx.uc_link           # 后继上下文;None 等价线程退出
        if nxt is None:
            raise SystemExit(0)
```

## 性能与边界

- swapcontext 是用户态直接切换:无系统调用、无内核调度,单次成本远低于线程切换;
  但每次要整组保存/恢复寄存器 + 信号掩码(`sigprocmask` 系统调用部分是 glibc
  实现的主要开销之一)。
- 栈是**固定大小**:64 KiB 演示栈跑不了深递归;分配太小 → 栈溢出直接段错误,
  没有"可增长栈"兜底(Go 运行时的分段/连续栈增长正是为此)。
- 一个进程内 ucontext 切换不占用内核资源,数量可以远超线程。

## 注意事项与常见坑

- **现象**:makecontext 后协程跑飞/段错误 → **原因**:忘记先 `getcontext`
  快照、或 uc_stack 悬空 → **规避**:严格四件套顺序。
- **现象**:协程 return 后程序崩溃 → **原因**:`uc_link = NULL` 时函数返回即
  线程退出(对主线程就是整个进程)→ **规避**:演示一律链回 main_ctx。
- **现象**:传结构体指针后 64 位机上偶发错乱 → **原因**:makecontext 只保证
  int 参数,指针传递是 UB(glibc 扩展)→ **规避**:传索引,结构体放全局。
- **现象**:Python 的 helper 里写 yield 没生效 → **原因**:helper 自己变成
  generator,挂起不会传导到调用方(无栈的词法限制)→ **规避**:层层 yield
  委托(Python 3.3+ `yield from`)或改用有栈方案。

## 参考资料(实际阅读过的权威来源)

- [makecontext(3) - Linux manual page](https://man7.org/linux/man-pages/man3/makecontext.3.html) —
  四件套流程、int-only 参数、uc_link 后继、UC_STACK 语义、POSIX.1-2008 移除
  与 glibc 历史、完整可运行示例
- [The Go Memory Model](https://go.dev/ref/mem) — go 语句 synchronized-before
  goroutine 启动(Go 版观察 goroutine 有栈可增长行为时的同步保证)
