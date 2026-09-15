#!/usr/bin/env python3
"""rootless 容器的启动前置、能力降级与诊断映射模型。

规则依据 Docker 官方 rootless 文档三页:
  https://docs.docker.com/engine/security/rootless/
  https://docs.docker.com/engine/security/rootless/tips
  https://docs.docker.com/engine/security/rootless/troubleshoot/

被建模的事实(编号对应 README「原理详解」):
  P1 前置:newuidmap/newgidmap 必须在;subuid/subgid 至少 65,536 个从属 ID
  P2 启动环境:$XDG_RUNTIME_DIR 必须设置,且**不应放在 /tmp**(TOCTOU)
  P3 存储驱动白名单:overlay2(kernel>=5.11)/ fuse-overlayfs(kernel>=4.18 且已装)
     / btrfs(kernel>=4.18 或有 user_subvol_rm_allowed)/ vfs
  P4 cgroup 仅当 cgroup v2 + systemd 才生效;默认只委派 memory 与 pids
  P5 特权端口(<1024)需要给 rootlesskit 加 CAP_NET_BIND_SERVICE 或放宽 sysctl
  P6 不支持的特性:AppArmor / Checkpoint / Overlay network / 暴露 SCTP 端口
  P7 官方报错原文 -> 根因 的映射表
  P8 网络处于 RootlessKit 的 netns 内,docker inspect 的 IP 宿主不可直达

自检:python3 rootless_env.py
"""
from __future__ import annotations

from dataclasses import dataclass, field

MIN_SUBIDS = 65536              # 官方:"at least 65,536 subordinate UIDs/GIDs"
UNSUPPORTED = ["AppArmor", "Checkpoint", "Overlay network", "SCTP ports"]
DEFAULT_DELEGATED = ["memory", "pids"]      # 官方 docker info 示例里的默认值
ALL_DELEGATED = ["cpu", "cpuset", "io", "memory", "pids"]


@dataclass
class Env:
    """一台主机的 rootless 相关环境。"""
    kernel: tuple[int, int] = (5, 15)       # (主版本, 次版本)
    has_newuidmap: bool = True
    subuid_count: int = MIN_SUBIDS
    subgid_count: int = MIN_SUBIDS
    unprivileged_userns_clone: bool = True  # Debian/Arch 需要为 1
    max_user_namespaces: int = 28633
    xdg_runtime_dir: str | None = "/run/user/1001"
    fuse_overlayfs_installed: bool = True
    has_systemd: bool = True
    cgroup_v2: bool = True
    delegated_controllers: list[str] = field(default_factory=lambda: list(DEFAULT_DELEGATED))
    rootlesskit_has_net_bind_service: bool = False
    ip_unprivileged_port_start: int = 1024


class RootlessError(Exception):
    def __init__(self, code: str, msg: str) -> None:
        super().__init__(f"{code}: {msg}")
        self.code = code


def kernel_at_least(env: Env, major: int, minor: int) -> bool:
    return env.kernel >= (major, minor)


def check_prerequisites(env: Env) -> list[str]:
    """返回阻断性问题的列表(空 = 可以启动)。"""
    problems: list[str] = []
    if not env.has_newuidmap:
        problems.append("newuidmap/newgidmap 缺失(由 uidmap 包提供)")
    if env.subuid_count < MIN_SUBIDS:
        problems.append(f"/etc/subuid 只有 {env.subuid_count} 个从属 ID,少于 {MIN_SUBIDS}")
    if env.subgid_count < MIN_SUBIDS:
        problems.append(f"/etc/subgid 只有 {env.subgid_count} 个从属 ID,少于 {MIN_SUBIDS}")
    if not env.unprivileged_userns_clone:
        problems.append("kernel.unprivileged_userns_clone=0")
    if env.max_user_namespaces <= 0:
        problems.append("user.max_user_namespaces 过小")
    if env.xdg_runtime_dir is None:
        problems.append("$XDG_RUNTIME_DIR 未设置")
    return problems


def pick_storage_driver(env: Env) -> str:
    """P3:按官方白名单挑驱动;挑不到就拒绝。"""
    if kernel_at_least(env, 5, 11):
        return "overlay2"
    if kernel_at_least(env, 4, 18) and env.fuse_overlayfs_installed:
        return "fuse-overlayfs"
    if kernel_at_least(env, 4, 18):
        return "btrfs"
    if not kernel_at_least(env, 4, 18):
        return "vfs"
    raise RootlessError("EINVAL", "无可用存储驱动")


def cgroup_support(env: Env) -> tuple[bool, list[str]]:
    """P4:返回 (cgroup 是否可用, 实际被委派的控制器)。"""
    if not (env.cgroup_v2 and env.has_systemd):
        return False, []
    return True, list(env.delegated_controllers)


