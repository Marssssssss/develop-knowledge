#!/usr/bin/env python3
"""SO_REUSEADDR / SO_REUSEPORT 与 TIME_WAIT 演示。

实验目标:
  1) 不开 SO_REUSEADDR → 服务端主动 close 后立刻重启 → EADDRINUSE
  2) 开 SO_REUSEADDR   → 同样实验 → 成功
  3) 开 SO_REUSEPORT   → 同进程内先后 bind 两次,演示内核如何哈希分连接

通过 popen 调 ss 查 TIME_WAIT 计数,直观感受状态变化。
"""
import os
import socket
import subprocess
import sys
import time

PORT = 9090


def make_listen(port: int, reuse_addr: bool, reuse_port: bool) -> socket.socket:
    fd = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if reuse_addr:
        fd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if reuse_port and hasattr(socket, "SO_REUSEPORT"):
        fd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    fd.bind(("0.0.0.0", port))
    fd.listen(16)
    return fd


def dump_time_wait(port: int) -> int:
    """返回当前本机 9090 上的 TIME_WAIT 计数(粗略)。"""
    try:
        out = subprocess.check_output(
            ["ss", "-tan", "state", "time-wait", f"sport", "=", f":{port}"],
            text=True, stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return -1
    lines = [l for l in out.splitlines() if l]
    return max(0, len(lines) - 1)  # 减去 header


def step(label: str) -> None:
    print(f"\n--- {label} ---", flush=True)


def demo_reuseaddr() -> None:
    step("实验 A:不开 SO_REUSEADDR → 立即重启应失败")
    s = make_listen(PORT, reuse_addr=False, reuse_port=False)
    print(f"[A1] listening (no REUSEADDR); TIME_WAIT now = {dump_time_wait(PORT)}")
    s.close()
    print(f"[A2] closed; TIME_WAIT now = {dump_time_wait(PORT)} (等待 0s 立即重 bind)")
    try:
        s2 = make_listen(PORT, reuse_addr=False, reuse_port=False)
        print(f"[A3] !!! bind 成功了??? REUSEADDR 缺省行为可能与预期不同")
        s2.close()
    except OSError as e:
        print(f"[A3] bind 失败(符合预期): {e}")

    step("实验 B:开 SO_REUSEADDR → 立即重启应成功")
    s = make_listen(PORT, reuse_addr=True, reuse_port=False)
    print(f"[B1] listening (REUSEADDR=1)")
    s.close()
    print(f"[B2] closed; TIME_WAIT now = {dump_time_wait(PORT)} (等待 0s 立即重 bind)")
    try:
        s2 = make_listen(PORT, reuse_addr=True, reuse_port=False)
        print(f"[B3] bind 成功(符合预期)")
        s2.close()
    except OSError as e:
        print(f"[B3] bind 失败(异常): {e}")

    step("实验 C:SO_REUSEPORT 同端口两组 socket(在子进程中)")
    if not hasattr(socket, "SO_REUSEPORT"):
        print("[C] 平台不支持 SO_REUSEPORT,跳过")
        return
    # 父进程一份,子进程一份,都监听 9091
    p = os.fork()
    port_c = PORT + 1
    if p == 0:
        # child:也 bind
        s = make_listen(port_c, reuse_addr=True, reuse_port=True)
        print(f"[C-child] listening on :{port_c}")
        time.sleep(0.2)
        s.close()
        os._exit(0)
    else:
        s = make_listen(port_c, reuse_addr=True, reuse_port=True)
        print(f"[C-parent] listening on :{port_c}")
        # 等 child 启动后再 accept 几个连接,看是否落到本进程
        for i in range(5):
            try:
                conn, peer = s.accept()
            except OSError:
                break
            print(f"[C-parent] accept #{i}: {peer}")
            conn.close()
        s.close()
        os.waitpid(p, 0)


def explain() -> None:
    print("""
=== SO_REUSEADDR vs SO_REUSEPORT vs TIME_WAIT ===

TIME_WAIT 状态:
  主动关闭方在发 FIN 并收到 ACK 后进入;持续 2*MSL(Linux 默认 60s,TCP_TIMEWAIT_LEN 写死)。
  目的:
    a) 让旧 FIN/ACK 重传完成,避免后续连接被旧报文混淆
    b) 让旧五元组在网络中彻底过期

SO_REUSEADDR (POSIX):
  允许 bind 一个处于 TIME_WAIT 的地址;
  Linux 上 '已被旧 socket 绑的端口' 也需 SO_REUSEADDR 才能被覆盖;
  BSD 仅新 socket 需要。

SO_REUSEPORT (Linux 3.9+):
  允许多个 socket 同时 bind 完全相同的 (addr, port);
  内核按 (srcip, srcport, dstip, dstport) 四元组 hash 分发新连接;
  用于多进程负载分担(典型:Nginx worker、Linux 4.6+ RSS 哈希)。
""")


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "explain":
        explain()
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] == "demo":
        demo_reuseaddr()
        return 0
    print(f"usage: {sys.argv[0]} demo | explain", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())