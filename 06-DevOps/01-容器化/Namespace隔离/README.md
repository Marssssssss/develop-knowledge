# Namespace 隔离(Linux namespaces)

> 容器三大基石之一:namespaces 负责"看不见"(资源隔离),cgroups 负责"用不多"(资源限制),联合文件系统负责"叠着放"(镜像分层)。本 demo 覆盖第一个。

## 简介

- namespace 把一个全局系统资源包装成抽象,使 namespace 内的进程看起来拥有该资源的独立实例;成员进程之间的修改互相可见,**对 namespace 外的进程不可见**(man7 namespaces(7) 原文:"Changes to the global resource are visible to other processes that are members of the namespace, but are invisible to other processes")
- 一个用途就是实现容器(同一 man 页:"One use of namespaces is to implement containers")
- 关键概念:
  - **8 类 namespace**:Mount / UTS / IPC / PID / Network / User / Cgroup / Time,每类一个 `CLONE_NEW*` 标志
  - **三个 API**:`clone(2)`(创建子进程时建新 ns)、`unshare(2)`(当前进程迁入新 ns,不建进程)、`setns(2)`(通过 `/proc/pid/ns` 的 fd 加入已有 ns)
  - **`/proc/pid/ns/`**:每个 ns 一个句柄文件,格式 `uts:[4026531838]`;bind mount 该文件可让 ns 在无进程存活时也不销毁
- 历史:Mount ns 最早(2.4.19, 2002),User ns 最晚成熟(3.8, 2013 无特权可用)

## 原理详解

### namespace 类型表(namespaces(7))

| Namespace | Flag | 隔离资源 | 引入版本 |
| --- | --- | --- | --- |
| Mount | `CLONE_NEWNS` | 挂载点 | 2.4.19 |
| UTS | `CLONE_NEWUTS` | 主机名、NIS 域名 | 2.6.19 |
| IPC | `CLONE_NEWIPC` | System V IPC、POSIX 消息队列 | 2.6.19 |
| PID | `CLONE_NEWPID` | 进程 ID | 2.6.24 |
| Network | `CLONE_NEWNET` | 网络设备、协议栈、端口 | 2.6.29 |
| User | `CLONE_NEWUSER` | 用户/组 ID | 3.8 |
| Cgroup | `CLONE_NEWCGROUP` | cgroup 根目录视图 | 4.6 |
| Time | `CLONE_NEWTIME` | boot/monotonic 时钟 | 5.6 |

### 三 API 语义

```text
clone(CLONE_NEW*)   ──创建子进程,子进程在新 ns 内(容器运行时的主流路径)
unshare(CLONE_NEW*) ──当前进程迁入新 ns;PID ns 例外:只影响"后续子进程"
setns(fd)           ──通过 /proc/pid/ns/<type> 的 fd 加入已有 ns(docker exec 路径)
```

- **除 user namespace 外**,创建新 ns 都需要 `CAP_SYS_ADMIN`
- **PID ns 是单向的**:进程可以向下进入子 PID ns,不能回到祖先 ns(pid_namespaces(7):"Changing PID namespaces is a one-way operation")
- `unshare(CLONE_NEWPID)` 后调用者自身 getpid() 不变 —— 否则"进程对自己 PID 的认知"突变会破坏应用库(pid_namespaces(7) 原文解释)

### user namespace 的无特权通道(unshare(2))

1. `unshare(CLONE_NEWUSER)` 是唯一无需 CAP_SYS_ADMIN 的 ns(要求进程单线程)
2. 调用后立即获得**新 ns 内的全套 capabilities**(仅对该 ns 及其创建的资源有效)
3. 新 ns 初始 ID 映射为空,必须写映射才能正常使用:

```bash
echo deny > /proc/self/setgroups          # 3.19 起无特权写 gid_map 前必须先 deny
echo "0 $(id -u) 1" > /proc/self/uid_map  # 格式: 新ns起始ID 父ns起始ID 长度
echo "0 $(id -g) 1" > /proc/self/gid_map
```

4. **关键推论**(unshare(2) NOTES):在同一次调用中 `CLONE_NEWUSER | CLONE_NEWUTS | ...`,即可**完全无特权**地创建其它所有 namespace —— rootless 容器(Podman rootless 模式)的基础

### PID namespace 与 procfs

- 新 PID ns 内的第一个进程 PID=1,并承担 init(1) 职责(孤儿进程收养、信号转发)
- 宿主视角每个进程仍有一个"全局 PID",ns 内视角是另一套;信号与 `/proc` 操作按**调用者所在 ns**解释
- ps(1) 要正常工作,子进程需在新 mount ns 内重挂 procfs:`mount -t proc proc /proc`

