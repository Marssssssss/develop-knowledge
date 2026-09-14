# ptrace 动态调试与软件断点

## 简介

`ptrace(2)` 是 Linux 上"一个进程控制另一个进程"的唯一通用入口：gdb、strace、ltrace、rr、perf 采样、frida 的 Linux 后端全部建立在它之上。本 demo 用 ~200 行 C 手写一个最小跟踪器，实现 **单步执行 + 软件断点（int3）+ 断点命中后恢复原指令并回退 RIP** 这三件事——也就是 gdb `next` / `break` 的底层。

关键概念：

- **PTRACE_TRACEME**：子进程主动声明"我将被父进程跟踪"，随后 `execve` 会先停在入口（SIGTRAP）
- **PTRACE_ATTACH / PTRACE_SEIZE**：反向操作——tracer 主动附加到已在运行的进程
- **PTRACE_PEEKTEXT / POKETEXT**：读写 tracee 内存一个字（Linux 上 text/data 无区分）
- **PTRACE_GETREGS / SETREGS**：读写通用寄存器（`struct user_regs_struct`）
- **软件断点**：把目标地址首字节换成 `0xCC`（`INT3`），命中后**恢复原字节 + RIP 减 1 + 单步 + 重新写入 0xCC**

历史背景：`ptrace` 1984 年随 Unix 进入内核，最初只为 `sdb` 调试器提供原语。它没有"设置断点"这种高层命令——断点是**用"读一个字节、写一个字节、读寄存器"拼出来的**，这正是本 demo 想展示的：调试器的复杂度在策略，不在原语。

## 原理详解

### 1. 两种附加方式

```
tracer                          tracee
  |                                |
  |                         ptrace(TRACEME)   # 声明被跟踪
  |                         execve(...)       # 停在入口，发 SIGTRAP 给父
  |<---- waitpid: WIFSTOPPED ------|
  |                                |
  |--- PTRACE_SINGLESTEP --------->|  执行 1 条指令后停止
  |<---- waitpid: SIGTRAP ---------|
```

| 操作 | 效果 | 注意 |
| --- | --- | --- |
| `PTRACE_TRACEME` | 由 **tracee 自己**调用，`pid/addr/data` 全忽略 | 之后任何信号（除 SIGKILL）都会停住并通知父 |
| `PTRACE_ATTACH` | tracer 调用，向目标**投递 SIGSTOP** | 返回时不保证已停止，必须 `waitpid` |
| `PTRACE_SEIZE` (Linux 3.4+) | tracer 调用，**不停止**目标 | `addr` 必须为 0，`data` 是选项位掩码；只有被 SEIZE 的进程才接受 `PTRACE_INTERRUPT`/`PTRACE_LISTEN` |

### 2. 等待：为什么必须用 `__WALL`

```c
pid = waitpid(pid_or_minus_1, &status, __WALL);
```

`__WALL` 隐含 `WSTOPPED|WEXITED`，能收到**所有**被跟踪子进程（含 `PTRACE_O_TRACECLONE` 自动附加的线程）的停止通知。手册明确**不建议**带 `WCONTINUED`：continued 状态按进程记账，消费它会干扰 tracee 真实父进程的语义。

### 3. 寄存器与内存原语

```c
struct user_regs_struct regs;
ptrace(PTRACE_GETREGS, pid, 0, &regs);          /* addr 忽略 */
long word = ptrace(PTRACE_PEEKTEXT, pid, addr, 0);
ptrace(PTRACE_POKETEXT, pid, addr, (void *)word);
```

**易错点**：`PTRACE_PEEKTEXT` 成功时可以返回 `-1`，与出错无法区分。手册要求：**调用前把 `errno` 清零，返回后再查 `errno`**。

### 4. 软件断点的四步生命周期

```
① 埋断点：orig = PEEKTEXT(addr)
           POKETEXT(addr, (orig & ~0xFF) | 0xCC)

② 命中：   waitpid 返回 SIGTRAP
           RIP 此时指向 0xCC **之后**（即原指令的第二个字节）

③ 修复：   POKETEXT(addr, orig)          # 恢复原字节，否则单步会再触发一次
           SETREGS(RIP = addr)            # 回退 1 字节，回到指令首
           PTRACE_SINGLESTEP              # 真正执行那条原始指令

④ 复位：   waitpid 等到单步停止
           POKETEXT(addr, ...0xCC)        # 重新埋回去，等下一次命中
```

