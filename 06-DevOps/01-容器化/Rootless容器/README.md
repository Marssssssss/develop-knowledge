# rootless 容器(以非 root 用户运行守护进程与容器)

## 简介

rootless 模式让 **Docker 守护进程和容器都跑在非 root 用户下**:守护进程在安装时也不需要 root 权限(只要满足前置条件)。它和 `userns-remap` 的区别很关键 —— `userns-remap` 里**守护进程仍是 root**,而 rootless 里守护进程本身也没有 root。

关键概念:

- **user namespace 是底座**:守护进程与容器都在用户命名空间里;容器内看到的 `root` 只是该命名空间内的 root。
- **`newuidmap` / `newgidmap`**:rootless 模式下**唯一**保留的 setuid 二进制,用来把多个宿主 UID/GID 映射进用户命名空间。
- **subuid / subgid**:`/etc/subuid`、`/etc/subgid` 给该用户分配的从属 ID 区间,官方要求至少 **65,536** 个。
- **`$XDG_RUNTIME_DIR`**:socket 路径的来源;官方明确它**不应该是 `/tmp`**(TOCTOU 风险)。
- **能力降级**:存储驱动受限、cgroup 需 cgroup v2 + systemd、部分特性直接不支持 —— 这些是"能不能用"而非"好不好用"的问题。

历史背景:rootless 模式在 Docker Engine **19.03** 作为实验特性引入,**20.10** 转正。在此之前,"非 root 跑容器"只能靠 `podman`/`udocker` 这类工具。Docker 官方原文:"Rootless mode does not use binaries with SETUID bits or file capabilities, except newuidmap and newgidmap."

## 原理详解

1. **执行模型**:`dockerd-rootless.sh` 先由 **RootlessKit** 建立用户命名空间与网络命名空间,再在其中 exec `dockerd`。容器与守护进程共享这套隔离。
2. **前置条件(硬性)**:
   - `newuidmap`、`newgidmap` 必须在(多数发行版由 `uidmap` 包提供)。
   - `/etc/subuid` 与 `/etc/subgid` 里该用户至少有 **65,536** 个从属 ID/组 ID。官方示例:`testuser:231072:65536`(即区间 `[231072, 296607]`)。
   - `$XDG_RUNTIME_DIR` 必须设置,且**不应放在 `/tmp`** —— 官方说"Locating this directory under `/tmp` might be vulnerable to TOCTOU attack"。
3. **发行版差异**:Debian/Arch 上需要 `kernel.unprivileged_userns_clone=1`;CentOS 7 上需要 `user.max_user_namespaces=28633`;Ubuntu 24.04 起默认限制非特权用户命名空间,需要给 `rootlesskit` 配一份 AppArmor profile。
4. **存储驱动白名单**(官方 "Known limitations"):
   - `overlay2` —— 仅当 **kernel ≥ 5.11**(或 Ubuntu/Debian 打过补丁的内核);
   - `fuse-overlayfs` —— 仅当 **kernel ≥ 4.18** 且已安装 `fuse-overlayfs`;
   - `btrfs` —— kernel ≥ 4.18,或数据目录挂载时带 `user_subvol_rm_allowed`;
   - `vfs` —— 兜底,但性能最差。
5. **cgroup 的严苛条件**:"cgroup is supported only when running with **cgroup v2 and systemd**"。不满足时,`--cpus`、`--memory`、`--pids-limit` 这类参数会被**静默忽略**(官方:"rootless mode ignores the cgroup-related docker run flags")。
6. **默认只委派两个控制器**:官方给的 `docker info` 条件是查看
   `cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers`,典型输出是 `memory pids`。想让 `--cpus` 生效,要新建 `/etc/systemd/system/user@.service.d/delegate.conf` 写入 `Delegate=cpu cpuset io memory pids` 再 `systemctl daemon-reload`(委派 `cpuset` 需要 systemd ≥ 244)。
