"""Cgroups v2 资源限制最小演示 —— Linux 内核 cgroup-v2 文档实战(Python 版).

三个子 demo(python3 cgroup_demo.py <org|mem|cpu>,均需 root + cgroup v2):
  org : 创建子 cgroup、启用控制器、自迁移进程、观察 memory.current
  mem : 设 memory.max 后子进程超限分配 -> OOM kill(memory.events.oom_kill)
  cpu : 设 cpu.max 配额后忙循环 -> 带宽限流(cpu.stat.nr_throttled)

cgroup v2 是纯文本文件接口(docs.kernel.org admin-guide/cgroup-v2):
  cgroup.controllers     可用控制器列表
  cgroup.subtree_control 控制器启用开关(写 "+cpu +memory")
  cgroup.procs           成员进程 PID(写 PID 即迁移)
  memory.current / max   当前用量 / 硬上限(字节)
  cpu.max                "$MAX $PERIOD" 带宽配额(微秒)
  cpu.stat               usage_usec / nr_periods / nr_throttled ...
"""
import os
import sys
import time

CGROOT = "/sys/fs/cgroup"
CGDEMO = f"{CGROOT}/cgroup-demo"


def die(msg: str) -> None:
    raise RuntimeError(msg)


def read_file(path: str) -> str:
    with open(path) as f:
        return f.read().strip()


def write_file(path: str, content: str) -> None:
    with open(path, "w") as f:
        f.write(content)


def ensure_controllers(needed: str) -> None:
    """确保 root 的 subtree_control 启用所需控制器(缺才补,不回收)。

    注意"内部进程约束":非根 cgroup 若含成员进程,则不能再在其
    subtree_control 启用 domain 控制器;root 豁免。
    """
    cur = read_file(f"{CGROOT}/cgroup.subtree_control")
    add = [t for t in needed.split() if t[1:] not in cur.split()]
    if add:
        write_file(f"{CGROOT}/cgroup.subtree_control", " ".join(add))
        print(f"  已在 root 启用控制器: {' '.join(add)}")


def cleanup() -> None:
    """迁回 root 并删除演示组(有进程的组不可 rmdir)。"""
    write_file(f"{CGROOT}/cgroup.procs", str(os.getpid()))
    os.rmdir(CGDEMO)
    print("  已清理 cgroup-demo")


def demo_org() -> None:
    """组织进程: 建组 / 自迁移 / 观察内存计量."""
    print("== cgroup 组织与迁移演示 ==")
    os.mkdir(CGDEMO, 0o755)
    ensure_controllers("+memory +cpu")

    print(f"  迁移前 memory.current = {read_file(CGDEMO + '/memory.current')} B(空组)")

    # 自迁移:写自己的 PID 到子 cgroup 的 cgroup.procs
    write_file(f"{CGDEMO}/cgroup.procs", str(os.getpid()))
    # v2 单一层级:/proc/self/cgroup 显示 "0::$PATH"
    print(f"  /proc/self/cgroup = {read_file('/proc/self/cgroup')}")

    # 分配并逐页触碰 64MB(触碰才计入 memory.current 的 anonymous memory)
    buf = bytearray(64 << 20)
    for i in range(0, len(buf), 4096):
        buf[i] = 1
    print(f"  触碰 64MB 后 memory.current = {read_file(CGDEMO + '/memory.current')} B")

    cleanup()


def demo_mem() -> None:
    """memory.max 硬上限: 超限 -> 回收失败 -> OOM kill."""
    print("== memory.max 资源限制演示 ==")
    os.mkdir(CGDEMO, 0o755)
    ensure_controllers("+memory")
    write_file(f"{CGDEMO}/cgroup.procs", str(os.getpid()))

    # 硬上限 16MiB;子进程试图触碰 512MB
    write_file(f"{CGDEMO}/memory.max", "16777216")

    pid = os.fork()
    if pid == 0:  # 子进程:超限分配,直到被 OOM killer SIGKILL
        try:
            buf = bytearray(512 << 20)
            for i in range(0, len(buf), 4096):
                buf[i] = 1
        except MemoryError:  # 通常等不到 Python 异常,内核先 SIGKILL
            os._exit(2)
        os._exit(0)  # 正常情况下到不了这里

    _, status = os.waitpid(pid, 0)
    killed = os.WIFSIGNALED(status)
    print(f"  子进程被 OOM kill: {'是' if killed else '否'}"
          f"(signal={os.WTERMSIG(status) if killed else 0}, SIGKILL=9)")
    # oom_kill 计数应 >= 1
    print("  memory.events:")
    for line in read_file(f"{CGDEMO}/memory.events").splitlines():
        print(f"    {line}")

    write_file(f"{CGDEMO}/memory.max", "max")
    cleanup()


def demo_cpu() -> None:
    """cpu.max 带宽配额: 每 100ms 最多 50ms -> 忙循环被限流."""
    print("== cpu.max 带宽配额演示 ==")
    os.mkdir(CGDEMO, 0o755)
    ensure_controllers("+cpu")
    write_file(f"{CGDEMO}/cgroup.procs", str(os.getpid()))

    # "50000 100000" = 每 100ms 周期最多 50ms CPU(50% 单核)
    write_file(f"{CGDEMO}/cpu.max", "50000 100000")

    pid = os.fork()
    if pid == 0:  # 子进程:满速忙循环 ~1.5s
        t0 = time.monotonic()
        x = 0
        while time.monotonic() - t0 < 1.5:
            x = (x * 1664525 + 1013904223) & 0xFFFFFFFF
        os._exit(0)

    os.waitpid(pid, 0)
    # usage_usec 约为墙钟一半;nr_throttled > 0 表示发生过限流
    print("  cpu.stat 关键字段:")
    for line in read_file(f"{CGDEMO}/cpu.stat").splitlines():
        key = line.split()[0]
        if key in ("usage_usec", "nr_periods", "nr_throttled", "throttled_usec"):
            print(f"    {line}")

    write_file(f"{CGDEMO}/cpu.max", "max 100000")
    cleanup()


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("org", "mem", "cpu"):
        print(__doc__)
        sys.exit(1)
    if not os.path.exists(f"{CGROOT}/cgroup.controllers"):
        print(f"未检测到 cgroup v2({CGROOT}/cgroup.controllers 不存在)")
        sys.exit(1)
    if os.geteuid() != 0:
        print("需要 root(写 cgroupfs)")
        sys.exit(1)
    {"org": demo_org, "mem": demo_mem, "cpu": demo_cpu}[sys.argv[1]]()


if __name__ == "__main__":
    main()