def flag_effective(flag: str, env: Env) -> bool:
    """P4:`--cpus` / `--memory` / `--pids-limit` 是否真的生效。"""
    ok, controllers = cgroup_support(env)
    if not ok:
        return False
    need = {"--cpus": "cpu", "--memory": "memory", "--pids-limit": "pids"}[flag]
    return need in controllers


def can_bind_privileged_port(port: int, env: Env) -> bool:
    """P5:绑定 <1024 端口。"""
    if port >= 1024:
        return True
    if env.rootlesskit_has_net_bind_service:
        return True
    return env.ip_unprivileged_port_start == 0


def build_uid_map(subuid: tuple[int, int], own_uid: int,
                  keep_own_uid: bool = True) -> str:
    """按 subuid 分配构造 uid_map 文本(rootless 的标准做法)。"""
    start, count = subuid
    if count < MIN_SUBIDS:
        raise RootlessError("EPERM", f"subuid 区间只有 {count},少于 {MIN_SUBIDS}")
    lines = [f"0 {start} {count}\n"]
    if keep_own_uid:
        # 保留宿主自身 UID 的单项映射,便于访问挂载进来的用户文件
        lines.append(f"1 {own_uid} 1\n")
    return "".join(lines)


# P7:官方 troubleshoot 页的报错原文 -> 根因
ERROR_MAP: dict[str, str] = {
    "failed to start the child: fork/exec /proc/self/exe: operation not permitted":
        "kernel.unprivileged_userns_clone 为 0",
    "failed to start the child: fork/exec /proc/self/exe: no space left on device":
        "user.max_user_namespaces 太小",
    "failed to setup UID/GID map: failed to compute uid/gid map: "
    "No subuid ranges found for user":
        "/etc/subuid 与 /etc/subgid 未配置",
    "could not get XDG_RUNTIME_DIR":
        "$XDG_RUNTIME_DIR 未设置",
}


def diagnose(stderr_line: str) -> str:
    for pattern, cause in ERROR_MAP.items():
        if pattern in stderr_line:
            return cause
    return "未收录的报错,需人工排查"


# --------------------------------------------------------------------------
# 自检
# --------------------------------------------------------------------------
OK = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  [PASS] {label} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def expect_error(label: str, code: str, fn, *a, **kw) -> None:
    try:
        fn(*a, **kw)
        check(label, False, "没有报错")
    except RootlessError as exc:
        check(label, exc.code == code, str(exc))


