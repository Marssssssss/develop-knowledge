# strace 与 ptrace 系统调用追踪

## 简介

`ptrace(2)` 是 Linux 提供的进程跟踪原语:tracer 进程可观察/控制 tracee 的执行并读写其内存与寄存器,man7 手册明确其主要用途就是**断点调试(gdb)与系统调用跟踪(strace)**。`strace(1)` 建立在 ptrace 之上:`strace -c` 汇总、`strace -p` 附加。本 demo 用 C 实现一个迷你 strace,并用 Python 建立 ptrace 的高开销模型。

- **ptrace-stop**:被跟踪线程在信号/系统调用边界停下,`waitpid(__WALL)` 通知 tracer。
- **syscall-enter-stop / syscall-exit-stop**:`PTRACE_SYSCALL` 让 tracee 在进入/退出系统调用处各停一次;两者**形态相同,tracer 必须自己配对**。
- **PTRACE_O_TRACESYSGOOD**:让系统调用陷阱以 `SIGTRAP|0x80` 报告,可靠区分普通 SIGTRAP(man7 推荐做法)。
- **Yama ptrace_scope**:限制 `PTRACE_MODE_ATTACH`,默认值 1 只允许祖先关系(防 GPG-agent 等被窃取内存)。

- **历史**:ptrace 起源于 Unix 的调试支持(名字 "process trace");strace 由 Paul Kranenburg 的 strace(源自 ktrace)演化而来,现由 strace 项目维护。

## 原理详解

### 追踪的启动与事件循环

```text
tracee:  ptrace(PTRACE_TRACEME) -> execve() ──┐
tracer:  waitpid(tracee)                     │ SIGTRAP
         PTRACE_SETOPTIONS(TRACESYSGOOD)     ▼
         loop: PTRACE_SYSCALL ─────► waitpid()
                ├─ WSTOPSIG == (SIGTRAP|0x80)  => syscall 边界
                │    偶数次 = enter-stop(可读参数)
                │    奇数次 = exit-stop(可读返回值)
                └─ WIFEXITED                    => 结束
```

### 关键语义(man7 ptrace(2) 要点)

1. **对象是线程不是进程**:"tracee" 始终指一个线程;多线程需逐线程附加(`PTRACE_O_TRACECLONE` 自动跟踪新线程)。
2. **enter/exit-stop 无法从 stop 本身区分**:syscall-enter-stop 之后必跟 exit-stop、PTRACE_EVENT stop 或 tracee 死亡;若 enter 后用 `PTRACE_CONT` 而非 `PTRACE_SYSCALL` 重启,则**不会**产生 exit-stop。
3. **PTRACE_ATTACH 发 SIGSTOP 但返回时 tracee 未必已停**:必须 waitpid 等到停止;更优的 `PTRACE_SEIZE`(3.4+)附加不停止、不发信号。
4. **读内存**:`PTRACE_PEEKTEXT/POKETEXT` 以"字"为单位,返回值可能合法为 -1——**调用前必须清 errno,调用后检查 errno**。
5. **读寄存器**:`PTRACE_GETREGS`(x86-64 下 `struct user_regs_struct`,syscall 号在 `orig_rax`,参数 rdi/rsi/rdx/r10/r8/r9,返回值 rax);跨架构推荐 `PTRACE_GETREGSET`。
6. **等待**:`waitpid(pid, &status, __WALL)`;`WSTOPSIG(status) == (SIGTRAP|0x80)` 即系统调用陷阱。
7. **seccomp 陷阱**:4.8+ 时 `PTRACE_EVENT_SECCOMP` stop 可能让 exit-stop 出现而没有先前 enter-stop,需防误判。

### 为什么 strace 慢

strace 用 ptrace 在每个系统调用边界"像调试器一样停下进程";perf 则在内核缓冲数据。Gregg 用 dd 实测:`strace -c` 使吞吐从 1.5GB/s 掉到 23.9MB/s(慢 62 倍),`perf stat` 只慢约 2.5 倍。开销公式:**T_traced ≈ N_syscalls × (2 次 ptrace-stop + 2 次上下文切换 + waitpid 往返)**。

## 对比 / 选型

| 工具 | 机制 | 开销 | 适用 |
| --- | --- | --- | --- |
| strace | ptrace,逐 syscall 停 | 极高(重 I/O 负载 10~100x) | 排查"调了什么" |
| perf trace | perf_events 采样 | 低得多 | 生产环境 |
| perf stat -e syscalls:* | 计数 | 最低 | 只需计数 |

## 环境准备

- Linux;x86-64(glibc 的 `sys/user.h`);C 版需要 `gcc -g`
- Python 版是纯模型,任何平台可跑

## 运行方式

### C(迷你 strace)
```bash
gcc -O2 -Wall -Wextra -g c/main.c -o ministrace
./ministrace                 # 跟踪内置负载;或 ./ministrace /bin/ls 改造后跟踪任意命令
```

### Python(开销模型 + 汇总解析)
```bash
python3 python/main.py
```

## 关键代码片段

C 版事件循环核心(对应"原理详解"流程图):

```c
for (;;) {
    ptrace(PTRACE_SYSCALL, pid, 0L, 0L);
    if (waitpid(pid, &status, __WALL) <= 0) break;
    if (WIFEXITED(status)) break;
    if (WSTOPSIG(status) == (SIGTRAP | 0x80)) {   /* TRACESYSGOOD 标记 */
        ptrace(PTRACE_GETREGS, pid, 0L, &regs);
        if (!in_syscall) {                        /* enter-stop:读 orig_rax 与参数 */
            ... 
        } else {                                   /* exit-stop:读 rax 返回值 */
            ...
        }
        in_syscall = !in_syscall;                 /* tracer 自己配对 */
    }
}
```

## 性能与边界

- 开销模型(Python 版):T = N × (2 × T_stop + 2 × T_ctxsw);以 man7/gregg 的实测边界校准——ptrace 只对系统调用多的进程造成数量级劣化。
- 单步(`PTRACE_SINGLESTEP`)每条指令都停,仅适合调试不适合剖析。
- Yama scope=3 后无法附加且写入后不可逆;容器/新 user namespace 会削弱 Yama 保护。

## 注意事项与常见坑

- **附加静止进程引发 EINTR**:`strace -p` 抑制 SIGSTOP 的副作用,`epoll_wait` 等可能返回杂散 `EINTR`(man7 BUGS)。
- **PEEK 返回 -1 歧义**:先清 errno 再判断(本 demo C 版有示范)。
- **glibc 原型是可变参数**:总是传满 4 个参数,未用置 `0L`。
- **execve 后线程 ID 重置**:多线程 tracee execve 后 TID 变化造成"死线程复活"混乱,建议 `PTRACE_O_TRACEEXEC`。
- `WNOHANG` 在 tracee 已死时仍可能返回 0——ptrace 报 ESRCH 不代表 waitpid 队列已空。

## 参考资料(实际阅读过的权威来源)

- [ptrace(2) — man7.org Linux man-pages](https://man7.org/linux/man-pages/man2/ptrace.2.html) — TRACEME/ATTACH/SEIZE、syscall-stop 语义、TRACESYSGOOD、waitpid __WALL、Yama、BUGS
- [Linux perf Examples — Brendan Gregg](https://www.brendangregg.com/perf.html) — strace vs perf 的 dd 实测开销对比(62x / 2.5x)、perf trace 替代建议
- [getitimer(2) — man7.org](https://man7.org/linux/man-pages/man2/getitimer.2.html) — 同族观测原语对照(定时采样)