7. **网络在 RootlessKit 的 netns 里**:"IPAddress shown in `docker inspect` is namespaced inside RootlessKit's network namespace. This means the IP address is not reachable from the host without `nsenter`-ing into the network namespace." 端口转发默认也**不保留源 IP**。历史上(v29.5 之前)`--net=host` 同样被关在这个 netns 内。
8. **特权端口**:绑定 `<1024` 需要给 `rootlesskit` 二进制加 `cap_net_bind_service`,或把 `net.ipv4.ip_unprivileged_port_start` 设为 `0`。
9. **不支持的特性**(官方清单):**AppArmor**、**Checkpoint**、**Overlay network**、**暴露 SCTP 端口**;另外 NFS 作为 data-root 不受支持。
10. **`--cap-add` 的作用域**:官方原文"Capabilities added with `--cap-add` apply only to resources governed by the container's user namespace. They don't grant privileges over host or other global resources." —— 因此需要 **init 用户命名空间**能力的操作仍然会失败。
11. **官方报错原文可直接定位根因**(见下表),这是排查 rootless 启动失败最有效的入口。

### 官方报错 → 根因映射

| stderr 原文(节选) | 根因 |
| --- | --- |
| `failed to start the child: fork/exec /proc/self/exe: operation not permitted` | `kernel.unprivileged_userns_clone` 为 0 |
| `failed to start the child: fork/exec /proc/self/exe: no space left on device` | `user.max_user_namespaces` 太小 |
| `failed to setup UID/GID map: failed to compute uid/gid map: No subuid ranges found for user 1001` | `/etc/subuid`、`/etc/subgid` 未配置 |
| `could not get XDG_RUNTIME_DIR` | `$XDG_RUNTIME_DIR` 未设置 |

### 映射与目录布局

```
/etc/subuid: testuser:231072:65536
                ↓
uid_map:  0 231072 65536          ← 容器内 0 → 宿主 231072
          1 1001   1              ← 保留宿主自身 UID,便于访问挂载的宿主机文件

socket : $XDG_RUNTIME_DIR/docker.sock   默认 /run/user/$UID/docker.sock
data   : ~/.local/share/docker          (不可在 NFS 上)
config : ~/.config/docker               (客户端用的 ~/.docker 是另一回事)
```

## 对比 / 选型

| 维度 | rootful | userns-remap | rootless |
| --- | --- | --- | --- |
| 安装需要 root | 需要 | 需要 | **不需要** |
| 守护进程身份 | root | root | 普通用户 |
| setuid 二进制 | 无 | 无 | 仅 `newuidmap`/`newgidmap` |
| 存储驱动 | 全部 | 全部 | 白名单(见规则 4) |
| cgroup 限制 | 完整 | 完整 | 仅 cgroup v2 + systemd,且默认只委派 memory/pids |
| 特权端口 | 直接可用 | 直接可用 | 需 `CAP_NET_BIND_SERVICE` 或放宽 sysctl |
| 网络 | 支持 overlay / SCTP | 同 rootful | 不支持 overlay network 与 SCTP |
| AppArmor | 支持 | 支持 | 不支持 |

选择建议:开发机/CI 上"不给 root 也要跑容器"用 rootless;需要完整网络功能或 AppArmor 的生产节点,rootless 不合适,应改用普通 Docker + `userns-remap`,或把 rootless 与 `podman`/`containerd` 组合评估。

## 环境准备

- 操作系统:任意(纯逻辑模型,不实际启动容器)
- 语言:Python 3.10+、Go 1.21+
- 依赖:无第三方库

## 运行方式

### Python

```bash
python3 rootless_env.py
```

### Go

```bash
cd go && go run .
```

## 关键代码片段

```python
def cgroup_support(env) -> tuple[bool, list[str]]:
    """P4:cgroup 只在 cgroup v2 + systemd 下可用(官方原文)。"""
    if not (env.cgroup_v2 and env.has_systemd):
        return False, []
    return True, list(env.delegated_controllers)

def flag_effective(flag: str, env: Env) -> bool:
    """不满足条件时,--cpus/--memory/--pids-limit 会被**静默忽略**。"""
    ok, controllers = cgroup_support(env)
    if not ok:
        return False
    need = {"--cpus": "cpu", "--memory": "memory", "--pids-limit": "pids"}[flag]
    return need in controllers
```

