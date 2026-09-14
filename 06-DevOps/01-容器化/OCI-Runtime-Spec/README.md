# OCI Runtime Spec 与容器生命周期

> Docker 镜像 / Podman / containerd 跑的容器底层都是 **runc** + **OCI Runtime Spec**。本 demo 解析 `config.json` + 演示容器 4 状态机。

## 简介

**OCI**(Open Container Initiative)2015 年由 Docker / CoreOS 创立,统一容器 image / runtime / distribution 三大标准:
- **OCI Image Spec**:镜像层、分发格式(本项目 demo 047 已覆盖)
- **OCI Distribution Spec**:registry v2 API / 拉取去重
- **OCI Runtime Spec**:本 demo 主题,定义 config.json、bundle、状态机

**runc** 是参考实现(0.1.0 2015 年 7 月捐赠,1.0.0 2017 年 6 月),被 Docker/containerd/CRI-O/Podman 集成。每个容器运行前都需要一个 **bundle**:

```
my-bundle/
├── config.json     # OCI Runtime Spec 定义
└── rootfs/         # 解开的镜像根文件系统
```

`config.json` 是 JSON,描述:
- `ociVersion`:`"1.0.2"` / `"1.2.1"`(约定版本)
- `process`:要执行的命令、参数、环境变量、用户、rlimits、capabilities
- `root`:根 fs 路径 + readonly 标志
- `hostname`, `domainname`:UTS ns 内值
- `mounts`:额外挂载(/proc、/dev、/sys 等)
- `linux`: namespaces、resources、seccomp、apparmor、selinux、maskedPaths

