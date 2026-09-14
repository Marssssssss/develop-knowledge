"""
OCI Runtime Spec 演示:生成 config.json + 解析 + 状态机展示

参考资料(已实际阅读,见 README):
  OCI runtime-spec GitHub:        https://github.com/opencontainers/runtime-spec
  DeepWiki Configuration Examples: https://deepwiki.com/opencontainers/runtime-spec/7-configuration-examples

用法:
  python3 oci_demo.py spec-default        # 经典默认 config(runc spec 输出)
  python3 oci_demo.py spec-minimal        # 最小合法 config(5 namespaces + proc)
  python3 oci_demo.py lifecycle           # 打印 4 状态机
  python3 oci_demo.py default-cap         # Docker 默认 cap 列表 + drop 列表
  python3 oci_demo.py mask                # maskedPaths + readonlyPaths 默认
"""

import json
import sys


# ---- 1. 默认 capability (Docker 官方 + runc 同:14 保留 / ~27 drop) ----

DOCKER_DEFAULT_CAPS = [
    "CAP_CHOWN", "CAP_DAC_OVERRIDE", "CAP_FOWNER", "CAP_FSETID", "CAP_KILL",
    "CAP_NET_BIND_SERVICE", "CAP_NET_RAW", "CAP_SETFCAP", "CAP_SETGID",
    "CAP_SETPCAP", "CAP_SETUID", "CAP_SYS_CHROOT", "CAP_MKNOD", "CAP_AUDIT_WRITE",
]
# 这是 Docker `--cap-drop=ALL --cap-add=<上述>` 的等价集合

DEFAULT_BOUNDS = DOCKER_DEFAULT_CAPS  # 两者一致即可


# ---- 2. maskedPaths / readonlyPaths (runc sources defaults.go) ----

DEFAULT_MASKED = [
    "/proc/asound", "/proc/acpi", "/proc/kcore", "/proc/keys", "/proc/latency_stats",
    "/proc/timer_list", "/proc/timer_stats", "/proc/sched_debug", "/proc/scsi",
    "/usr/lib/modules", "/usr/lib/firmware", "/proc/sysrq-trigger",
    "/sys/firmware", "/proc/devices", "/proc/mounts", "/proc/swaps",
    "/proc/cgroups", "/proc/filesystems",
]
DEFAULT_READONLY = [
    "/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys", "/proc/sysrq-trigger",
    "/proc/kcore", "/proc/keys", "/proc/latency_stats", "/proc/timer_list",
    "/proc/timer_stats", "/proc/sched_debug", "/proc/scsi",
]


# ---- 3. config.json 构造 ----

def make_config(minimal: bool = False) -> dict:
    """构造 OCI Runtime Spec config.json 字典(直接 dict,后续 to_json 即存盘)。

    minimal = True  → 仅最小字段(5 namespaces + /proc)
    minimal = False → 复刻 runc spec 默认输出 + 安全选项
    """
    cfg = {
        "ociVersion": "1.2.1",
        "process": {
            "terminal": not minimal,
            "consoleSize": {"height": 25, "width": 80} if not minimal else None,
            "user": {"uid": 0, "gid": 0},
            "args": ["/bin/sh"] if minimal else ["sh"],
            "env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                    "TERM=xterm"],
            "cwd": "/",
            "rlimits": [{"type": "RLIMIT_NOFILE", "hard": 1024, "soft": 1024}] if not minimal else None,
            "noNewPrivileges": True,
            "capabilities": {
                "bounding":   list(DEFAULT_BOUNDS) if not minimal else [],
                "effective":  list(DEFAULT_BOUNDS) if not minimal else [],
                "permitted":  list(DEFAULT_BOUNDS) if not minimal else [],
                "inheritable": [],
                "ambient":    [],
            },
            "oomScoreAdj": 0,
        },
        "root": {"path": "rootfs", "readonly": True},
        "hostname": "runc",
        "domainname": "localdomain" if minimal else None,
        "mounts": [
            {"destination": "/proc", "type": "proc", "source": "proc"},
            {"destination": "/dev", "type": "tmpfs",
             "source": "tmpfs",
             "options": ["nosuid", "strictatime", "mode=755", "size=65536k"]},
            {"destination": "/dev/pts", "type": "devpts",
             "source": "devpts",
             "options": ["nosuid", "noexec", "newinstance", "ptmxmode=0660", "mode=0620", "gid=5"]},
            {"destination": "/dev/shm", "type": "tmpfs",
             "source": "shm",
             "options": ["nosuid", "nodev", "mode=1777", "size=65536k"]},
            {"destination": "/dev/mqueue", "type": "mqueue",
             "source": "mqueue", "options": ["nosuid", "nodev"]},
            {"destination": "/sys", "type": "none",
             "source": "/sys", "options": ["rbind", "nosuid", "noexec", "nodev", "ro"]},
            {"destination": "/sys/fs/cgroup", "type": "cgroup",
             "source": "cgroup",
             "options": ["rbind", "nosuid", "noexec", "nodev", "relatime", "rw"]}
            ] if not minimal else
            [{"destination": "/proc", "type": "proc", "source": "proc"}],
        "hooks": {},
        "annotations": {"org.opencontainers.example.disclaimer": "demo"} if not minimal else None,
        "linux": {
            "uidMappings": [] if not minimal else None,
            "gidMappings": [] if not minimal else None,
            "sysctl": {} if not minimal else None,
            "resources": {
                "devices": [{"allow": False, "access": "rwm"}]
            } if not minimal else None,
            "cgroupsPath": "/container.slice",
            "namespaces": [{"type": t} for t in
                (["pid", "network", "ipc", "uts", "mount"]
                 if not minimal else
                 ["pid", "network", "ipc", "uts", "mount"])],
            "devices": [] if not minimal else None,
            "seccomp": {
                "defaultAction": "SCMP_ACT_ERRNO",
                "architectures": ["SCMP_ARCH_X86_64"],
                "syscalls": []
            } if not minimal else None,
            "rootfsPropagation": "rprivate" if not minimal else None,
            "maskedPaths": DEFAULT_MASKED if not minimal else None,
            "readonlyPaths": DEFAULT_READONLY if not minimal else None,
        },
    }
    return cfg


