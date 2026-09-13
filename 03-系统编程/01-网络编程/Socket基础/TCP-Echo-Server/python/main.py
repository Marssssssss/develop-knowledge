#!/usr/bin/env python3
"""TCP Echo Server — socket 标准库最小阻塞实现。
    流程: socket() → bind() → listen() → accept() → recv/send 回环
    运行: python3 main.py 9090   (客户端:nc 127.0.0.1 9090)
    多客户端串行(单 accept loop);每连接 read==0 → FIN 关闭
"""
import socket
import signal
import sys

BUF_SIZE = 4096


def serve(port: int) -> None:
    stop = False

    def on_sigint(_sig, _frm):
        nonlocal stop
        stop = True
        print("\nshutting down...", flush=True)

    signal.signal(signal.SIGINT, on_sigint)
    signal.signal(signal.SIGPIPE, signal.SIG_IGN)  # 客户端关闭后 send 不崩溃

    # 1) socket(AF_INET, SOCK_STREAM)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # 2) bind
    srv.bind(("0.0.0.0", port))

    # 3) listen(backlog = 16)
    srv.listen(16)
    print(f"echo server listening on :{port} (Ctrl-C to stop)", flush=True)

    # 4) accept loop
    srv.settimeout(0.5)  # 让 signal 中断可被周期性检查
    while not stop:
        try:
            conn, peer = srv.accept()
        except socket.timeout:
            continue
        except OSError as e:
            if stop:
                break
            print(f"accept error: {e}", file=sys.stderr)
            break
        print(f"accept {peer[0]}:{peer[1]}", flush=True)

        # 5) recv/send 回环;b'' 表示客户端 FIN
        with conn:
            while not stop:
                try:
                    data = conn.recv(BUF_SIZE)
                except ConnectionResetError:
                    break
                if not data:
                    break
                # 模拟 partial write 处理:用 sendall 自动循环
                conn.sendall(data)
        print(f"close {peer[0]}:{peer[1]}", flush=True)

    srv.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python3 main.py <port>", file=sys.stderr)
        sys.exit(1)
    serve(int(sys.argv[1]))