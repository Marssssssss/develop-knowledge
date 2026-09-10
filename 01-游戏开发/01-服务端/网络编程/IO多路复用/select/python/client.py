#!/usr/bin/env python3
"""client.py — Tiny TCP echo client for testing select_echo.

Usage:    python3 client.py [host] [port] [message]
Default:  python3 client.py 127.0.0.1 9000 hello
"""

import socket
import sys

def main() -> None:
    host  = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port  = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    msg   = sys.argv[3].encode() if len(sys.argv) > 3 else b"hello from python client"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((host, port))
        s.sendall(msg)
        # Echo protocol: server returns the same number of bytes
        data = b""
        while len(data) < len(msg):
            chunk = s.recv(len(msg) - len(data))
            if not chunk:
                break
            data += chunk
    print(f"sent     : {msg!r}")
    print(f"received : {data!r}")

if __name__ == "__main__":
    main()