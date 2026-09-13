#!/usr/bin/env python3
"""TCP Keepalive 演示 — 启用 SO_KEEPALIVE + 自定义三参数,验证对端挂起时 recv 返回 ETIMEDOUT。

Linux 默认: TCP_KEEPIDLE = 7200s (2h), TCP_KEEPINTVL = 75s, TCP_KEEPCNT = 9
→ 总探测时间 ≈ 2h + 9×75s ≈ 2h11m

本 demo 把 idle=3, intvl=2, probes=3 → 总时长 ≈ 3+6=9s,加速验证。
"""
import socket
import struct
import sys
import threading
import time

# Linux 下 IPPROTO_TCP 常量 + 三参数(Windows 上名称是 TCP_KEEPIDLE 等,值一样)
TCP_KEEPIDLE = socket.TCP_KEEPIDLE if hasattr(socket, "TCP_KEEPIDLE") else 4
TCP_KEEPINTVL = socket.TCP_KEEPINTVL if hasattr(socket, "TCP_KEEPINTVL") else 5
TCP_KEEPCNT = socket.TCP_KEEPCNT if hasattr(socket, "TCP_KEEPCNT") else 6


def enable_keepalive(fd: socket.socket, idle: int, intvl: int, cnt: int) -> None:
    """启用 SO_KEEPALIVE 并自定义三参数。"""
    fd.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    fd.setsockopt(socket.IPPROTO_TCP, TCP_KEEPIDLE, idle)
    fd.setsockopt(socket.IPPROTO_TCP, TCP_KEEPINTVL, intvl)
    fd.setsockopt(socket.IPPROTO_TCP, TCP_KEEPCNT, cnt)


def dump_keepalive(fd: socket.socket) -> None:
    on = fd.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)
    idle = fd.getsockopt(socket.IPPROTO_TCP, TCP_KEEPIDLE)
    intvl = fd.getsockopt(socket.IPPROTO_TCP, TCP_KEEPINTVL)
    cnt = fd.getsockopt(socket.IPPROTO_TCP, TCP_KEEPCNT)
    print(f"  SO_KEEPALIVE={on}  TCP_KEEPIDLE={idle}s  TCP_KEEPINTVL={intvl}s  TCP_KEEPCNT={cnt}")


def run_server(port: int) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(8)
    print(f"[server] listening on :{port}", flush=True)
    conn, peer = srv.accept()
    print(f"[server] accepted from {peer}", flush=True)
    print("[server] before keepalive:", flush=True)
    dump_keepalive(conn)

    enable_keepalive(conn, idle=3, intvl=2, cnt=3)
    print(f"[server] after keepalive (idle=3, intvl=2, cnt=3 → ~3+6=9s 探测失败):", flush=True)
    dump_keepalive(conn)

    print("[server] waiting for data (simulate dead peer: kill -STOP the client PID)...",
          flush=True)
    conn.settimeout(20)  # 兜底,避免 keepalive 没生效时永久阻塞
    try:
        while True:
            data = conn.recv(64)
            if not data:
                print("[server] peer closed cleanly (FIN)", flush=True)
                break
            print(f"[server] got {len(data)}B: {data!r}", flush=True)
    except socket.timeout:
        print("[server] recv timeout (no data but peer still alive)", flush=True)
    except OSError as e:
        # keepalive 探测失败 → ECONNRESET / ETIMEDOUT
        print(f"[server] recv error after dead peer detection: {e} (errno={e.errno})",
              flush=True)
    conn.close()
    srv.close()


def run_client(host: str, port: int) -> None:
    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    cli.connect((host, port))
    print("[client] connected; sending hello and sleeping 15s "
          "(server keepalive 9s should detect dead-peer)", flush=True)
    cli.send(b"hello\n")
    # 注释掉 sleep 模拟"client 进程挂起/网线拔掉";真测试时:开启此行 + 另起终端 kill -STOP PID
    time.sleep(15)
    try:
        cli.send(b"after sleep\n")
    except OSError as e:
        print(f"[client] send failed: {e}", flush=True)
    cli.close()


def main() -> int:
    if len(sys.argv) < 3:
        print(f"usage:\n  {sys.argv[0]} server <port>\n  {sys.argv[0]} client <ip> <port>",
              file=sys.stderr)
        return 1
    mode = sys.argv[1]
    if mode == "server":
        run_server(int(sys.argv[2]))
    elif mode == "client" and len(sys.argv) == 4:
        run_client(sys.argv[2], int(sys.argv[3]))
    else:
        print("bad args", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())