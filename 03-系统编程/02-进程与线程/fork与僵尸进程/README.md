# fork 与僵尸进程

## 简介

`fork(2)` 通过复制调用进程创建子进程;子进程终止后若父进程迟迟不 `wait`,内核必须
保留其 PID / 退出状态 / 资源使用信息,这个"已死未收尸"的进程就是**僵尸(zombie)**。
本 demo 覆盖:fork 的 COW 复制语义、僵尸的产生与清除、退出状态的位编码、stdio
缓冲区被复制导致的输出双份、以及 `waitpid(WNOHANG)` 的三态返回。

关键概念:

- **COW(写时复制)**:fork 只复制页表与 task 结构,父子共享物理页,写入时才真正拷贝。
- **僵尸(zombie)**:子进程已终止、但父进程尚未 wait 的状态;占用内核进程表槽位。
- **退出状态编码**:wait 拿到的 `wstatus` 是位域,`WEXITSTATUS` 取子进程退出参数的低 8 位。
- **_exit(2) vs exit(3)**:前者直接陷入内核终止;后者先跑 atexit、刷 stdio 缓冲区再终止。

历史背景:fork/exec/wait 是 Unix 自 1971 年就有的原语;Linux 上 glibc 的 `fork()`
实际通过 `clone(2)` 实现(等价于 `flags=SIGCHLD`),页表按 COW 处理。

## 原理详解

### 1. fork 的复制语义

- 一次调用两次返回:**父进程拿到子进程 PID,子进程拿到 0**;失败时父进程返回 -1 且
  没有子进程被创建(`EAGAIN` 受 `RLIMIT_NPROC`、`/proc/sys/kernel/pid_max`、
  cgroup pids.max 等限制,`ENOMEM` 内存紧张)。
- 子进程是父进程的**近乎完全复制**,但有差异:PID 唯一、CPU 时间清零、挂起信号清空、
  不继承记录锁/定时器;**子进程只保留调用 fork 的那一个线程**,整个地址空间(含互斥量
  的状态)被复制,多线程程序 fork 后子进程在 `execve` 之前只能调用 async-signal-safe
  函数。
- fd 被复制且**指向同一个 open file description**:父子共享文件偏移量与状态标志
  (这正是本 demo"双份输出写到同一文件同一位置之后"的机制基础)。
- COW:fork 的代价只是复制页表 + 建 task 结构,父子写入各自内存互不影响。

### 2. 僵尸的产生、危害与清除

- 子进程终止 → 内核保留最小信息集(PID、终止状态、资源使用),等待父进程 wait;
  此时 `ps` 里状态为 `Z`。
- 僵尸**占用内核进程表槽位**;表满则**无法再创建任何新进程**。
- 清除只有一条正路:一次成功的 wait。父进程先死 → 僵尸被 `init(1)`(或最近的
  subreaper,`prctl(PR_SET_CHILD_SUBREAPER)`)收养并自动收尸。
- 例外:**SIGCHLD 显式设为 `SIG_IGN`** 或 `SA_NOCLDWAIT` → 子进程终止**不变僵尸**,
  但此后 wait/waitpid 会阻塞到所有子进程终止后以 `ECHILD` 失败。
  (Linux 2.6 起符合 POSIX;2.4 及更早不符合。)

### 3. 退出状态位编码

`waitpid` 写出的 `wstatus` 是位域(Linux 布局,与 `<sys/wait.h>` 宏展开一致):

```text
位       15    8 7      0
wstatus  [ exit code ][ sig | core | 0x80 标志 ]
正常退出:  (code & 0xff) << 8      WIFEXITED = (w & 0x7f) == 0
信号致死:  sig 本身                WIFSIGNALED = 0 < (w & 0x7f) < 0x7f
```

- `WEXITSTATUS` = 退出参数的**低 8 位**:所以 `_exit(300)` 解码出来是 **44**,`_exit(-1)`
  是 **255**——这是 wait(2) 明文规定的("least significant 8 bits")。
- 必须先用 `WIFEXITED` / `WIFSIGNALED` 判断,再取 `WEXITSTATUS` / `WTERMSIG`。

### 4. stdio 双份输出(经典面试坑)

stdout 接管道/文件时是**全缓冲**。fork 会复制用户态 stdio 缓冲区:

```text
printf("X") 未换行未刷 → fork → 父子缓冲区各含 "X"
  子进程 exit(3)   → atexit + 刷自己那份缓冲区 → 文件里第一个 "X"
  父进程 fflush    → 文件里第二个 "X"          ⇒ "XX"
  子进程 _exit(2)  → 不刷 → 只有父进程那一份    ⇒ "X"
```

所以 fork 出的子进程不打算 exec 时,应当用 `_exit` 终止。

### 5. waitpid(WNOHANG) 三态返回