第 ③ 步的"先恢复原字节、再单步、再重新埋回"是**必须的顺序**：如果不恢复就单步，`RIP` 指向的仍是 `0xCC`，会立刻再次陷入同一个断点，形成死循环。

### 5. 区分系统调用陷阱：`PTRACE_O_TRACESYSGOOD`

`PTRACE_SYSCALL` 会在**进入或退出**系统调用时停止。默认情况下它和普通断点一样报 SIGTRAP，无法区分。设置 `PTRACE_O_TRACESYSGOOD` 后，系统调用陷阱的信号编号**第 7 位置位**，即 `WSTOPSIG(status) == (SIGTRAP | 0x80)`。手册推荐用它而不是每次都调 `PTRACE_GETSIGINFO`——可靠且无性能开销。

### 6. 权限：为什么 attach 常常失败

- 需要 `CAP_SYS_PTRACE`；非特权进程不能跟踪它无法发信号的进程，也不能跟踪 set-uid 程序
- 1.7.27 起区分 `PTRACE_MODE_READ`（如读 `/proc/pid/environ`）与 `PTRACE_MODE_ATTACH`（attach、`process_vm_writev`）
- **Yama LSM** `/proc/sys/kernel/yama/ptrace_scope`：`0` 传统 / **`1` 受限（多数发行版默认）** / `2` 仅管理员 / `3` 完全禁止（写入后不可改）。值 `1` 下，只有**目标进程是调用者的后代**才允许 attach；目标可自行 `prctl(PR_SET_PTRACER, pid)` 放行

## 对比 / 选型

| 方案 | 粒度 | 侵入性 | 适用 |
| --- | --- | --- | --- |
| `ptrace` 软件断点 | 指令级 | 改内存（可被自校验发现） | 通用调试、strace |
| 硬件断点（DR0-DR7） | 指令级 | 不改内存 | 代码段只读 / 反调试对抗 |
| `PTRACE_SYSCALL` | 系统调用边界 | 依赖内核 | strace 类工具 |
| uprobes (eBPF) | 函数/指令级 | 内核插桩 | 生产环境可观测性 |
| Frida (inline hook) | 函数级 | 改指令序言 | 移动端 / 动态插桩 |

## 环境准备

- 操作系统：**Linux**（`ptrace` 为 Linux 特有；WSL2 可用）
- C：gcc ≥ 9；需包含 `<sys/ptrace.h>` `<sys/user.h>` `<sys/wait.h>`
- Python：3.8+（Python 版为跨平台状态机模型，**不调用 ptrace**）

## 运行方式

### C（真实 ptrace，需 Linux）

```bash
gcc -O0 -g -Wall -Wextra mini_dbg.c -o mini_dbg
./mini_dbg
```

> 编译命令刻意**不带 `-pedantic`**：把函数地址转成 `uintptr_t` 当断点地址属于 ISO C 之外的实现定义行为（`-pedantic` 会告警），但这正是所有调试器的常规做法。源码顶部另有 `#define _GNU_SOURCE`——glibc 把 `__WALL` 放在 `__USE_GNU` 保护下，不定义它会在默认 `-std=gnu17` 下编译失败。

若 attach 失败，先看 `cat /proc/sys/kernel/yama/ptrace_scope`；本 demo 用 `PTRACE_TRACEME` 路径（自己是目标的后代），在 `ptrace_scope=1` 下也可运行。

### Python（跨平台断点生命周期模型）

```bash
python3 breakpoint_lifecycle.py
```

## 关键代码片段

```c
/* mini_dbg.c —— 软件断点：埋点 -> 命中 -> 修复 -> 单步 -> 复位 */
static int set_breakpoint(pid_t pid, uintptr_t addr, uint8_t *orig) {
    errno = 0;                                   /* PEEKTEXT 可能成功返回 -1 */
    long word = ptrace(PTRACE_PEEKTEXT, pid, (void *)addr, 0);
    if (word == -1 && errno != 0) return -1;
    *orig = (uint8_t)(word & 0xFF);
    long patched = (word & ~0xFFL) | 0xCCL;      /* INT3 */
    return ptrace(PTRACE_POKETEXT, pid, (void *)addr, (void *)patched);
}

static int handle_breakpoint(pid_t pid, uintptr_t addr, uint8_t orig) {
    struct user_regs_struct regs;
    /* 1) 恢复原字节：否则单步会再次踩到 0xCC */
    long word = ptrace(PTRACE_PEEKTEXT, pid, (void *)addr, 0);
    ptrace(PTRACE_POKETEXT, pid, (void *)addr,
           (void *)((word & ~0xFFL) | (long)orig));
    /* 2) RIP 减 1 回到指令首字节 —— INT3 命中后 RIP 已指向 0xCC 之后 */
    ptrace(PTRACE_GETREGS, pid, 0, &regs);
    regs.rip = addr;
    ptrace(PTRACE_SETREGS, pid, 0, &regs);
    /* 3) 单步执行那条原始指令 */
    ptrace(PTRACE_SINGLESTEP, pid, 0, 0);
    /* 4) 复位断点 */
    ...
}
```

