# -*- coding: utf-8 -*-
"""非阻塞 IO 与 EAGAIN 语义 —— 模型层（Linux）

原文依据（均已实读 man7 页面）：
  * read(2):   ``EAGAIN The file descriptor fd refers to a file other than a
               socket and has been marked nonblocking (O_NONBLOCK), and the
               read would block.``
               ``EAGAIN or EWOULDBLOCK  The file descriptor fd refers to a
               socket and has been marked nonblocking ...``
  * recv(2):   ``The receive calls normally return any data available, up to
               the requested amount, rather than waiting for receipt of the
               full amount requested.``
  * recv(2):   ``MSG_DONTWAIT ... Enables nonblocking operation; if the
               operation would block, the call fails with EAGAIN or
               EWOULDBLOCK.``
  * socket(7): ``It is possible to do nonblocking I/O on sockets by setting
               the O_NONBLOCK flag on a socket file descriptor using
               fcntl(2). Then all operations that would block will (usually)
               return with EAGAIN (operation should be retried later);
               connect(2) ...``
"""
from collections import deque

# ---- Linux/x86-64 errno ----------------------------------------------------
EAGAIN = 11
EWOULDBLOCK = 11      # Linux 上二者同值；POSIX 允许它们不同
EINPROGRESS = 115
EALREADY = 114
EISCONN = 106
ECONNRESET = 104

O_NONBLOCK = 0x800    # fcntl(F_SETFL) 标志位
MSG_DONTWAIT = 0x40   # 单次 recv/send 的按调用覆盖位
SOCK_NONBLOCK = O_NONBLOCK


class R:
    """系统调用返回值：要么 ok 带值，要么 err 带 errno。

    io_uring 之外的 Linux 传统接口一律「返回值 + errno」两通道报错，
    所以这里刻意保留 errno 而不抛异常。
    """

    __slots__ = ("ok", "val", "errno")

    def __init__(self, ok, val=0, errno=0):
        self.ok = ok
        self.val = val
        self.errno = errno

    def __repr__(self):
        return "ok(%r)" % (self.val,) if self.ok else "err(E%d)" % self.errno


def Ok(v=0):
    return R(True, val=v)


def Err(e):
    return R(False, errno=e)


class Sock:
    """一个被建模的 socket 文件描述符。"""

    def __init__(self, nonblock=False, kind="socket", snd_room=4096):
        self.fl = O_NONBLOCK if nonblock else 0
        self.kind = kind                 # "socket" | "pipe"（read(2) 措辞不同）
        self.rcv = bytearray()           # 已到达内核、未被应用读走
        self.incoming = deque()          # 「网络侧」待注入的后续数据
        self.snd_room = snd_room         # 发送缓冲剩余空间
        self.backlog = deque()           # 已完成握手、待 accept
        self.state = "CLOSED"
        self.so_error = 0

    @property
    def nonblock(self):
        return bool(self.fl & O_NONBLOCK)


class Net:
    """模拟「内核 + 应用」边界，统计系统调用次数与阻塞等待次数。"""

    def __init__(self):
        self.syscalls = 0
        self.waits = 0                   # 有多少次调用真的把线程挂起了
        self.drain_per_wait = 1500       # 每次阻塞等待后对端 ACK 腾出的空间

    def _enter(self):
        self.syscalls += 1

    def arrive(self, s, data):
        """网卡 + 内核下半部把数据放进接收缓冲（不占应用线程，不算阻塞等待）。"""
        s.rcv += data

    def _block(self, s):
        """阻塞等待：把「网络侧」已排队的数据/连接搬进内核缓冲。"""
        self.waits += 1
        while s.incoming:
            s.rcv += s.incoming.popleft()
        if s.state == "SYN_SENT":
            s.state = "ESTABLISHED"
        if s.snd_room == 0:
            s.snd_room = min(self.drain_per_wait, 4096)


def _nonblocking_now(s, flags):
    """fd 上的 O_NONBLOCK 与本次调用的 MSG_DONTWAIT 任一置位即为非阻塞。"""
    return s.nonblock or bool(flags & MSG_DONTWAIT)