## 性能与边界

- **存储驱动决定性能量级**:`fuse-overlayfs` 走用户态 FUSE,I/O 开销明显高于内核 `overlay2`;`vfs` 会整目录拷贝,镜像一大就非常慢。kernel ≥ 5.11 才允许在 userns 里用原生 `overlay2`。
- **cgroup 默认只委派 `memory` 与 `pids`** → `docker run --cpus 0.5` 在默认配置下**不报错也不生效**,这是最容易被误判为"容器跑满了"的坑。
- **无 cgroup 时的替代**:官方给出 `ulimit -v` 限内存、`cpulimit --limit` 限 CPU、`--ulimit nproc=` 限进程数 —— 但它们是**进程粒度**而非容器粒度,且可被容器内进程自己取消。
- **网络绕行成本**:宿主访问容器 IP 需要 `nsenter` 进 netns,端口转发默认丢源 IP。
- **端口范围**:`<1024` 需要额外授权;socket 路径长度受 `$XDG_RUNTIME_DIR` 影响(路径过长会撞古老的 UNIX socket 长度限制)。

## 注意事项与常见坑

1. **`--cpus` 无声失效**:先 `docker info` 看 `Cgroup Driver`,显示 `none` 说明条件不满足(官方原话);显示 `systemd` 也可能只是没委派 `cpu`。
2. **`subuid` 只影响新建容器**:改完 `/etc/subuid` 后必须重启守护进程/重建容器才会重新计算映射。
3. **root 也不例外**:`newuidmap` 要求调用者的宿主 UID 落在 `/etc/subuid` 内,`root` 同样需要有一条 subuid 记录。
4. **`$XDG_RUNTIME_DIR` 的坑**:用 `sudo -iu user` 切换用户会导致 `systemctl --user` 连不上 bus,官方建议用 `pam_systemd` 登录(图形终端 / `ssh @localhost` / `machinectl shell`);另外每次登出应当清理该目录。
5. **`--net=host` 不是真的宿主机网络**:它在 RootlessKit 的 netns 内;v29.5 之前一直如此。
6. **`ping` 不工作**:需要 `net.ipv4.ping_group_range` 放行(官方给出的 sysctl)。
7. **把 data-root 放 NFS**:不支持,且这不是 rootless 特有的限制。
8. **想用 `docker-in-docker`**:要用 `docker:dind-rootless` 镜像;它虽然以 UID 1000 运行,但仍需 `--privileged` 才能关闭 seccomp/AppArmor 与挂载掩码。
9. **把 `--cap-add` 当成"提权到宿主"**:不会 —— 它只在容器自己的 userns 内生效。

## 参考资料(实际阅读过的权威来源)

- [Docker Docs — Run the Docker daemon as a non-root user (Rootless mode)](https://docs.docker.com/engine/security/rootless/) — 工作原理(守护进程与容器都在 userns 内)、唯一保留的 setuid 二进制、`/etc/subuid` 至少 65,536 个从属 ID 的原文与 `testuser:231072:65536` 示例、发行版差异
- [Docker Docs — Rootless mode 限制与排障](https://docs.docker.com/engine/security/rootless/troubleshoot/) — 存储驱动白名单与内核版本门槛、**不支持特性清单**(AppArmor/Checkpoint/Overlay network/SCTP)、cgroup 需 v2+systemd 且不满足时忽略相关参数、四条官方报错原文与其根因、Ubuntu 24.04 的 AppArmor profile 要求、`--cap-add` 的作用域
- [Docker Docs — Rootless mode Tips](https://docs.docker.com/engine/security/rootless/tips) — `$XDG_RUNTIME_DIR` 的 TOCTOU 说明与 socket/data/config 默认路径、`Delegate=cpu cpuset io memory pids` 与 systemd ≥ 244、特权端口与 ping 的 sysctl、无 cgroup 时的 ulimit/cpulimit 替代方案、dind-rootless