# ---- 4. 生命周期展示 ----

LIFECYCLE_TRANSITIONS = {
    "creating": {"create完成", "→ created"},
    "created":  {"start", "delete"},
    "running":  {"pause", "kill", "delete"},
    "paused":   {"resume", "kill", "delete"},
    "stopped":  {"delete"},
}


# ---- CLI ----

def cmd_spec_default(args):
    """打印经典默认 config.json(runc spec 输出模板)。"""
    cfg = make_config(minimal=False)
    # 移除 None 字段(模拟 runc 输出风格,JSON 不含 null)
    def strip_none(o):
        if isinstance(o, dict):
            return {k: strip_none(v) for k, v in o.items() if v is not None}
        if isinstance(o, list):
            return [strip_none(v) for v in o]
        return o
    cfg = strip_none(cfg)
    print(json.dumps(cfg, indent=2))


def cmd_spec_minimal(_args):
    """打印最小合法 config.json(5 namespaces + /proc,5 cap)。"""
    cfg = make_config(minimal=True)
    def strip_none(o):
        if isinstance(o, dict):
            return {k: strip_none(v) for k, v in o.items() if v is not None}
        if isinstance(o, list):
            return [strip_none(v) for v in o]
        return o
    cfg = strip_none(cfg)
    print(json.dumps(cfg, indent=2))


def cmd_lifecycle(_args):
    """打印 4 状态机与转换。"""
    print("# OCI 容器生命周期 (runtime-spec §Lifecycle):")
    print()
    print("            create")
    print("  creating ──────── created")
    print("     ↑                 │")
    print("     │ delete          │ start")
    print("     │                 ↓")
    print("     │              running ───── pause/resume ─── paused")
    print("     │                 │")
    print("     │                 │ kill / exit")
    print("     │                 ↓")
    print("     └────────────── stopped")
    print("                       │")
    print("                       │ delete (清理 cgroup+ns)")
    print("                       ↓")
    print("                   (destroyed)")
    print()
    print("# 状态转换(合法):")
    for st, transitions in LIFECYCLE_TRANSITIONS.items():
        print(f"  {st:10s} → {sorted(transitions)}")
    print()
    print("# runc CLI 命令等价:")
    print("  runc create --bundle <b> <id>     # → created")
    print("  runc start <id>                  # → running")
    print("  runc state <id>                  # 显示状态")
    print("  runc list                        # 列所有")
    print("  runc kill <id> SIGTERM           # → stopped")
    print("  runc delete <id>                 # → destroyed")


def cmd_default_cap(_args):
    """Docker 默认 cap 集合(14 保留)。"""
    print("# Docker / runc 默认 capability 集合(保留 14,其它 drop):")
    for c in DOCKER_DEFAULT_CAPS:
        print(f"  + {c}")
    print()
    print("# drop 集合(常见容器默认全部 drop):")
    # 一个 demo 列表:从 capabilities(7) 全表剔除上面 14
    all_caps = [
        "CHOWN", "DAC_OVERRIDE", "DAC_READ_SEARCH", "FOWNER", "FSETID", "KILL",
        "SETGID", "SETUID", "SETPCAP", "LINUX_IMMUTABLE", "NET_BIND_SERVICE",
        "NET_BROADCAST", "NET_ADMIN", "NET_RAW", "IPC_LOCK", "IPC_OWNER",
        "SYS_MODULE", "SYS_RAWIO", "SYS_CHROOT", "SYS_PTRACE", "SYS_PACCT",
        "SYS_ADMIN", "SYS_BOOT", "SYS_NICE", "SYS_RESOURCE", "SYS_TIME",
        "SYS_TTY_CONFIG", "MKNOD", "LEASE", "AUDIT_WRITE", "AUDIT_CONTROL",
        "SETFCAP", "MAC_OVERRIDE", "MAC_ADMIN", "SYSLOG", "WAKE_ALARM",
        "BLOCK_SUSPEND", "AUDIT_READ", "PERFMON", "BPF", "CHECKPOINT_RESTORE",
    ]
    drop_list = [c for c in all_caps if f"CAP_{c}" not in DOCKER_DEFAULT_CAPS]
    for c in drop_list:
        print(f"  - CAP_{c}")


def cmd_mask(_args):
    """runc 默认 maskedPaths + readonlyPaths。"""
    print("# maskedPaths (runc defaults.go 默认):")
    for p in DEFAULT_MASKED:
        print(f"  - {p}")
    print()
    print("# readonlyPaths:")
    for p in DEFAULT_READONLY:
        print(f"  - {p}")


def main():
    cmds = {
        "spec-default": cmd_spec_default,
        "spec-minimal": cmd_spec_minimal,
        "lifecycle":    cmd_lifecycle,
        "default-cap":  cmd_default_cap,
        "mask":         cmd_mask,
    }
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print("usage: oci_demo.py {spec-default|spec-minimal|lifecycle|default-cap|mask}")
        sys.exit(1)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
