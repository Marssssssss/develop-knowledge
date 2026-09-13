"""
reactor: single-threaded event-driven game server using selectors.

The reactor is a small framework that decouples:
  - Synchronous Event Demultiplexer (selectors) -> block until fd ready
  - Event Handler API: handle_accept / handle_read / handle_write
  - Reactor core: register/deregister + event loop

This demo builds a tiny "chat server" skeleton: each connection has
a session object holding the half-read buffer; handlers are pluggable.
    python3 reactor.py 9000
"""
import selectors
import socket
import sys
import threading

sel = selectors.DefaultSelector()


# ---- Reactable handler interface ----
class Handler:
    def handle_accept(self, listener): ...
    def handle_read(self, sess): ...
    def handle_close(self, sess): ...


# ---- Default echo handler ----
class EchoHandler(Handler):
    def handle_accept(self, listener):
        s, _ = listener.accept()
        s.setblocking(False)
        sess = Session(s, self)
        sel.register(s, selectors.EVENT_READ, data=sess)

    def handle_read(self, sess):
        data = sess.sock.recv(4096)
        if not data:
            self.handle_close(sess)
            return
        sess.feed(data)              # session implements the receive buffer
        sess.sendall(b"echo:" + data)  # naive full send (no EAGAIN split)

    def handle_close(self, sess):
        sel.unregister(sess.sock)
        sess.sock.close()


class Session:
    def __init__(self, sock: socket.socket, handler: Handler):
        self.sock = sock
        self.handler = handler
        self.buf = bytearray()

    def feed(self, data: bytes):
        # The session receives bytes that may represent one or more
        # application-layer messages; a real reactor would dispatch them
        # here (see demo 119 TCP framing).
        self.buf.extend(data)

    def sendall(self, data: bytes):
        # Non-blocking-ish send: try once; if EAGAIN, the reactor would
        # switch to EPOLLOUT mode. Skipped for brevity.
        self.sock.sendall(data)


# ---- Reactor core ----
class Reactor:
    def __init__(self, sel_impl=selectors.DefaultSelector()):
        self.sel = sel_impl
        self.handler: Handler = EchoHandler()
        self.keep_running = True

    def add_listener(self, port: int):
        l = socket.socket()
        l.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        l.bind(("0.0.0.0", port))
        l.listen(16)
        l.setblocking(False)
        # Tag the listener with a Handler binding so we know to accept
        self.sel.register(l, selectors.EVENT_READ, data=("listener", l))
        return l

    def run(self, port: int = 9000):
        self.add_listener(port)
        print(f"reactor listening on :{port} (sel={type(self.sel).__name__})")
        while self.keep_running:
            for key, mask in self.sel.select(timeout=1.0):
                if key.data[0] == "listener":
                    self.handler.handle_accept(key.data[1])
                else:
                    sess: Session = key.data
                    try:
                        self.handler.handle_read(sess)
                    except ConnectionResetError:
                        self.handler.handle_close(sess)


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 9000
    Reactor().run(p)
