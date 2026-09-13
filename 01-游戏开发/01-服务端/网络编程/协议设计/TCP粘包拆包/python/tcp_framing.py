"""
tcp_framing: TLV / length-prefix streaming parser for TCP.

   Frame:  ┌──────────┬──────────────┬──────────────┐
           │ Magic 4B │ Length 4B BE │ Payload:Len  │
           │ b"GAME"  │   u32 BE     │  variable    │
           └──────────┴──────────────┴──────────────┘

A single bytes-buffer per connection absorbs 粘包 (multiple frames in
one recv) and 拆包 (one frame straddling several recvs).
    python3 tcp_framing.py 9000
"""
import socket
import struct
import selectors

MAGIC = b"GAME"
HDR_LEN = 8
MAX_FRAME = 1 << 20          # 1 MiB sanity cap


def write_frame(sock: socket.socket, payload: bytes) -> None:
    """Length-prefixed write, atomic via sendmsg-of-single-fragment."""
    hdr = struct.pack("!4sI", MAGIC, len(payload))
    sock.sendall(hdr + payload)


class FrameParser:
    """Streaming parser: feed bytes, pull out complete bodies."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.state = 0       # 0=header, 1=body
        self.body_len = 0

    def feed(self, data: bytes) -> None:
        """Append raw bytes, advance parser state."""
        self.buf.extend(data)
        # Cap the buffer to prevent unbounded growth from misbehaving clients
        if len(self.buf) > MAX_FRAME * 2:
            self.buf.clear(); self.state = 0; self.body_len = 0
            return
        while True:
            if self.state == 0:
                if len(self.buf) < HDR_LEN:
                    return
                magic = bytes(self.buf[:4])
                if magic != MAGIC:
                    # resync: drop 1 byte (cheap loss-of-sync recovery)
                    del self.buf[0]
                    continue
                self.body_len = struct.unpack("!I", bytes(self.buf[4:8]))[0]
                del self.buf[:HDR_LEN]
                if self.body_len == 0 or self.body_len > MAX_FRAME:
                    self.buf.clear(); self.state = 0; self.body_len = 0
                    continue
                self.state = 1
            if self.state == 1:
                if len(self.buf) < self.body_len:
                    return
                # complete; let consume() pop it
                return

    def take(self) -> bytes | None:
        """Pop the next complete body if available, else None."""
        if self.state != 1 or len(self.buf) < self.body_len:
            return None
        body = bytes(self.buf[:self.body_len])
        del self.buf[:self.body_len]
        self.state = 0
        self.body_len = 0
        return body


class Server:
    def __init__(self, port: int) -> None:
        self.sel = selectors.DefaultSelector()
        self.listen = socket.socket()
        self.listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listen.bind(("0.0.0.0", port))
        self.listen.listen(16)
        self.listen.setblocking(False)
        self.sel.register(self.listen, selectors.EVENT_READ, data=("listen", None))
        self.conns: dict[int, FrameParser] = {}

    def accept_loop(self) -> None:
        while True:
            try:
                c, _ = self.listen.accept()
            except BlockingIOError:
                return
            c.setblocking(False)
            self.conns[c.fileno()] = FrameParser()
            self.sel.register(c, selectors.EVENT_READ, data=("conn", c))

    def serve(self) -> None:
        print(f"tcp_framing listening on :{self.listen.getsockname()[1]}")
        while True:
            for key, _ in self.sel.select(timeout=1.0):
                if key.data[0] == "listen":
                    self.accept_loop()
                    continue
                c: socket.socket = key.data[1]
                parser = self.conns[c.fileno()]
                try:
                    chunk = c.recv(65536)
                except ConnectionResetError:
                    self.close_conn(c); continue
                if not chunk:
                    self.close_conn(c); continue
                parser.feed(chunk)
                while True:
                    body = parser.take()
                    if body is None:
                        break
                    print(f"fd={c.fileno()} frame len={len(body)} {body!r}")
                    write_frame(c, body)

    def close_conn(self, c: socket.socket) -> None:
        self.sel.unregister(c)
        c.close()
        self.conns.pop(c.fileno(), None)


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    Server(port).serve()
