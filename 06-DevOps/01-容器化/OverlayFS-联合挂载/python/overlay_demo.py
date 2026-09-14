"""
OverlayFS 联合挂载 demo(无 root 解析 + 有 root mount 演示)

参考资料(实际读过的权威来源,见 README 引用列表):
  kernel.org overlayfs.rst: https://sources.debian.org/src/linux/6.9.7-1/Documentation/filesystems/overlayfs.rst/
  kernel.org filesystems/overlayfs.html: https://www.kernel.org/doc/html/v5.8/filesystems/overlayfs.html

本 demo:
  inspect    - 解析 /proc/self/mounts,展示系统里现有 overlay 挂载的拆解
  demo-layers- 模拟 Docker 多层 lower 栈的数据结构、merge 查找顺序、whiteout 处理
  mount-demo - 实际 mount overlay(需要 root;失败时给出 EPERM 友好提示)
"""

import ctypes
import os
import stat
import sys
from pathlib import Path


# ---------- 1. /proc/mounts 解析 ----------

def parse_overlay_opts(opts: str) -> dict:
    """解析 mount(8) 返回的 overlay 选项字符串。
    例: 'lowerdir=/l1:/l2,upperdir=/u,workdir=/w' → 拆分三段。

    关键事实:lowerdir 用 ":" 分隔多层时,遵循 `man overlayfs.rst` 描述,
    "rightmost" 是最底层(最先构建的层)。这里原样保存顺序,演示用。
    """
    out = {"lowerdir": [], "upperdir": None, "workdir": None, "metacopy": False, "redirect_dir": None}
    for kv in opts.split(","):
        k, _, v = kv.partition("=")
        if k == "lowerdir":
            out["lowerdir"] = v.split(":")
        elif k == "upperdir":
            out["upperdir"] = v
        elif k == "workdir":
            out["workdir"] = v
        elif k == "metacopy":
            out["metacopy"] = (v == "on")
        elif k == "redirect_dir":
            out["redirect_dir"] = v
    return out


def list_overlay_mounts() -> list:
    """读取 /proc/self/mounts 找 type=overlay 的挂载,返回 [{mp, src, opts}]。
    Docker/containerd/Podman 的容器 rootfs 通常在这里。
    """
    overlays = []
    # /proc/self/mounts 仅 Linux 有;非 Linux 平台返回空(走 sim 层栈演示)
    if not sys.platform.startswith("linux"):
        return overlays
    with open("/proc/self/mounts") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 6:
                continue
            mp, fstype, opts = parts[1], parts[2], parts[3]
            if fstype == "overlay":
                overlays.append({
                    "mount_point": mp,
                    "device": parts[0],
                    "options": opts,
                    "parsed": parse_overlay_opts(opts),
                })
    return overlays


# ---------- 2. 层栈与合并查找逻辑(无 fs,纯数据演示) ----------

class OverlayStack:
    """OverlayFS 层栈的数据结构。

    遵循 overlayfs.rst 的"栈"语义:
      - lowerdirs: List[Path],index 0 = 顶层(应用层),index -1 = 最底(base image)
      - upperdir : Path 可写 layer (容器运行时)
      - workdir  : 内核原子重命名暂存(与 upper 同 fs)
    """

    def __init__(self, lowerdirs, upperdir=None, workdir=None):
        # lowerdirs 顺序:左 = 上层(后构建),右 = 下层(base)
        self.lowerdirs = [Path(p) for p in lowerdirs]
        self.upperdir = Path(upperdir) if upperdir else None
        self.workdir = Path(workdir) if workdir else None

    def lookup(self, relpath: str) -> str:
        """kernel `ovl_lookup` 语义模拟:先 upper(含 whiteout),后 lower 从上到下。"""
        # upper 存在 → 命中(含 whiteout 拦截)
        if self.upperdir:
            p = self.upperdir / relpath
            if p.exists():
                return f"upper:{relpath}"
        # lower 逐层找,Docker 等价于右到左 build 顺序
        # 演示里 lowerdirs[0] = 顶层,所以先查 [0] 再 [-1] 倒序
        for i, lower in enumerate(self.lowerdirs):
            p = lower / relpath
            if p.exists():
                return f"lower[{i}]:{relpath}"
        return "MISS"

    def describe(self) -> str:
        lines = ["# OverlayFS Stack(top→bottom):"]
        for i, l in enumerate(self.lowerdirs):
            lines.append(f"  lower[{i}] (idx={i}) = {l}")
        if self.upperdir:
            lines.append(f"  upper = {self.upperdir}  (RW)")
        if self.workdir:
            lines.append(f"  work  = {self.workdir}  (atomic rename)")
        return "\n".join(lines)


# ---------- 3. whiteout 创建(实际写文件系统) ----------