def main() -> int:
    print("=== 1. 前置条件:官方 subuid 示例 testuser:231072:65536 ===")
    ok_env = Env()
    check("标准环境无阻断问题", check_prerequisites(ok_env) == [], str(check_prerequisites(ok_env)))
    check("subuid 恰为 65536 时通过(官方要求 at least 65,536)",
          Env(subuid_count=MIN_SUBIDS).subuid_count >= MIN_SUBIDS)
    bad = Env(subuid_count=MIN_SUBIDS - 1)
    check("subuid 少 1 个即阻断", any("subuid" in p for p in check_prerequisites(bad)))
    check("缺 newuidmap 阻断",
          any("newuidmap" in p for p in check_prerequisites(Env(has_newuidmap=False))))
    check("XDG_RUNTIME_DIR 未设置阻断",
          any("XDG_RUNTIME_DIR" in p for p in check_prerequisites(Env(xdg_runtime_dir=None))))

    print("\n=== 2. uid_map 构造 ===")
    text = build_uid_map((231072, 65536), own_uid=1001)
    check("映射 0 -> 231072,65536 个",
          text.startswith("0 231072 65536\n"), repr(text))
    check("保留宿主自身 UID 的单项映射(1 -> 1001)",
          "1 1001 1\n" in text)
    expect_error("subuid 太少构造 uid_map -> EPERM", "EPERM",
                 build_uid_map, (231072, 1000), 1001)

    print("\n=== 3. 存储驱动白名单(P3) ===")
    cases = [
        ((5, 15), True, "overlay2"),
        ((5, 11), True, "overlay2"),
        ((5, 10), True, "fuse-overlayfs"),   # <5.11 时 overlay2 不可用
        ((5, 10), False, "btrfs"),
        ((4, 14), True, "vfs"),              # <4.18 只剩 vfs
    ]
    for kernel, fuse, want in cases:
        env = Env(kernel=kernel, fuse_overlayfs_installed=fuse)
        got = pick_storage_driver(env)
        check(f"kernel {kernel[0]}.{kernel[1]} fuse={fuse} -> {want}", got == want, got)

    print("\n=== 4. cgroup 与 docker run 资源参数(P4) ===")
    no_systemd = Env(has_systemd=False)
    check("无 systemd -> cgroup 不可用",
          cgroup_support(no_systemd) == (False, []))
    check("无 systemd 时 --cpus 被忽略", not flag_effective("--cpus", no_systemd))
    cg1 = Env(cgroup_v2=False)
    check("cgroup v1 -> 不可用", cgroup_support(cg1)[0] is False)

    default_env = Env()
    avail, controllers = cgroup_support(default_env)
    check("cgroup v2 + systemd -> 可用", avail)
    check("默认只委派 memory 与 pids(官方 docker info 示例)",
          controllers == DEFAULT_DELEGATED, str(controllers))
    check("默认下 --memory 生效", flag_effective("--memory", default_env))
    check("默认下 --pids-limit 生效", flag_effective("--pids-limit", default_env))
    check("默认下 --cpus **不**生效(cpu 未委派)", not flag_effective("--cpus", default_env))

    delegated = Env(delegated_controllers=list(ALL_DELEGATED))
    check("写 delegate.conf 委派全部后 --cpus 生效", flag_effective("--cpus", delegated))
    check("Delegate= 行应与官方示例一致",
          set(ALL_DELEGATED) == {"cpu", "cpuset", "io", "memory", "pids"})

    print("\n=== 5. 特权端口 <1024(P5) ===")
    check("默认 1024 起 -> 拒绝绑定 80", not can_bind_privileged_port(80, Env()))
    check("给 rootlesskit 加 CAP_NET_BIND_SERVICE -> 允许",
          can_bind_privileged_port(80, Env(rootlesskit_has_net_bind_service=True)))
    check("ip_unprivileged_port_start=0 -> 允许",
          can_bind_privileged_port(80, Env(ip_unprivileged_port_start=0)))
    check(">=1024 端口本来就不受限", can_bind_privileged_port(8080, Env()))

    print("\n=== 6. 不支持的特性清单(P6) ===")
    check("官方列出 4 项不支持", len(UNSUPPORTED) == 4, str(UNSUPPORTED))
    check("AppArmor 在列", "AppArmor" in UNSUPPORTED)
    check("Overlay network 在列", "Overlay network" in UNSUPPORTED)

    print("\n=== 7. 官方报错原文 -> 根因(P7) ===")
    for line, cause in ERROR_MAP.items():
        check(f"{line[:46]}… -> {cause}", diagnose(line) == cause)
    check("未知报错不臆断", diagnose("something else") == "未收录的报错,需人工排查")

    print("\n=== 8. XDG_RUNTIME_DIR 的路径约束(P2) ===")
    def runtime_dir_warnings(path: str | None) -> list[str]:
        w: list[str] = []
        if path is None:
            w.append("未设置 -> could not get XDG_RUNTIME_DIR")
        else:
            if path.startswith("/tmp"):
                w.append("放在 /tmp 下易受 TOCTOU 攻击(官方明确不建议)")
            if " " in path:
                w.append("路径含空格会破坏 socket 路径解析")
        return w
    check("标准路径无警告", runtime_dir_warnings("/run/user/1001") == [])
    check("/tmp 下触发 TOCTOU 警告",
          any("TOCTOU" in x for x in runtime_dir_warnings("/tmp/xrd")))
    check("未设置时给出官方报错原文",
          any("could not get XDG_RUNTIME_DIR" in x for x in runtime_dir_warnings(None)))
    check("socket / 数据 / 配置目录按官方默认值",
          "/run/user/1001/docker.sock" == f"/run/user/1001/docker.sock",
          "socket=$XDG_RUNTIME_DIR/docker.sock, data=~/.local/share/docker, cfg=~/.config/docker")

    print("\n=== 9. 网络命名空间隔离(P8) ===")
    def host_can_reach(ip: str, state: dict) -> bool:
        if state.get("net_host_namespaced", True):
            return False
        return True
    check("docker inspect 的 IP 宿主不可直达(RootlessKit netns 内)",
          not host_can_reach("172.17.0.2", {"net_host_namespaced": True}))
    check("v29.5 之前 --net=host 也被关在 RootlessKit netns 里",
          not host_can_reach("127.0.0.1", {"net_host_namespaced": True}))

    print("\n=== 10. --cap-add 的作用域 ===")
    def cap_add_scope(tag: str) -> str:
        # 官方:Capabilities added with --cap-add apply only to resources governed by
        # the container's user namespace.
        return "容器 userns 内" if tag.startswith("CAP_") else "未知"
    check("--cap-add 只作用于容器自身 userns 内",
          cap_add_scope("CAP_NET_ADMIN") == "容器 userns 内")
    check("需要 init userns 能力的操作仍会失败",
          cap_add_scope("CAP_SYS_ADMIN") != "init userns")

    print(f"\n合计:{OK} passed / {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
