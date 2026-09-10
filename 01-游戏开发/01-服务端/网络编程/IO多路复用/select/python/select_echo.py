#!/usr/bin/env python3
"""select_echo.py — Minimal TCP echo server using the selectors module.

The selectors module picks the best available backend:
    - epoll   on Linux
    - kqueue  on macOS / BSD
    - select  on Windows (fallback)

This shows the *idiomatic* Python way; the raw `select` module would require
manual fd_set rebuilding like the C version.

Run:    python3 select_echo.py 9000
Test:   nc localhost 9000
        or: python3 client.py
"""

import selectors
import socket
import sys
import types

sel: selectors.DefaultSelector

def accept(sock: socket.socket, mask: int) -> None:
    """Handle a read-ready listening socket → accept new client."""
    conn, addr = sock.accept()
    print(f"+ client {addr}", flush=True)
    conn.setblocking(False)
    # Each connection registers its own callback via `data`
    sel.register(conn, selectors.EVENT_READ,
                 data=types.SimpleNamespace(addr=addr))

def read(conn: socket.socket, mask: int) -> None:
    """Handle a read-ready client socket → read & echo."""
    try:
        data = conn.recv(4096)
    except ConnectionResetError:
        data = None
    if not data:
        # Peer closed (recv() returns b'') or hard reset → cleanup
        print(f"- client {conn.getpeername()} closed", flush=True)
        sel.unregister(conn)
        conn.close()
        return
    print(f"<- {conn.getpeername()}: {data!r}", flush=True)
    conn.sendall(data)   # echo exactly what we got

def main(port: int) -> None:
    global sel
    sel = selectors.DefaultSelector()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("", port))
    srv.listen(16)
    srv.setblocking(False)
    sel.register(srv, selectors.EVENT_READ, data=None)   # data=None → "this is the listener"

    backend = f"{type(sel).__module__}.{type(sel).__name__}"
    print(f"select_echo listening on :{port} (backend={backend})", flush=True)

    try:
        while True:
            events = sel.select(timeout=1.0)   # 1s timeout → KeyboardInterrupt-friendly
            for key, mask in events:
                callback = accept if key.data is None else read
                callback(key.fileobj, mask)
    except KeyboardInterrupt:
        print("\nbye")
        sel.close()
        srv.close()

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    main(port)