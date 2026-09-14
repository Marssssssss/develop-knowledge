# Linux Capabilities 权限分割

> 把"root 全能"拆成 ~40 个最小权限单元,容器运行时按需给进程授予,**最小特权原则**的核心工具。

## 简介

Linux 2.2 之前只有"特权进程 vs 非特权进程"二态。2.2 起把 root 的特权切成 **capability**,每个 capability 是一个独立的权限单元。Container runtime 启动容器时,默认给一组 capability(如 Docker 默认 14 个),其余 drop 掉——这是容器与宿主"权限隔离"的第二道闸。

参考 [man7 capabilities(7)](https://man7.org/linux/man-pages/man7/capabilities.7.html) 原文:
> "Linux divides the privileges traditionally associated with superuser into distinct units, known as capabilities, which can be independently enabled and disabled."

应用场景:
- **容器运行时**(Docker/runc):`--cap-add` / `--cap-drop`
- **systemd 单元**:`CapabilityBoundingSet=`、`AmbientCapabilities=`
- **可执行文件**:文件 cap(扩展属性,无需 root)+ `setcap cap_net_bind_service=+ep /path/bin`

## 原理详解

### 五集合(thread-scoped)

每个线程有 5 个 capability 集合(man7 capabilities(7) §"The five capability sets for a thread"),从 `/proc/self/status` 读 `CapInh`/`CapPrm`/`CapEff`/`CapBnd`/`CapAmb` 5 个 64-bit 十六进制:

| 集合 | 含义 | 典型用法 |
| --- | --- | --- |
| **Permitted** | 进程可以使用的 cap 上限 | 一旦 drop,不可再加入(除非 execve 文件 cap) |
| **Effective** | 当前生效集合 | 内核检查这里 |
| **Inheritable** | execve 时携带的 cap | 与 file inheritable 配合 |
| **Bounding** | execve 后可获得的 cap 上限 | 通常 = 全集,drop 不可恢复 |
| **Ambient** | execve 后保留为 Effective 的 cap(5.8+ 引入) | Python `/usr/bin/python3` + file caps 不会污染子进程 |

`execve` 公式(man7 capabilities(7) §"Transforming capabilities during execve"):
```
P'(ambient)     = (file is privileged) ? 0 : P(ambient)
P'(permitted)   = (P(inheritable) & F(inheritable)) | (F(permitted) & P(bounding)) | P(ambient)
P'(effective)   = F(effective) ? P'(permitted) : P'(ambient)
P'(inheritable) = P(inheritable)
```

### 41 个 capability(man7 capabilities(7) §"Capabilities list")

| Cap | 内核版本 | 允许操作 |
| --- | --- | --- |
| CAP_CHOWN | 2.2 | 修改文件 UID/GID |
| CAP_DAC_OVERRIDE | 2.2 | 绕过文件读/写/执行权限 |
| CAP_NET_BIND_SERVICE | 2.2 | 绑定端口 < 1024 |
| CAP_NET_RAW | 2.2 | RAW/PACKET socket |
| CAP_NET_ADMIN | 2.2 | 接口配置/路由/iptables |
| CAP_SYS_ADMIN | 2.2 | **"万能"**:mount / ns / cgroup / swapon / sethostname |
| CAP_SYS_PTRACE | 2.2 | 跟踪任意进程 |
| CAP_SYS_MODULE | 2.2 | 加载内核模块 |
| CAP_SETUID | 2.2 | 修改 UID |
| CAP_SETPCAP | 2.6.25 | 修改 Bounding set |
| CAP_BPF | 5.8 | 加载 BPF 程序(从 CAP_SYS_ADMIN 拆出) |
| CAP_PERFMON | 5.8 | perf_event_open(从 CAP_SYS_ADMIN 拆出) |
| CAP_CHECKPOINT_RESTORE | 5.9 | PID ns + clone3 flags(从 CAP_SYS_ADMIN 拆出) |
| ... | | 共 ~41 个,见 `cap_last_cap` |

> "`CAP_SYS_ADMIN` 是上帝能力"——几乎包含 mount / 改名 / ns 创建 / cgroup 配置,`docker run --privileged` 即授予全集。

### 三类 API

| API | 用途 |
| --- | --- |
| `capset()` | 设置线程 cap(2.6.26+ 同时支持 5 个集合) |
| `prctl(PR_CAPBSET_*)` | 增/删 Bounding 集合 cap(永久性) |
| 文件 cap(`setcap` / `getcap`) | 给可执行文件附加 cap,通过 `xattr security.capability` 存储 |

`setcap` 标志语法:
- `+ep`:增加 effective + permitted
- `+ei`:增加 effective + inheritable
- `-all`:清除所有
- `cap_net_bind_service+eip`:三个集合都加

## 对比 / 选型

| 方案 | 安全性 | 使用复杂度 | 适用 |
| --- | --- | --- | --- |
| capabilities | 中(per-cap) | 中 | 单二进制服务(nginx / db) |
| setuid root | 低(等价 root) | 低 | 旧 CGI 脚本 |
| SELinux / AppArmor | 高(MAC + cap) | 高 | RHEL/Fedora 严苛隔离 |
| 容器(默认) | 中(默认 14 cap) | 低 | runc 默认策略 |

Docker **默认 drop 27 个**,只给 `CAP_CHOWN`/`DAC_OVERRIDE`/`FOWNER`/`FSETID`/`KILL`/`NET_BIND_SERVICE`/`NET_RAW`/`SETFCAP`/`SETGID`/`SETPCAP`/`SETUID`/`SYS_CHROOT`/`MKNOD`/`AUDIT_WRITE`,即 `--cap-drop=ALL --cap-add=...`。

## 环境准备

- OS:Linux 2.2+;cap BBP / PERFMON 需 5.8+;Ambient 需 4.3+
- C:gcc + libcap-dev(可选,本 demo 直接走 syscall);Python:3.6+;Go:1.21+
- 演示进程需 root(或 user namespace)修改自身 cap

## 运行方式

### Python(无 root,只读解析)
```bash
python3 cap_demo.py inspect
# 打印本进程 5 个集合的十六进制与解码的能力名
python3 cap_demo.py demo bit-manipulation
# 演示位运算,与可读 cap 列表互转
python3 cap_demo.py file-cap /path/to/file
# 读取文件 security.capability xattr 并解码(需 xattr lib;非 root 返回 ENODATA)
```

### C(真 capset,需 root 或 user namespace)
```bash
gcc -O2 -Wall -Wextra cap_demo.c -o cap_demo && ./cap_demo self-inspect
# 第一次试:root 跑 demo 丢弃 CAP_NET_RAW,再尝试 ping(应失败)
sudo ./cap_demo drop-net-raw && ./cap_demo ping-demo
```

### Go(无 root, syscall 直调)
```bash
cd go && go run cap_demo.go inspect
```

## 关键代码片段

### C 版 syscall 直调(`cap_demo.c`)

```c
#include <sys/syscall.h>
struct __user_cap_header_struct h = {.version = _LINUX_CAPABILITY_VERSION_3,
                                     .pid = 0 /* 0 = self */};
struct __user_cap_data_struct d[2];   // v3:2 元素,每元素 32 bit
syscall(SYS_capget, &h, d);
// d[0] = lower 32 caps, d[1] = upper 32 caps (CAP_LAST_CAP=39)
printf("CapEff: %#010x %#010x\n", d[0].effective, d[1].effective);
```

### Python 版解码

```python
# Read /proc/self/status Cap* 5 个 64-bit hex
def read_cap(pid: int) -> dict[str, int]:
    out = {}
    with open(f"/proc/{pid}/status") as f:
        for ln in f:
            if ln.startswith(("CapInh:", "CapPrm:", "CapEff:", "CapBnd:", "CapAmb:")):
                k, v = ln.split()
                out[k.rstrip(":")] = int(v, 16)
    return out

# 位 → 名(Linux 5.8+ cap_last_cap=39,需双 uint64)
def decode_cap(mask: int, names: dict[int, str]) -> list[str]:
    return [names[i] for i in range(64) if (mask >> i) & 1]
```

### capdrop 与 execve(capability(7) §"Capability-diminishing operations")

```c
// prctl(PR_CAPBSET_DROP, cap_bit) 永久从 Bounding 移除
if (prctl(PR_CAPBSET_DROP, CAP_NET_RAW, 0, 0, 0) < 0) die("bsdrop");
// 然后 cap_set_proc 把该 cap 从 P/E/I 全删
capset(... cap &= ~CAP_NET_RAW ...);
```

## 性能与边界

- cap 操作都是 O(1) 的位赋值;`capget`/`capset` 单次开销 ~100 ns
- v3 format 用两 `__user_cap_data_struct` 覆盖 64 cap(Linux 2.6.25+);v1 仅 64 cap
- `CAP_LAST_CAP` 5.8 后到 39(BPF/PERFMON 拆出),5.9 后 41(CHECKPOINT_RESTORE)
- 文件 cap `xattr security.capability` 仅 ext4 / btrfs / xfs / tmpfs / f2fs 支持;NFS/FAT 不支持
- `no_new_privs`(`prctl(PR_SET_NO_NEW_PRIVS, 1)`)永久禁止 execve 提权;seccomp-BPF 安装的前置

## 注意事项与常见坑

1. **drop 后不可逆**(Permitted):忘记就重启进程
2. **文件 cap 与 no_new_privs 互斥**:`setcap` 后 `prctl(PR_SET_NO_NEW_PRIVS, 1)` 安装 seccomp 会拦截
3. **Python 等解释器文件 cap 极危险**:这把 cap 握给每个运行脚本——必须给专用单二进制
4. **Bounding vs Permitted 关系**:CapPrm ⊆ CapBnd;后者永久
5. **容器内的 cap 只在该容器 ns 内有效**:对宿主文件无效(man7 user_namespaces(7))
6. **linux 内核 5.8+** 用全 Bounding set;早版本实测有效 cap 数 = 31(cap_last_cap=30)
7. **macOS 没有 Linux capability**:BSD 用 sandbox / trustedbsd 不同模型

## 参考资料(实际阅读过的权威来源)

- [man7 capabilities(7)](https://man7.org/linux/man-pages/man7/capabilities.7.html) — 41 个 cap 总表、5 集合语义、`execve` 转换公式(全文阅读)
- [man7 prctl(2) PR_CAPBSET_DROP/PR_CAP_AMBIENT](https://man7.org/linux/man-pages/man2/prctl.2.html) — Bounding 操作、Ambient 设置、5.8/5.9 引入版本
- [man7 setcap(8) / getcap(8) / capsh(1)](https://man7.org/linux/man-pages/man8/setcap.8.html) — libcap 工具集、capsh `--decode` 用法
- [Linux Containers and Capabilities — runbook.academy](https://runbook.academy/courses/linux/lessons/linux-traditional-root-model) — 4 cap 子集完整示例 + 文件 cap + no_new_privs
- [Docker runtime capabilities 官方文档](https://docs.docker.com/engine/reference/run/#runtime-privilege-and-linux-capabilities) — `--cap-add/--cap-drop` 默认策略(14 保留 / 27 drop)