## 性能与边界

- `ptrace` 每次用户态↔内核态往返成本约微秒级；单步跟踪大程序会慢 3-4 个数量级
- `PTRACE_PEEKTEXT` 每次只读写**一个字**（x86-64 为 8 字节），批量读需循环或改用 `process_vm_readv`
- `PTRACE_GETREGS`/`SETREGS` **并非所有架构都存在**，可移植代码应改用 `PTRACE_GETREGSET` + `NT_PRSTATUS`
- 硬件断点最多 4 个（DR0-DR7 中 4 个地址寄存器），软件断点无数量上限

## 注意事项与常见坑

1. **attach 后以为已经停住** —— 现象：立刻 `PEEKTEXT` 读到随机值。原因：`PTRACE_ATTACH` 只是投递 SIGSTOP，返回时调度可能还没跑。规避：必须 `waitpid` 等到 `WIFSTOPPED` 再做任何操作。
2. **PEEK 返回 -1 被当成错误** —— 现象：读到的合法数据 `0xFFFFFFFFFFFFFFFF` 被判为失败。原因：成功值域与错误码重叠。规避：`errno = 0` 前置 + 后置检查。
3. **断点命中后不回退 RIP** —— 现象：崩溃在非法指令 / 从指令中间执行。原因：INT3 命中时 RIP 已越过 `0xCC`。规避：`SETREGS` 把 RIP 设回断点地址。
4. **忘记恢复原字节** —— 现象：断点在同一处无限重复触发。原因：`0xCC` 仍在内存里。规避：严格按"恢复→单步→重埋"顺序。
5. **`PTRACE_ATTACH` 在 Ubuntu/Debian 上 EPERM** —— 现象：只能跟踪自己的子进程。原因：Yama `ptrace_scope=1` 默认值。规避：用 `PTRACE_TRACEME` 路径，或 `prctl(PR_SET_PTRACER, ...)`，或临时 `echo 0 > .../ptrace_scope`（需 root）。
6. **tracee 被杀后 tracer 挂着** —— 现象：`waitpid` 返回 `ESRCH`。原因：tracee 已死。规避：把 `ESRCH` 当作正常终止处理。

## 参考资料（实际阅读过的权威来源）

- [ptrace(2) — Linux man-pages 6.19](https://man7.org/linux/man-pages/man2/ptrace.2.html) —— ATTACH vs SEIZE 对照、PEEKTEXT/POKETEXT 等价性、GETREGS/SETREGS 的 SPARC 例外、`__WALL` 与 `WCONTINUED` 建议、`PTRACE_O_TRACESYSGOOD` 的第 7 位语义、`errno` 前置清零要求、CAP_SYS_PTRACE 与 Yama `ptrace_scope` 四档表，本 README 第 1-3、5-6 节全部依据此页
- [How debuggers work: Part 1 — Basics, Eli Bendersky](https://eli.thegreenplace.net/2011/01/23/how-debuggers-work-part-1) —— 完整 tracer 骨架（`fork` + `TRACEME` + `exec` → 入口 SIGTRAP → `SINGLESTEP` 循环）、`PTRACE_GETREGS` + `PTRACE_PEEKTEXT` 读取当前指令并用 `objdump` 对拍的实操过程，本 README 第 1、3 节与"关键代码片段"的循环结构来自此文
- [Intel 64 and IA-32 SDM, Vol.2 — INT3 指令描述](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html) —— 单字节 `CC` 断点指令的语义与"保存的指令指针指向 INT3 之后"这一事实（决定了断点修复必须回退 RIP）
- 说明：`ptrace(2)` 手册页明确**未**描述 int3 断点与调试寄存器（DR0-DR7）的实现细节，这两部分属于调试器实现约定；本 demo 中硬件断点仅作对比提及，未实现。
