"""
kqueue_echo: BSD/macOS echo server using Python's selectors module.
Python transparently selects the best implementation on the current platform:
    KqueueSelector  (macOS / FreeBSD)
    EpollSelector   (Linux)
    DevpollSelector (Solaris)
    PollSelector    (all Unix)
    SelectSelector  (Windows fallback)

This file works on Linux/macOS the same; the BSD-only kqueue flavour
is exercised on macOS where the implementation is KqueueSelector.
    python3 kqueue_echo.py 9000
"""
import selectors
import socket
import sys
import types

sel = selectors.DefaultSelector()
conns = {}  # fd -> (sock, accumulator_buffer)


def set_nonblocking(sock):
    sock.setblocking(False)


def accept_handler(key: selectors.SelectorKey, mask):
    """listen socket ready -> accept loop until EAGAIN."""
    listen_sock = key.fileobj
    while True:
        try:
            conn, addr = listen_sock.accept()
        except BlockingIOError:
            return  # no more pending connections
        set_nonblocking(conn)
        conns[conn.fileno()] = (conn, bytearray())
        sel.register(conn, selectors.EVENT_READ, data=conn.fileno())


def read_handler(key: selectors.SelectorKey, mask):
    """client socket ready -> recv loop until empty."""
    fd = key.fileno()
    sock, buf = conns[fd]
    while True:
        try:
            chunk = sock.recv(4096)
        except BlockingIOError:
            return  # no more data right now
        if not chunk:
            sel.unregister(sock)
            sock.close()
            del conns[fd]
            return
        buf.extend(chunk)
        sent = sock.send(bytes(buf))
        del buf[:sent]


def main(port: int = 9000) -> None:
    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_sock.bind(("0.0.0.0", port))
    listen_sock.listen(16)
    set_nonblocking(listen_sock)
    sel.register(listen_sock, selectors.EVENT_READ, data="listen")
    backend = type(sel).__name__
    print(f"kqueue_echo listening on :{port} (backend={backend})")
    try:
        while True:
            events = sel.select(timeout=1.0)
            for key, mask in events:
                if key.data == "listen":
                    accept_handler(key, mask)
                else:
                    read_handler(key, mask)
    except KeyboardInterrupt:
        print("shutting down")
    finally:
        sel.close()
        listen_sock.close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    main(p)
