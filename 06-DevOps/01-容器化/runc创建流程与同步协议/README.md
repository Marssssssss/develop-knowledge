# runc 创建流程：父子进程是怎么握手的

> `runc create` 会**起两个进程**：留在宿主侧的 `runc create`（父）与 `runc init`（子，最终 execve 成容器 1 号进程）。二者靠一对 socketpair 上的 9 个同步常量完成握手。本文把这套协议做成一个可执行的模型，用来回答三个问题：**seccomp 到底在什么时候装**、**为什么容器能停在 created 状态等着 `runc start`**、**`runc init` 死在中途时父进程怎么知道**。

权威来源（本 demo 实际读过）：

- `opencontainers/runc` `libcontainer/sync.go`（5382 B）：同步常量与握手图、`syncT` 结构、`syncFlagHasFd`
- `opencontainers/runc` `libcontainer/process_linux.go`（36776 B）：`initProcess.start()` 的 `parseSync` switch
- `opencontainers/runc` `libcontainer/standard_init_linux.go`（10272 B）：`linuxStandardInit.Init()` 的完整顺序
- `opencontainers/runc` `libcontainer/rootfs_linux.go`（52735 B）：`prepareRootfs` 里的 `syncParentHooks` 与切根分支

```bash
python python/main.py              # 演示入口
python python/selfcheck_runc.py    # 58 项断言
go run go/runc_create.go go/main.go
```

## 1. 九个同步常量，四对握手

`sync.go` 注释里画得很清楚（本 demo 按原文收录）：

```
     [  child  ] <-> [   parent   ]
procMountPlease  --> [open(2) or open_tree(2) and configure mount]
                 <-- procMountFd        file: mountfd
procSeccomp      --> [forward fd to listenerPath]     （无返回同步）
procHooks        --> [run hooks]        <-- procHooksDone
procReady        --> [final setup]      <-- procRun
procSeccomp      --> [pidfd_getfd()]    <-- procSeccompDone
```

外加一个通用的 `procError`（后跟 `initError{message}` 负载）。

`syncT` 的 `File` 字段**不走 JSON**，而是紧跟在包后面用 `SCM_RIGHTS` 传 fd；能不能传由 `flags` 的 `syncFlagHasFd`（`1<<0`）位标记。`doReadSync` 见到 `procError` 就把 `arg` 还原成 `initError`——因为 `encoding/json` 没法反序列化成 `error` 接口。

`readSync`（严格版）还会拒绝非预期的 `arg` 与 `file`；`readSyncFull`（宽松版）只校验类型。类型不对时报 `unexpected synchronisation flag: got %q, expected %q`。

## 2. 子进程（runc init）的顺序

`linuxStandardInit.Init()` 的实际步骤（本 demo 逐步建模）：

1. 加入 session keyring（`_ses.<containerID>`）；
2. `setupNetwork` / `setupRoute`；
3. `prepareRootfs`：
   - 需要父进程代开的挂载 → `procMountPlease` / `procMountFd`；
   - **`syncParentHooks`** → 父进程跑 Prestart + CreateRuntime；
   - 子进程自己跑 **CreateContainer** hook（状态 `creating`）；
   - 切根：`pivot_root` / `--no-pivot` 时 `msMoveRoot` / 没有 NEWNS 时退化为 `chroot`；
   - 应用 root propagation（仅当配了且不是 `MS_PRIVATE|MS_SLAVE`，因为 `rootfsParentMountPropagation()` 已经处理过）；
4. console（`setupConsole` + `Setctty`，必须在 finalize 之前、挂载之后）；
5. `finalizeRootfs`（仅 NEWNS）、hostname、AppArmor、sysctls、readonlyPaths、maskPaths；
6. 取 `pdeath` 信号、`PR_SET_NO_NEW_PRIVS`、调度器、IO 优先级、personality、内存策略；
7. **`syncParentReady`** —— 注释明说必须在 seccomp **之前**，因为装了 seccomp 之后就不能读写 socket 了；
8. SELinux exec label；
9. seccomp（位置见下节）；
10. `finalizeNamespace`（切 uid/gid，会清掉 pdeath，所以之后要 `pdeath.Restore()`）；
11. StartContainer hook；
12. **检查 `Getppid()`**：父进程变了就 `SIGKILL` 自己（防止被 reparent 后乱跑）；
13. `exec.LookPath` —— 注释说要**在等 fifo 之前**做，这样命令不存在会作为 create 期错误返回；
14. `UnsafeCloseFrom(PassedFilesCount + 3)`，然后 `execve`。

## 3. seccomp 的两个插入点（本 demo 的核心）

同一份 seccomp 配置，插入点取决于 `NoNewPrivileges`：