## 对比 / 选型

| 方案 | 隔离度 | 开销 | 典型用途 |
| --- | --- | --- | --- |
| namespaces | 系统资源视图隔离 | 近零(同一内核) | 容器、沙箱 |
| VM | 硬件级完整隔离 | GB 级内存 + hypervisor | 强隔离多租户 |
| chroot | 仅文件系统根 | 近零 | 遗留工具 |

## 环境准备

- OS:Linux(内核 ≥ 3.19 以支持 `setgroups=deny` 流程;demo 不依赖新特性)
- C:gcc;Python:3.8+;Go:1.21+(仅需标准库)

## 运行方式

### C
```bash
gcc -O2 -Wall -Wextra ns_demo.c -o ns_demo
sudo ./ns_demo uts      # UTS 隔离(父子视角对照)
./ns_demo user          # 无特权 user namespace + 全套 caps
sudo ./ns_demo pid      # 子进程 PID=1 + 新 procfs
```

### Python / Go
```bash
python3 ns_demo.py <uts|user|pid>   # 需要 Linux;uts/pid 需 root
go run ns_demo.go <uts|user|pid>    # 同上;observer/pid-child 为内部子命令
```

## 关键代码片段(C 版 demo_user 核心)

```c
if (unshare(CLONE_NEWUSER) < 0) die("unshare(CLONE_NEWUSER)"); // 无特权
write_map("setgroups", "deny");                     // 3.19 起强制先 deny
snprintf(map, sizeof(map), "0 %u 1\n", uid);
write_map("uid_map", map);                          // 新 ns 的 0 <- 真实 uid
write_map("gid_map", map);
/* 此后 /proc/self/status 的 CapEff 全 1,可无特权创建其它 namespace */
if (unshare(CLONE_NEWUTS) < 0) die("无特权 unshare(CLONE_NEWUTS)");
```

## 性能与边界

- ns 创建开销是 O(1) 的内核对象分配,容器启动毫秒级
- 每系统 user ns 嵌套深度有限制(4.9 起 `ENOSPC`/`EUSERS`)
- PID ns 嵌套过深同样报 `ENOSPC`(3.7 起)
- ns 句柄 inode(`/proc/pid/ns/*`)是判断"是否同一 ns"的标准方法

## 注意事项与常见坑

1. **`unshare(CLONE_NEWPID)` 不改变调用者自身 PID**,只影响之后 fork 的子进程 —— 忘了这点会让 demo 里父进程"看不到 PID=1"而误判失败
2. **写 `gid_map` 前必须先 `setgroups=deny`**(3.19+ 无特权路径),顺序反了报 EPERM;deny 后不可撤销
3. **多线程进程调 `unshare(CLONE_NEWUSER)` 直接 EINVAL**;Go 运行时自带多线程,故 Go 版 demo 在单 goroutine 下演示(创建子进程用 exec 而非 fork)
4. **在新 mount ns 挂 procfs 前先 `mount(NULL, "/", NULL, MS_REC|MS_PRIVATE, NULL)`**,否则挂载事件经共享传播泄漏到宿主(宿主 `/proc` 被覆盖是经典事故)
5. **capabilities 只在新 user ns 内有效**,对宿主文件(不在该 ns 属主下)仍无特权 —— 不要以为 CapEff 全 1 就能在宿主为所欲为
6. Go 标准库无 `sethostname`/`fork` 封装:前者用 `syscall.Syscall(SYS_SETHOSTNAME,...)`,后者用 `exec.Command`(fork+exec 语义足够演示 PID ns)

## 参考资料(实际阅读过的权威来源)

- [namespaces(7) — Linux manual page](https://man7.org/linux/man-pages/man7/namespaces.7.html) — 8 类 ns 总表、三 API 语义、`/proc/pid/ns` 句柄与 bind mount 保活(全文阅读)
- [unshare(2) — Linux manual page](https://man7.org/linux/man-pages/man2/unshare.2.html) — flags 表、EINVAL/EPERM 条件、`uid_map`/`gid_map`/`setgroups` 无特权流程、"NEWUSER 同调用免 CAP_SYS_ADMIN"推论(全文阅读)
- [pid_namespaces(7) — Linux manual page](https://www.man7.org/linux/man-pages/man7/pid_namespaces.7.html) — PID 1 语义、单向迁移、新 procfs 挂载要求(搜索快照全文阅读)
- [Michael Kerrisk, "Linux namespaces" 讲义](https://man7.org/conf/meetup/Linux-namespaces--jambit-Kerrisk-2019-05-20.pdf) — 各 ns 引入版本时间线与容器应用视角(快照阅读)