参考(已读的真实来源):
- OCI [runtime-spec GitHub](https://github.com/opencontainers/runtime-spec) — 规范主仓
- DeepWiki [opencontainers/runtime-spec Configuration Examples](https://deepwiki.com/opencontainers/runtime-spec/7-configuration-examples) — 配置实用示例 + `config.json` schema
- runc 官方 [runc spec](https://github.com/opencontainers/runc) — `runc spec` 命令生成默认 config
- SUSE [Demystifying Containers Part II](https://www.suse.com/c/demystifying-containers-part-ii-container-runtimes/) — `runc` 与 Runtime Spec 实践

## 原理详解

### 容器生命周期 4 状态机(runtime-spec §"Lifecycle")

```text
            create           start
  creating ─────→ created ───────→ running
     ↑              │               │
     │ delete       │ kill          │ pause/resume
     │              │               ↓
     └──────────  stopped ←────  paused
                     │ delete
                     ↓  (destroyed)

# runc CLI 对应:create → start → kill → delete
# K8s kubelet 用 create+start 两步以同步多个容器
```

每个状态:**creating** 阻塞在 `pid=1` exec 之前;**created** 容器存在但未启动;**running** pid=1 已 exec;**stopped** 进程已退出,资源未清理。

### config.json 关键字段

```json
{
  "ociVersion": "1.2.1",
  "process": {"terminal": false, "user": {"uid": 0, "gid": 0},
              "args": ["nginx", "-g", "daemon off;"],
              "env": ["PATH=...", "NGINX_VERSION=1.25.0"], "cwd": "/",
              "rlimits": [{"type": "RLIMIT_NOFILE", "hard": 1024, "soft": 1024}],
              "noNewPrivileges": true,
              "capabilities": {"bounding": ["CAP_AUDIT_WRITE", "CAP_KILL", "..."],
                               "effective": ["CAP_AUDIT_WRITE", "CAP_KILL", "..."],
                               "permitted": ["CAP_AUDIT_WRITE", "CAP_KILL", "..."]}},
  "root": {"path": "rootfs", "readonly": false}, "hostname": "my-container",
  "mounts": [
    {"destination": "/proc", "type": "proc", "source": "proc"},
    {"destination": "/dev", "type": "tmpfs", "source": "tmpfs",
     "options": ["nosuid", "strictatime", "mode=755", "size=65536k"]}],
  "linux": {
    "namespaces": [{"type": "pid"}, {"type": "network"}, {"type": "ipc"},
                   {"type": "uts"}, {"type": "mount"}],
    "resources": {"memory": {"limit": 536870912},
                  "cpu":    {"shares": 1024, "quota": 100000, "period": 100000}},
    "seccomp": {"defaultAction": "SCMP_ACT_ERRNO",
                "architectures": ["SCMP_ARCH_X86_64"],
                "syscalls": [{"names": ["read","write","exit","exit_group"],
                              "action": "SCMP_ACT_ALLOW"}]},
    "maskedPaths":  ["/proc/kcore", "/proc/keys", "/proc/latency_stats"],
    "readonlyPaths": ["/proc/asound", "/proc/bus", "/proc/fs", "/proc/irq"]}
}
```

### Linux 必需 mounts 与被屏蔽路径

[runtime-spec](https://github.com/opencontainers/runtime-spec/blob/main/config.md#posix-platform-mounts) 默认应提供:`/proc`、tmpfs 上的 `/dev`、`/dev/pts` devpts、`/dev/shm` tmpfs、`/sys` sysfs(ro)。

避免容器进程读系统敏感信息(runc 默认 maskedPaths):
```
/proc/asound  /proc/bus  /proc/fs       /proc/irq
/proc/sys     /proc/sysrq-trigger      /proc/scsi
/proc/acpi    /proc/timer_stats        /proc/cgroups
/proc/devices /proc/mounts             /proc/swaps
```

runtime-spec 跨平台字段:**linux**(namespaces/cgroups/seccomp/apparmor/selinux)、**windows**(hyperv-isolate/layer-folder/network)、**solaris**(zone)、**freebsd**(vnet/jail)。

## 对比 / 选型

| 运行时 | 运行环境 | 性能 | 兼容性 | 典型 |
| --- | --- | --- | --- | --- |
| **runc** | 内核 namespace | 原生 | OCI 100% | Docker / containerd |
| **crun** | 内核 namespace + C 实现 | 比 runc 快 ~30% | OCI 同 | Podman / RHEL |
| **Kata Containers** | runc + microVM | 隔离↑ 性能↓ | OCI + hypervisor | K8s gVisor 替代 |
| **gVisor** | runc + 用户态 sysemu | 隔离↑ | OCI 部分 | K8s sandbox |
| **Nabla** | runc + Unikernel | 隔离↑ 性能↓ | OCI 子集 | 研究 |

## 环境准备

- Linux 3.18+ 满足 OCI 全部命名空间
- runc:`go install github.com/opencontainers/runc@latest` 或 apt 安装
- Python 3.6+ JSON 解析 + 显示
- Go 1.21+ 标准库 `encoding/json`

## 运行方式

### Python
```bash
python3 oci_demo.py spec-default      # 打印经典 OCI config.json(参考 runc spec)
python3 oci_demo.py spec-minimal      # 最小合法 config(Namespaces + mounts proc)
python3 oci_demo.py lifecycle         # 打印 4 状态机 + 转换
python3 oci_demo.py default-cap       # Docker 默认 14 capability + drop 27
python3 oci_demo.py mask              # maskedPaths + readonlyPaths 默认值
```

### C(只需 JSON 解析)
```bash
gcc -O2 -Wall oci_demo.c -o oci_demo && ./oci_demo spec-default
```

### Go
```bash
cd go && go run oci_demo.go dump         # 打印默认 config
go run oci_demo.go schema                # 打印 spec 字段树
```

## 关键代码片段

### Python 版生成的最小 config

```python
import json
cfg = {
    "ociVersion": "1.2.1",
    "process": {"user": {"uid": 0, "gid": 0}, "args": ["/bin/sh"], "env": [], "cwd": "/",
                "noNewPrivileges": True, "capabilities": {"bounding": [], "effective": [], "permitted": []}},
    "root": {"path": "rootfs"},
    "hostname": "demo",
    "mounts": [{"destination": "/proc", "type": "proc", "source": "proc"}],
    "linux": {
        "namespaces": [{"type": t} for t in ("pid","network","ipc","uts","mount")],
        "maskedPaths":  ["/proc/asound","/proc/acpi", ...],
        "readonlyPaths": ["/proc/bus", ...]
    }
}
print(json.dumps(cfg, indent=2))
```

### 容器状态机(伪代码)

```python
TRANSITIONS = {
    "creating": {"create 完成 → created"},
    "created":  {"start", "delete"},
    "running":  {"pause", "kill", "delete"},
    "stopped":  {"delete"},
    "paused":   {"resume", "kill", "delete"},
}
```

## 性能与边界

- `runc create` 一次 ~30-100ms(namespaces + cgroup + mount + capabilities);`maskedPaths` 几乎零成本(仅 bind mount)
- 4 状态设计让 K8s Pause container 成为可能:sandbox 创建后 start 注入业务
- ociVersion 后向兼容;Spec 升级通常保留旧字段

## 注意事项与常见坑

1. `config.json` 必须合法 JSON 且 schema 校验通过(runc/crun 内置验证)
2. `rootfs` 必须存在(绝对或相对 bundle 路径)
3. `readonly: true` 后容器无法写 rootfs——数据存 volume
4. `process.args[0]` = executable(IEEE 1003.1 execvp 语义,与 argv[0] 不同)
5. `linux.namespaces.path` = 加入已有 netns(非创建)
6. 推荐 `noNewPrivileges: true`(防 setuid/setgid 提权)
7. uid 0 在 user ns 内映射到外部其它 uid;容器 root ≠ host root
8. runc 1.0+ 强制 `process.args[0]` = executable

## 参考资料(实际阅读过的权威来源)

- [OCI runtime-spec GitHub](https://github.com/opencontainers/runtime-spec) — 规范主仓 + `config.md` + `config-linux.md`(全文)
- [DeepWiki Configuration Examples](https://deepwiki.com/opencontainers/runtime-spec/7-configuration-examples) — 常用配置模板 + Linux 字段
- [runc spec / runc source](https://github.com/opencontainers/runc) — `runc spec` 默认模板源码 `defaults.go`
- [SUSE Demystifying Containers – Part II](https://www.suse.com/c/demystifying-containers-part-ii-container-runtimes/) — `runc` + bundle 实际产生过程 + libcontainer 历史