# ---- 读 ---------------------------------------------------------------------
def recv(net, s, n, flags=0):
    net._enter()
    if not s.rcv:
        if _nonblocking_now(s, flags):
            return Err(EAGAIN)
        net._block(s)                    # 阻塞：挂起直到有数据
        if not s.rcv:
            return Err(EAGAIN)
    data = bytes(s.rcv[:n])              # 短读：有多少给多少，绝不等满 n
    del s.rcv[:n]
    return Ok(data)


def read(net, s, n):
    """read(2) 版本：语义同 recv，但报错措辞按 fd 类型区分（见模块 docstring）。"""
    net._enter()
    if not s.rcv:
        if s.nonblock:
            return Err(EAGAIN)
        net._block(s)
        if not s.rcv:
            return Err(EAGAIN)
    data = bytes(s.rcv[:n])
    del s.rcv[:n]
    return Ok(data)


def errno_wording(s):
    """复现 man page 的措辞差异：非 socket 只写 EAGAIN，socket 写 EAGAIN/EWOULDBLOCK。"""
    return "EAGAIN or EWOULDBLOCK" if s.kind == "socket" else "EAGAIN"


# ---- 写 ---------------------------------------------------------------------
def send(net, s, data, flags=0):
    net._enter()
    if s.snd_room == 0:
        if _nonblocking_now(s, flags):
            return Err(EAGAIN)
        net._block(s)
        if s.snd_room == 0:
            return Err(EAGAIN)
    k = min(len(data), s.snd_room)       # 部分写：能收多少算多少
    s.snd_room -= k
    return Ok(k)


# ---- 连接管理 ----------------------------------------------------------------
def listen(s):
    s.state = "LISTEN"
    return Ok(0)


def accept(net, s):
    net._enter()
    if not s.backlog:
        if s.nonblock:
            return Err(EAGAIN)
        net._block(s)
        if not s.backlog:
            return Err(EAGAIN)
    return Ok(s.backlog.popleft())


def connect(net, s, instant=False):
    """非阻塞 connect 不阻塞在三次握手上，而是立刻返回 EINPROGRESS。"""
    net._enter()
    if s.state == "ESTABLISHED":
        return Err(EISCONN)
    if s.state == "SYN_SENT":
        return Err(EALREADY)
    if s.nonblock and not instant:
        s.state = "SYN_SENT"
        return Err(EINPROGRESS)
    s.state = "ESTABLISHED"
    return Ok(0)


def getsockopt_so_error(net, s):
    """非阻塞 connect 完成后，成败只能靠 SO_ERROR 取（返回值永远是 0/-1）。"""
    net._enter()
    return Ok(s.so_error)


# ---- 两种事件循环：忙轮询 vs 就绪通知 ------------------------------------------
def busy_poll_recv(net, s, want_bytes, arrivals, max_spin=10000):
    """只有 O_NONBLOCK、没有 epoll 的写法：不断重试直到读到数据。

    `arrivals` 是「每次空转后网卡恰好到一块数据」的剧本，用来量化空转次数。
    """
    it = iter(arrivals)
    spins = 0
    got = bytearray()
    while len(got) < want_bytes and spins < max_spin:
        r = recv(net, s, want_bytes - len(got))
        spins += 1
        if r.ok:
            got += r.val
        elif r.errno == EAGAIN and it is not None:
            try:
                net.arrive(s, next(it))
            except StopIteration:
                break
    return bytes(got), spins


def epoll_then_recv(net, s, want_bytes, arrivals):
    """epoll 写法：先把全部数据收进内核（1 次 epoll_wait 挂起），再一次性读。"""
    for chunk in arrivals:
        net.arrive(s, chunk)
    net._block(s)                        # epoll_wait 只挂起一次
    r = recv(net, s, want_bytes)
    agains = 0 if r.ok else 1
    return (r.val if r.ok else b""), agains
