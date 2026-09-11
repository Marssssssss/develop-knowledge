"""Namespace 隔离最小演示 —— Linux namespaces(7) / unshare(2) 实战.

三个子 demo(python3 ns_demo.py <uts|user|pid>):
  uts  : fork 先行,子进程 unshare(CLONE_NEWUTS) 后改主机名,父子互不可见
  user : unshare(CLONE_NEWUSER) 无需特权,写 uid_map/gid_map/setgroups
         后获得新 ns 内全套 capabilities,再无特权创建 UTS namespace
  pid  : unshare(CLONE_NEWPID|CLONE_NEWNS) 只影响后续子进程,
         子进程在新 PID namespace 中 PID=1,并挂载新 procfs

Python 标准库没有 unshare(2)/sethostname(2) 封装,用 ctypes 直接调 libc。
常量值来自 linux/sched.h(user_namespaces(7) 各 man 页同源):
  CLONE_NEWUTS=0x04000000  CLONE_NEWUSER=0x10000000
  CLONE_NEWPID=0x20000000  CLONE_NEWNS=0x00020000
"""
import ctypes
import ctypes.util
import os
import socket
import sys

libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

CLONE_NEWNS = 0x00020000
CLONE_NEWUTS = 0x04000000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000


def die(msg: str) -> None:
    errno = ctypes.get_errno()
    raise OSError(errno, f"{msg}: {os.strerror(errno)}")


def sys_unshare(flags: int) -> None:
    """unshare(2): 把调用进程移入新 namespace(PID ns 除外,只影响子进程)."""
    if libc.unshare(ctypes.c_int(flags)) != 0:
        die(f"unshare({flags:#x})")


def sys_sethostname(name: str) -> None:
    """sethostname(2): 修改当前 UTS namespace 的主机名."""
    b = name.encode()
    if libc.sethostname(b, len(b)) != 0:
        die("sethostname")


def print_hostname(who: str) -> None:
    print(f"  [{who}] hostname = {socket.gethostname()}")


def print_ns_link(name: str) -> None:
    """打印 /proc/self/ns 句柄的 inode,观察 namespace 是否切换."""
    target = os.readlink(f"/proc/self/ns/{name}")
    print(f"  {name:<4} ns handle: {target}")


def demo_uts() -> None:
    """UTS namespace: 主机名隔离."""
    print("== UTS namespace 演示 ==")
    print_hostname("父(初始)")

    pid = os.fork()
    if pid == 0:  # 子进程
        print_ns_link("uts")
        try:
            sys_unshare(CLONE_NEWUTS)
        except OSError as e:
            print(f"  [子] 需要 root 或先建 user namespace: {e}")
            os._exit(1)
        print_ns_link("uts")  # inode 变化 => 新 UTS ns
        sys_sethostname("container-ns")
        print_hostname("子(新 UTS ns)")
        os._exit(0)

    os.waitpid(pid, 0)
    print_hostname("父(旧 UTS ns)")
    print("  => 父子互不可见对方的修改")


def write_map(file: str, content: str) -> None:
    """写 /proc/self/{uid_map,gid_map,setgroups},格式见 user_namespaces(7)."""
    with open(f"/proc/self/{file}", "w") as f:
        f.write(content)


def demo_user() -> None:
    """user namespace: 无特权获得全套 capabilities."""
    print("== user namespace 演示(无需 root) ==")
    uid, gid = os.getuid(), os.getgid()
    print(f"  真实身份: uid={uid} gid={gid}")

    # 唯一不需要 CAP_SYS_ADMIN 的 namespace;要求进程单线程
    sys_unshare(CLONE_NEWUSER)

    # Linux 3.19 起无特权进程必须先 setgroups=deny 再写 gid_map
    write_map("setgroups", "deny")
    write_map("uid_map", f"0 {uid} 1\n")  # 新 ns uid 0 <-> 真实 uid
    write_map("gid_map", f"0 {gid} 1\n")
    print(f"  映射后身份: uid={os.getuid()} gid={os.getgid()} (新 ns 内解释为 0)")

    # CapEff 是内核视角的有效能力位图;新 user ns 内应为全 1
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("CapEff:"):
                print(f"  {line.strip()}")
                break

    # 新 user ns 内有全套 capabilities => 可继续无特权创建其他 namespace
    sys_unshare(CLONE_NEWUTS)
    print("  无特权创建 UTS namespace 成功(依赖新 user ns 的 capabilities)")


def demo_pid() -> None:
    """PID namespace: 子进程成为 PID 1 + 挂载新 procfs."""
    print("== PID namespace 演示 ==")
    print(f"  [父] unshare 前 getpid() = {os.getpid()}")

    # 只让后续子进程进入新 PID ns;同时建 mount ns 以挂新 procfs
    sys_unshare(CLONE_NEWPID | CLONE_NEWNS)

    pid = os.fork()
    if pid == 0:  # 新 PID ns 内的第一个进程
        print(f"  [子] getpid() = {os.getpid()} (新 ns 内 PID 1)")
        print(f"  [子] getppid() = {os.getppid()} (ns 外父进程不可见)")
        # 挂载新 procfs:只显示本 ns 内进程(pid_namespaces(7))
        # MS_REC|MS_PRIVATE 断开共享传播,避免泄漏到宿主
        libc.mount(None, b"/", None, 4096 | (1 << 18), None)  # MS_REC|MS_PRIVATE
        if libc.mount(b"proc", b"/proc", b"proc", 0, None) != 0:
            die("mount proc")
        print("  [子] 新 /proc 下可见进程:")
        for name in sorted(os.listdir("/proc")):
            if name.isdigit():
                print(f"    pid={name}")
        os._exit(0)

    _, status = os.waitpid(pid, 0)
    print(f"  [父] getpid() = {os.getpid()} (仍在旧 ns,值不变)")
    print("  => PID namespace 只对子进程生效,单向不可回退")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in ("uts", "user", "pid"):
        print(__doc__)
        sys.exit(1)
    {"uts": demo_uts, "user": demo_user, "pid": demo_pid}[sys.argv[1]]()


if __name__ == "__main__":
    main()