def create_whiteout(dirpath: str, name: str) -> None:
    """模拟内核创建 whiteout:设备号 0/0 字符设备。
    overlayfs.rst "whiteouts and opaque directories":
      'A whiteout is created as a character device with 0/0 device number'
    这样 lower 的同名文件被遮挡,与 `rm /merged/foo` 等价。

    仅 Linux 有 os.mknod(Windows 上 mknod 不存在,降级为创建 0 字节文件并提示)
    """
    p = Path(dirpath) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "mknod"):
        os.mknod(p, mode=0o000 | stat.S_IFCHR, device=os.makedev(0, 0))  # noqa
    else:
        # 非 POSIX(Linux 才有真 mknod);在内核语义中 whiteout 由内核创建白out
        # 这里仅作演示记录,真实 overlay 仍要 Linux
        p.write_bytes(b"")


# ---------- 4. 实际 mount(需要 root) ----------

CLONE_NEWNS = 0x00020000
MS_REC = 16384
MS_PRIVATE = 1 << 18


def mount_overlay(lowerdir, upperdir, workdir, merged) -> int:
    """用 ctypes 调 libc mount(2)。需要 root 或 user namespace + CAP_SYS_ADMIN。
    失败时常见 errno: EPERM(无权限)、EINVAL(work 与 upper 不同 fs)、ENODEV(无 overlay 模块)。
    """
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    opts = f"lowerdir={lowerdir},upperdir={upperdir},workdir={workdir}".encode()
    src = b"overlay"
    target = merged.encode()
    fstype = b"overlay"
    data = opts
    ret = libc.mount(src, target, fstype, 0, data)
    if ret != 0:
        return ctypes.get_errno()
    return 0


# ---------- 5. CLI ----------

def cmd_inspect(_args):
    """打印当前进程可见的 overlay 挂载(系统里有 Docker/Podman 才有输出)。"""
    overlays = list_overlay_mounts()
    if not overlays:
        print("当前进程 namespace 内未发现 overlay 挂载。")
        print("(若运行了 Docker 容器,通常可在主机 /proc/1/mounts 看到)")
        return
    for i, ov in enumerate(overlays):
        print(f"=== overlay mount #{i} @ {ov['mount_point']} ===")
        print(f"  device : {ov['device']}")
        print(f"  options: {ov['options']}")
        p = ov["parsed"]
        print(f"  lower  ({len(p['lowerdir'])} layers): {p['lowerdir']}")
        print(f"  upper  : {p['upperdir']}")
        print(f"  work   : {p['workdir']}")
        print(f"  metacopy={p['metacopy']}, redirect_dir={p['redirect_dir']}")


def cmd_demo_layers(_args):
    """演示层栈查找顺序与 whiteout 屏蔽。"""
    # Linux 用 /tmp,Windows 退到 ./tmp(实际 mount 仍需 Linux)
    base_root = "/tmp" if sys.platform.startswith("linux") else "."
    base = Path(base_root) / "ovl_demo_layers"
    if base.exists():
        import shutil; shutil.rmtree(base)
    base.mkdir(parents=True, exist_ok=True)
    # lower[0] = app 层(top), lower[1] = base image(bottom)
    layers = [
        base / "lower_app",    # 上层:应用
        base / "lower_base",   # 下层:基础
    ]
    upper = base / "upper"
    work = base / "work"
    for d in [*layers, upper, work]:
        d.mkdir()

    # 写入文件
    (layers[0] / "app.conf").write_text("APP=1")
    (layers[0] / "log.txt").write_text("from app")
    (layers[1] / "release").write_text("debian 12")
    (layers[1] / "log.txt").write_text("from base")

    stack = OverlayStack(
        lowerdirs=[str(layers[0]), str(layers[1])],
        upperdir=str(upper), workdir=str(work))

    print(stack.describe())
    print()
    print("# 合并查找(模拟内核 ovl_lookup):")
    print(f"  /app.conf   → {stack.lookup('app.conf')}     # app 层独有")
    print(f"  /release    → {stack.lookup('release')}      # base 独有")
    print(f"  /log.txt    → {stack.lookup('log.txt')}       # 上下都有 → 上层胜")
    print(f"  /missing    → {stack.lookup('missing')}       # 都没")

    # 演示 whiteout:在 upper 创建 whiteout,模拟"rm"
    print()
    print("# 演示 whiteout 屏蔽:")
    create_whiteout(str(upper), "release")
    print(f"  rm /release → 现在查找: {stack.lookup('release')}     # lower[1] 被屏蔽")

    print()
    print("# 实际 mount overlay(本 demo 不真 mount,仅展示 syscall 调用结果):")
    merged = base / "merged"
    merged.mkdir(exist_ok=True)
    if not sys.platform.startswith("linux"):
        print("  skip mount demo(非 Linux 平台);Linux 行为见 README §运行方式")
    else:
        err = mount_overlay(str(layers[1]), str(upper), str(work),
                            str(merged))
        err_map = {1: "EPERM(需 root / CAP_SYS_ADMIN)",
                   22: "EINVAL(work 与 upper 不同 fs)",
                   19: "ENODEV(内核无 overlay 模块)"}
        print(f"  mount(2) 返回 errno = {err} → {err_map.get(err, 'OK')}")


def main():
    cmds = {"inspect": cmd_inspect, "demo-layers": cmd_demo_layers}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print("usage: overlay_demo.py {inspect|demo-layers}")
        sys.exit(1)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