| 返回值 | 含义 |
| --- | --- |
| 子进程 PID | 该子进程状态已改变(如已变僵尸),wstatus 已填充 |
| `0` | 指定了 WNOHANG 且子进程存在但**尚未改变状态**,立即返回不阻塞 |
| `-1` | 失败(如 `ECHILD`:没有可 wait 的子进程) |

`wait(&wstatus)` 等价于 `waitpid(-1, &wstatus, 0)`。

## 对比 / 选型

| 方式 | 特点 |
| --- | --- |
| `wait` | 等任意子进程,阻塞 |
| `waitpid(pid, ..., 0)` | 定向等待,阻塞 |
| `waitpid(pid, ..., WNOHANG)` | 轮询收尸,不阻塞(事件循环里常用) |
| `SIGCHLD → SIG_IGN` | 内核自动收尸,零僵尸;代价是再也拿不到退出状态 |
| `SIGCHLD 处理器内 waitpid(-1, ..., WNOHANG)` 循环 | 异步收尸标准写法 |

## 环境准备

- C:Linux + gcc(用 `clone`/`signal`/`waitpid`;Windows 无 fork,不可运行)。
- Python:3.8+(纯语义模拟,**跨平台可跑**,Windows 上即本仓库自检方式)。
- Go:1.21+(无 fork 原语,演示 `os/exec` + `Wait` 的收尸与退出码解码,Linux 下 8 位截断)。

## 运行方式

### C(Linux)

```bash
gcc -O2 -Wall -Wextra main.c -o fork_demo
./fork_demo
# 观察僵尸窗口:程序在 "zombie window" 处停留 2 秒,另开终端执行:
#   ps -o pid,ppid,stat,cmd -C fork_demo
```

### Python(跨平台语义模拟 + 断言自检)

```bash
python3 main.py
```

### Go(Linux)

```bash
go run .
```

## 关键代码片段

C——WNOHANG 轮询 + 状态解码(对应原理 §2/§5):

```c
int wstatus = 0;
pid_t r = waitpid(pid, &wstatus, WNOHANG);   /* 子进程未退出时立即返回 0 */
if (r == pid && WIFEXITED(wstatus))
    printf("exit=%d\n", WEXITSTATUS(wstatus)); /* 42 */
```

Python——位编码公式与宏一一对应(对应原理 §3):

```python
def wifexited(w):   return (w & 0x7f) == 0
def wexitstatus(w): return (w >> 8) & 0xff
# _exit(300) → wstatus = 44<<8 → WEXITSTATUS == 44(低 8 位截断)
```

Go——自 exec 子进程并解码退出码(对应原理 §3):

```go
cmd := exec.Command(self, "-child", "-code=300")
err := cmd.Run()                       // Run 内部完成 wait(收尸)
ee, _ := err.(*exec.ExitError)
fmt.Println(ee.ExitCode())             // 44,与 wait(2) 低 8 位规则一致
```

## 性能与边界

- fork 成本 ≈ 复制页表 + 创建 task 结构(COW,不拷贝数据页);`vfork(2)` 已被淘汰语义。
- 僵尸只占进程表槽,不占内存/CPU;但数量上限受 `pid_max`、`threads-max`、cgroup 限制。
- 退出码有效区间 0–255;超界静默截断(`300→44`)是 wait(2) 规则而非 bug。

## 注意事项与常见坑

- **现象**:父进程 printf 的内容输出两份 → **原因**:全缓冲 + fork 复制缓冲区 →
  **规避**:fork 前 `fflush`,子进程用 `_exit`。
- **现象**:waitpid 一直返回 -1/ECHILD → **原因**:SIGCHLD 已被设 SIG_IGN →
  **规避**:改回 SIG_DFL 或直接不 wait。
- **现象**:`WEXITSTATUS` 得到 0 但子进程明明 `_exit(256)` → **原因**:低 8 位截断 →
  **规避**:退出码只传 0–255。
- 孤儿(orphan,父先死被 init 收养)≠ 僵尸(zombie,子已死父不收)。守护进程的
  double-fork 惯用法正是靠"孤儿自动被 init 收养"来避免僵尸。
- 多线程程序 fork 后,子进程只剩一个线程:锁在被持有时被复制会导致子进程死锁,
  这也是"只能调用 async-signal-safe 函数直到 execve"的原因。

## 参考资料(实际阅读过的权威来源)

- [fork(2) - Linux manual page](https://man7.org/linux/man-pages/man2/fork.2.html) —
  复制语义/COW/单线程子进程/fd 共享 open file description/EAGAIN 限制
- [wait(2) - Linux manual page](https://man7.org/linux/man-pages/man2/wait.2.html) —
  僵尸定义与进程表占用/WEXITSTATUS 低 8 位/WNOHANG 三态/SIG_IGN→ECHLD/init 收养
