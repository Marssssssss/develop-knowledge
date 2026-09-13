"""
sendfile_server: Python zero-copy file -> socket using os.sendfile().

Python 3.3+ exposes Linux's sendfile(2) via os.sendfile(out_fd, in_fd, offset, count).
On platforms without sendfile (Windows), it falls back to plain loop send.

For HTTP-like headers preceding the file, prepend via os.writev() on Linux
(os.pwritev with offset=0 trick is awkward; we use socket.send() instead).

   python3 sendfile_server.py 9000 1024
"""
import os
import socket
import sys
import struct
import selectors

FILE_PATH = "/tmp/sendfile_demo.bin"
HAS_SENDFILE = hasattr(os, "sendfile")


def build_demo_file(kib: int) -> int:
    """Materialise a deterministic N-KiB file."""
    with open(FILE_PATH, "wb") as f:
        chunk = bytes(range(256)) * 16  # 4096 B repeating pattern
        for _ in range(kib * 1024 // 4096):
            f.write(chunk)
        rest = (kib * 1024) % 4096
        if rest:
            f.write(chunk[:rest])
    return os.path.getsize(FILE_PATH)


def serve_file_via_sendfile(client: socket.socket, file_fd: int,
                            file_size: int) -> int:
    """Zero-copy transfer of `file_size` bytes from `file_fd` to `client`."""
    header = (f"X-Source: sendfile\r\nContent-Length: {file_size}\r\n\r\n"
              ).encode("ascii")
    client.sendall(header)
    sent = 0
    offset = 0
    while sent < file_size:
        if HAS_SENDFILE:
            n = os.sendfile(client.fileno(), file_fd, offset,
                            file_size - sent)
        else:
            # Fallback: read+write loop with large buffer
            os.lseek(file_fd, offset, os.SEEK_SET)
            buf = os.read(file_fd, min(65536, file_size - sent))
            if not buf:
                break
            client.sendall(buf)
            n = len(buf)
        sent += n
        offset += n
    return sent


def main(port: int = 9000, kib: int = 1024) -> None:
    file_size = build_demo_file(kib)
    file_fd = os.open(FILE_PATH, os.O_RDONLY)
    try:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        s.listen(16)
        s.setblocking(False)
        sel = selectors.DefaultSelector()
        sel.register(s, selectors.EVENT_READ, data=("listen",))
        print(f"sendfile serving {FILE_PATH} ({file_size} B) on :{port} "
              f"({HAS_SENDFILE=})")
        try:
            while True:
                for key, _ in sel.select(timeout=1.0):
                    if key.data[0] == "listen":
                        c, _ = s.accept()
                        c.setblocking(False)
                        sel.register(c, selectors.EVENT_READ, data=("conn", c))
                        continue
                    c: socket.socket = key.data[1]
                    sel.unregister(c)
                    try:
                        # drain whatever client sent
                        try:
                            c.recv(4096)
                        except BlockingIOError:
                            pass
                        serve_file_via_sendfile(c, file_fd, file_size)
                    finally:
                        c.close()
        finally:
            sel.close()
            s.close()
    finally:
        os.close(file_fd)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    kib = int(sys.argv[2]) if len(sys.argv) > 2 else 1024
    main(port, kib)