| 条件 | 插入位置 | 理由（源码注释） |
| --- | --- | --- |
| 有 seccomp 且 **无** NNP | 紧跟 `procReady` 之后、`finalizeNamespace` 之前 | 无 NNP 时 seccomp 是**特权操作**，必须在丢 capabilities 之前做完 |
| 有 seccomp 且 **有** NNP | `LookPath` 之后、`close_pipe` 之前 | NNP 下不需要特权，就**尽可能晚**、紧贴 execve，让之后发生的系统调用越少越好（用户的 seccomp profile 需要放行的 syscall 也就越少） |

两次都通过 `procSeccomp` 把 fd 交给父进程：父进程用 `pidfd_getfd()` 抓过来、回 `procSeccompDone`，再把这个 fd 连同 `ContainerProcessState` 送到 `ListenerPath`。注意此时 OCI 状态的 `Status` 被**显式写成 `creating`**（`initProcessStartTime` 还没设），且 `Fds: ["seccompFd"]`。`ListenerPath` 没配就报 `seccomp listenerPath is not set`。

## 4. 父进程（runc create）的顺序

`initProcess.start()`：

1. `cmd.Start()` 起子进程 → 立刻 `closeChild()`（关掉子进程侧的 fd）；
2. `manager.Apply(pid)`——**必须在同步之前**，否则子进程可能逃出 cgroup；
3. 把 bootstrap data 写进 `initSockParent`；
4. `getChildPid()` → `getPipeFds()` → `waitForChildExit()`（等第一层子进程退出）；
5. `parseSync` 循环处理四对握手，直到 io.EOF；
6. `syncSockParent.Shutdown(SHUT_WR)`；
7. `if !seenProcReady && ierr == nil { ierr = "procReady not received" }`。

最后这条是**唯一的兜底判据**：子进程在发 `procReady` 之前就崩了（没留下任何 `procError`），父进程只能靠「没收到 procReady」来判断失败。

`procReady` 分支里有个很容易忽略的顺序：**先 `updateState` 再发 `procRun`**。源码注释解释得很细——如果先发 `procRun` 而 `runc create` 进程恰好被杀，`runc-init[2:stage]` 就会漏在进程表里，而 `runc delete` 又因为找不到 `state.json` 无法清理。

## 5. exec fifo：created 状态是怎么"停住"的

子进程在 `execve` 之前会：

1. `l.pipe.Close()` —— **关闭管道即宣告 init 完成**（父进程的 `parseSync` 读到 EOF 退出循环）；
2. 关掉 logPipe，让父进程的 `ForwardLogs` 退出；
3. 通过 `/proc/self/fd/$fd` 重新打开那个 `O_PATH` 的 fifo（Linux 允许从 `/proc` 重开 `O_PATH` fd）；
4. 往里写一个字节 `"0"` **阻塞等待父进程打开读端**。

`runc start` 做的事就是打开这个 fifo 的读端，子进程随即被唤醒并执行 `execve`。**容器之所以能停在 `created` 状态，靠的就是这个 fifo 的写阻塞**。

写 fifo 之前还要 `UnsafeCloseFrom(PassedFilesCount + 3)`：把 3 个标准 fd 加上「显式传给容器的 fd」之外的**全部** fd 关掉。这是 CVE-2024-21626 的修复——否则 execve 的 target 可能把 runc 内部的宿主 fd 当路径打开，造成容器逃逸。正因为这个操作会从 Go runtime 底下抽走文件描述符，之后**不能再做任何文件操作**。

## 6. 会话 keyring 的权限位

`getSessionRingParams()` 的取值取决于有没有 user namespace：

- 有 `NEWUSER` → 需要 **other 搜索位** `0x8`；
- 没有 → 需要 **UID 搜索位** `0x80000`；
- keyring 名恒为 `_ses.<containerID>`，`keepperms` 是 `0xffffffff`。

## 7. 与既有 demo 的分工

- `OCI-Runtime-Spec`：`config.json` 的 schema 与四状态机（**静态约定**）；
- 本 demo：父子进程怎么把这份约定跑起来（**动态流程**）；
- `pivotRoot与挂载传播`：切根那一步本身的细节，是本文第 3 步的展开。

## 8. 自检覆盖

58 项断言，分五组：基本流程与同步对（握手序列、`update_state` 先于 `procRun`、fifo 写入 `b"0"`、`UnsafeCloseFrom` 起点）、seccomp 两个插入点（顺序、只发一次、`ContainerProcessState` 的 fd 与 creating 状态、缺 ListenerPath 报错）、错误路径（`procReady not received`、`InitError` 经 `procError` 回报、类型/arg/file 三类非预期、EOF、`setns` 收到 `procHooks` 直接 panic）、hooks 与挂载（cgroup 先于 hook、Prestart 先于 CreateRuntime、CreateContainer 在切根前、`procMountFd` 带 fd 标志）、杂项（keyring 权限位、ppid 变化自杀、console/pidfd/hostname 条件分支、`--no-pivot` 与无 NEWNS 的分支）。
