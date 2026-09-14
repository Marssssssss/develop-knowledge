# Seccomp-BPF 系统调用过滤

> 容器内进程能调哪些 syscall 是白名单还是黑名单?Seccomp-BPF 用 BPF 程序让进程自定义过滤规则,容器 runtime 默认用它限制攻击面。

## 简介

Seccomp = "**SEC**ure **COMP**uting"。Linux 2.6.12(2005)引入严格模式:进程仅能调 `read`/`write`/`exit`/`sigreturn` 四 syscall。3.5(2012)扩展为 **Seccomp-BPF**:`prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &bpf_prog)`,允许附加一个 BPF(类 socket filter)程序,读 `seccomp_data` 后返回 5 种动作之一。

**Container runtime 的第二道权限闸**(第一道是 capabilities)——Docker 默认 profile 见 [Docker seccomp default profile](https://github.com/moby/moby/blob/master/profiles/seccomp/default.json),约 300 syscall 允许,~150 拒绝,~70 报错。

**基本引用(读完的实际资料)**:
- kernel.org [`userspace-api/seccomp_filter.html`](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html) — BPF filter 数据结构 + 5 种 Return values
- man7 [seccomp(2)](https://man7.org/linux/man-pages/man2/seccomp.2.html) — 6 种 flags + seccomp_data 字段
- man7 [prctl(2) PR_SET_SECCOMP](https://man7.org/linux/man-pages/man2/prctl.2.html) — 严格模式 vs BPF 模式
- Mozilla [Security/Sandbox/Seccomp](https://wiki.mozilla.org/Security/Sandbox/Seccomp) — BPF_STMT/BPF_JUMP 宏真实使用

## 原理详解

### seccomp_data(过滤器输入)

每条 syscall 进入时,内核向 BPF 程序暴露 `struct seccomp_data`:

```c
struct seccomp_data {
    int   nr;                  // 系统调用号
    __u32 arch;                // AUDIT_ARCH_* (例如 x86_64 上 0xc000003e)
    __u64 instruction_pointer; // syscall 指令地址(可选用于"warn-only"模式)
    __u64 args[6];             // 前 6 个参数
};
```

```c
// BPF 程序只允许访问 6 字段;不能 deref 指针 — 消除 TOCTOU 类攻击
```

### BPF 程序(filter)

BPF 经典版指令 `struct sock_filter`:

| 字段 | 含义 |
| --- | --- |
| `code` | 指令+操作+BPF_LD/BPF_JMP 等 16-bit |
| `jt` | true 时跳转指令数 |
| `jf` | false 时跳转指令数 |
| `k` | 多用(立即数/偏移) |

常用宏(Mozilla `BPF_STMT`/`BPF_JUMP` 顺序):

```c
#define BPF_STMT(code, k)        {(code), 0, 0, (k)}
#define BPF_JUMP(code, k, jt, jf) {(code), jt, jf, (k)}

// 加载 seccomp_data.nr 到 A
BPF_STMT(BPF_LD+BPF_W+BPF_ABS, offsetof(struct seccomp_data, nr))
// 若 nr == __NR_write 则允许;否则下一步
BPF_JUMP(BPF_JMP+BPF_JEQ+BPF_K, __NR_write, 1, 0)  // jt=1 (skip KILL)
BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_KILL_PROCESS)
BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_ALLOW)
```

### 5 种返回值(按优先级递增)

| Return | 含义 |
| --- | --- |
| `SECCOMP_RET_KILL_PROCESS`(0x80000000) | 整个进程组立即退出,exit status = SIGSYS |
| `SECCOMP_RET_KILL_THREAD`(0x00000000) | 仅当前线程退出 |
| `SECCOMP_RET_TRAP`(0x00030000) | 发 SIGSYS,si_call_addr 指向 syscall 指令 |
| `SECCOMP_RET_ERRNO`(0x00050000) | 返回自定义 errno(高 16 位) |
| `SECCOMP_RET_USER_NOTIF`(0x7fc00000,5.0+) | 给用户态发送通知 |
| `SECCOMP_RET_LOG`(0x7fc00000,4.14+) | 允许但 auditlog |
| `SECCOMP_RET_ALLOW`(0x7fff0000) | 允许 |

多个 filter 时**取最高优先级值**(KILL_PROCESS 始终优先)。

### 安装 3 条件(kernel.org)

1. 内核 ≥ 3.5(`CONFIG_SECCOMP_FILTER=y`)
2. 调用 `prctl(PR_SET_NO_NEW_PRIVS, 1)` **或** 在 ns 内有 `CAP_SYS_ADMIN`
3. 过滤器只能收紧、不能放松(避免子进程"提权")

```c
prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
struct sock_fprog prog = {.len = N, .filter = insns};
prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog);
```

## 对比 / 选型

| 方案 | 粒度 | 上下文 | 工具 |
| --- | --- | --- | --- |
| **Seccomp-BPF** | syscall 号 + 前 6 参数 | BPF 程序 | libseccomp-golang, bpf(2) |
| **ptrace** | syscall 号 + 全参数 | 用户态拦截 | strace |
| **LD_PRELOAD** | 函数级 | libc 包装 | nsswitch / glib |
| **kprobe/ftrace** | 任意内核点 | 内核模块 + uprobes | bcc |
| **eBPF(非 seccomp)** | 内核任意路径 | BPF + CO-RE | libbpf |

**Seccomp 优势**:BPF 程序不能 deref 指针,故不可能 TOCTOU;**性能**:syscall 路径上加 BPF 程序(~O(n) n 是 BPF 指令数),实测 **~50-200ns**;ptrace 慢 1000 倍。

## 环境准备

- Linux 3.5+(BPF 模式);4.14+ for `SECCOMP_RET_LOG`
- 编译需 `<linux/seccomp.h>`、`<linux/filter.h>`、`<linux/audit.h>`、`gperf` BPF 宏
- Python:`seccomp` 第三方 lib 或 `ctypes`+`syscall.SYS_prctl + SYS_seccomp`
- Go:`github.com/seccomp/libseccomp-golang`

## 运行方式

### C(真 prctl,需 3.5+ 内核)
```bash
gcc -O2 -Wall seccomp_demo.c -o seccomp_demo
./seccomp_demo whitelist        # 只允许 read/write/exit/exit_group → 跑 echo 测试
./seccomp_demo block-rmmod      # 删特定 syscall → 测试
```

### Python(无 root,可编码 BPF 程序不安装)
```bash
python3 seccomp_demo.py generate whitelist "read,write,exit,exit_group" > filter.bpf
# 仅显示 BPF 字节码 + 安装伪演示
python3 seccomp_demo.py toolchain-bin2c filter.bpf
# 转换 BPF 程序到 C 数组
```

### Go(真 syscall,需 prctl)
```bash
cd go && go run seccomp_demo.go dump        # 输出 go-style BPF 数组
go run seccomp_demo.go arch-check            # 验证当前 arch 值
```

## 关键代码片段

### C 版过滤器(`seccomp_demo.c`)

```c
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <linux/audit.h>

static int allowed[] = {__NR_read, __NR_write, __NR_exit, __NR_exit_group, __NR_sigreturn};

static struct sock_filter prog[] = {
    /* Load arch */
    BPF_STMT(BPF_LD+BPF_W+BPF_ABS, offsetof(struct seccomp_data, arch)),
    BPF_JUMP(BPF_JMP+BPF_JEQ+BPF_K, AUDIT_ARCH_X86_64, 1, 0),
    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_KILL_PROCESS),
    /* Load syscall nr */
    BPF_STMT(BPF_LD+BPF_W+BPF_ABS, offsetof(struct seccomp_data, nr)),
    /* Linear search */
#define ALLOW(i) BPF_JUMP(BPF_JMP+BPF_JEQ+BPF_K, allowed[i], 0, 1),
#define KILL    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_KILL_PROCESS)
    ALLOW(0) ALLOW(1) ALLOW(2) ALLOW(3) ALLOW(4)
    KILL,                                       // 不在白名单 → KILL
    BPF_STMT(BPF_RET+BPF_K, SECCOMP_RET_ALLOW) // 匹配 → ALLOW
};
```

### Python 版 BPF 程序生成

```python
# 编码 BPF 指令为字节序列(C 结构体顺序)
import struct
def emit(code, jt=0, jf=0, k=0):
    return struct.pack("<HBBi", code & 0xffff, jt, jf, k)

# usage 与上面 C 版等价
```

## 性能与边界

- 单条 syscall 检查开销 = **单次 BPF 解释循环**:Chrome 实测约 50-200 ns,Chromium 用 dispatcher 编译为 O(log n) **BST** 派发
- 受限进程 syscall 性能损耗:~5-10% 视 syscall 频度
- `SECCOMP_MODE_STRICT`(2.6.12 老模式)与 BPF 模式**互斥**;升级前需 exit
- 多 filter 时,新 filter 进 `TSYNC` 等所有线程同步(MODE_TSYNC flag)或失败
- 5.0+ `SECCOMP_FILTER_FLAG_NEW_LISTENER`:filter 返回 USER_NOTIF 时给指定 fd 发通知(用 seccomp_unotify(2) 处理)

## 注意事项与常见坑

1. **装 filter 前必须先 `PR_SET_NO_NEW_PRIVS=1`**(否则 EPERM)
2. **filter 不能放松**:KILL_PROCESS 比 ALLOW 优先级高,所以用 ERRNO 替代 KILL 给路径"调到再处理"
3. **多线程安装**:若其他线程已装 seccomp,新装需要 `SECCOMP_FILTER_FLAG_TSYNC`,否则回 EINVAL
4. **arch 校验**:AMD64 系统可能跑 i386 二进制,需同时检查 `arch == AUDIT_ARCH_I386` 等
5. **syscall 号随 arch 变**:`__NR_write` 在 x86_64 = 1, i386 = 4, aarch64 = 64;同一 BPF 程序跨平台需重 encode
6. **SECCOMP_RET_TRACE**:已被早期 kernel 废弃,改用 ptrace
7. **Chrome / Chromium**:用 `seccomp-bpf` 实现 4-沙箱(content-process sandbox);CONFIG_SECCOMP_FILTER=y 必须

## 参考资料(实际阅读过的权威来源)

- [kernel.org `userspace-api/seccomp_filter.html`](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html) — 5 Return values + struct seccomp_data + 装配流程(全文)
- [man7 seccomp(2)](https://man7.org/linux/man-pages/man2/seccomp.2.html) — 6 flags + 操作编号、SYS_SECCOMP / SECCOMP_SET_MODE_FILTER
- [man7 prctl(2) PR_SET_SECCOMP / PR_SET_NO_NEW_PRIVS](https://man7.org/linux/man-pages/man2/prctl.2.html) — 严格模式 vs BPF 模式、epice filesystem 限制
- [Mozilla Security/Sandbox/Seccomp](https://wiki.mozilla.org/Security/Sandbox/Seccomp) — BPF_STMT/BPF_JUMP 宏实际用法 + chromium ErrorCode → BPF 编译(BST)
- [Chrome Sandbox Internals docs](https://chromium.googlesource.com/chromium/src/+/master/docs/security/sandbox.md) — 4-沙箱设计 seccomp-BPF 实践
