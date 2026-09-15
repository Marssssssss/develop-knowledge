#!/usr/bin/env python3
"""SCM_RIGHTS 的最小可信模型。

本文件只负责"内核语义"，不打印任何东西 —— 打印与断言在 main.py。

核心抽象只有两个（man7 unix(7) 的措辞）：
  * open file description（OFD）—— 内核里真正持有偏移量和文件对象的那一层；
  * file descriptor table —— 每个进程一张，把整数 fd 映射到 OFD 的引用。

`SCM_RIGHTS` 传的是 **对 OFD 的引用**，man7 unix(7) 原话：
    "what is being passed is a reference to an open file description …
     Semantically, this operation is equivalent to duplicating (dup(2)) a
     file descriptor into the file descriptor table of another process."
所以模型里"发送一个 fd" = 目标表里多一个指向**同一个** OFD 的项，
偏移量共享、引用计数 +1 —— 这正是它与"传路径让对端自己 open"的分水岭。
"""

from __future__ import annotations

from cmsg import *  # noqa: F401,F403  ← 常量与 cmsg 尺寸算术

# ---------------------------------------------------------------- OFD / fd 表

class OpenFileDescription:
    """打开文件描述 —— 偏移量、引用计数都在这一层。"""

    _next_oid = 0

    def __init__(self, kind: str, payload: bytes = b"", path: str = ""):
        OpenFileDescription._next_oid += 1
        self.oid = OpenFileDescription._next_oid
        self.kind = kind            # "file" / "pipe-r" / "socket" ...
        self.path = path
        self.data = bytearray(payload)
        self.offset = 0             # ← 共享的关键
        self.refcount = 0
        self.flags = 0              # O_NONBLOCK / FD_CLOEXEC 等记录在此

    def read(self, n: int) -> bytes:
        chunk = bytes(self.data[self.offset:self.offset + n])
        self.offset += len(chunk)
        return chunk

    def write(self, buf: bytes) -> int:
        self.data[self.offset:self.offset + len(buf)] = buf
        self.offset += len(buf)
        return len(buf)

    def __repr__(self) -> str:
        return f"<OFD#{self.oid} {self.kind} refs={self.refcount} off={self.offset}>"


class Entry:
    """fd 表里的一项：一个 OFD 引用 + 该编号自己的 FD_CLOEXEC 标志。"""

    __slots__ = ("ofd", "cloexec")

    def __init__(self, ofd: OpenFileDescription, cloexec: bool = False):
        self.ofd = ofd
        self.cloexec = cloexec


class FdTable:
    """一个进程的 fd 表。limit 就是 RLIMIT_NOFILE 的软限。"""

    RESERVED = (0, 1, 2)         # stdin / stdout / stderr 天生占着

    def __init__(self, name: str, limit: int = 1024):
        self.name = name
        self.limit = limit
        self.entries: dict[int, Entry] = {}
        self.closed_count = 0        # 统计用：被关掉的 fd 数

    # --- 分配编号：内核取"从 0 开始最小的空位"（0/1/2 已被占） --------
    def _alloc_fd(self, start: int = 3) -> int:
        fd = start
        while fd in self.entries:
            fd += 1
        return fd

    def install(self, ofd: OpenFileDescription, cloexec: bool = False) -> int:
        if len(self.entries) >= self.limit:
            raise ScmError(EMFILE, f"{self.name} 已达 RLIMIT_NOFILE={self.limit}")
        fd = self._alloc_fd()
        ofd.refcount += 1
        self.entries[fd] = Entry(ofd, cloexec)
        return fd

    def get(self, fd: int) -> Entry:
        e = self.entries.get(fd)
        if e is None:
            raise ScmError(EBADF, f"{self.name} 没有 fd={fd}")
        return e

    def close(self, fd: int) -> None:
        e = self.entries.pop(fd, None)
        if e is None:
            raise ScmError(EBADF, f"{self.name} 关不存在的 fd={fd}")
        expect = e.ofd.refcount - 1
        e.ofd.refcount = expect
        self.closed_count += 1

    def size(self) -> int:
        return len(self.entries)

    def summary(self) -> str:
        if not self.entries:
            return "（空）"
        return ", ".join(f"{fd}->OFD#{e.ofd.oid}" for fd, e in sorted(self.entries.items()))


# ---------------------------------------------------------------- 消息与 socket

class Message:
    """队列里的一个发送记录（流式 socket 上它是字节流的一段 + 附挂控制数据）。"""

    __slots__ = ("data", "fds", "in_flight_fds")

    def __init__(self, data: bytes, fds: list[OpenFileDescription]):
        self.data = data
        self.fds = fds             # 对 OFD 的"在途引用"
        self.in_flight_fds = len(fds)


class RecvResult:
    __slots__ = ("data", "fds", "msg_flags", "consumed_msgs", "dropped_rlimit", "dropped_trunc")

    def __init__(self, data: bytes, fds: list[int], msg_flags: int,
                 consumed_msgs: int, dropped_rlimit: int, dropped_trunc: int):
        self.data = data
        self.fds = fds
        self.msg_flags = msg_flags
        self.consumed_msgs = consumed_msgs
        self.dropped_rlimit = dropped_rlimit
        self.dropped_trunc = dropped_trunc


class UnixSocket:
    """AF_UNIX socketpair 的一端（模型只关心队列与在途 fd 记账）。"""

    def __init__(self, kind: str = "stream", peer: "UnixSocket | None" = None):
        self.kind = kind                 # "stream" / "dgram"
        self.peer: UnixSocket | None = peer
        self.queue: list[Message] = []
        if peer is not None:
            peer.peer = self

    # ---- 发送侧 -------------------------------------------------------
    def sendmsg(self, sender: FdTable, data: bytes, fds: list[int],
                cap_sys_resource: bool = False) -> int:
        if len(fds) > SCM_MAX_FD:
            raise ScmError(EINVAL, f"一次最多传 {SCM_MAX_FD} 个 fd（收到 {len(fds)}）")
        if self.kind == "stream" and len(data) == 0:
            # man7 unix(7): "At least one byte of real data should be sent when
            # sending ancillary data." —— 流式 socket 上这是硬要求
            raise ScmError(EINVAL, "流式 socket 传 fd 必须附带 ≥1 字节真实数据")

        ofds: list[OpenFileDescription] = []
        for fd in fds:
            ofds.append(sender.get(fd).ofd)   # 无效 fd → EBADF

        # 在途 fd 记账：Linux ≥ 4.5 会诊断，防止"发完就 close"绕过 RLIMIT_NOFILE
        if not cap_sys_resource:
            in_flight = self.in_flight() + len(fds)
            if in_flight > sender.limit:
                raise ScmError(
                    ETOOMANYREFS,
                    f"在途 fd {in_flight} 超过 RLIMIT_NOFILE={sender.limit} 且无 CAP_SYS_RESOURCE")
        for o in ofds:
            o.refcount += 1                   # 队列里的在途引用
        # 数据与控制数据进入**对端**的接收队列
        target = self.peer if self.peer is not None else self
        target.queue.append(Message(data, ofds))
        return len(data)

    def pending_fds(self) -> int:
        """本 socket **接收队列**里挂着、还没被本进程接走的 fd 数。"""
        return sum(m.in_flight_fds for m in self.queue)

    def in_flight(self) -> int:
        """"在途" fd：本方已 sendmsg 发出、但对端还没 recvmsg 接走的数量。

        man7 unix(7) 的 ETOOMANYREFS 说明里定义的就是这个量。
        """
        target = self.peer if self.peer is not None else self
        return target.pending_fds()

    # ---- 接收侧 -------------------------------------------------------
    def recvmsg(self, receiver: FdTable, buf_size: int, ctrl_buf_size: int,
                flags: int = 0) -> RecvResult | None:
        """返回 None 表示队列空（等价于阻塞）。

        与真实内核一致的两条规则：
          * 流式 socket 上，附挂控制数据的记录是**屏障**：一次 recvmsg 先取走
            它前面那些不带控制数据的记录，取到带控制数据的记录即停，不会顺带
            把后面新记录的字节也吞进来（man7 unix(7) 的 4/1/4 字节例子）；
          * 控制缓冲装不下的 fd 在**接收进程**里被自动关闭（不是退回发送方）。
        """
        if not self.queue:
            return None

        got = bytearray()
        anc_fds: list[OpenFileDescription] = []
        consumed = 0
        msg_flags = 0

        while self.queue and len(got) < buf_size:
            m = self.queue[0]
            room = buf_size - len(got)
            take = min(room, len(m.data))
            got += m.data[:take]
            m.data = m.data[take:]
            if len(got) >= buf_size and self.kind == "dgram" and m.data:
                msg_flags |= MSG_TRUNC          # 数据报被截断：剩余部分丢弃
                m.data = b""
            if m.fds:
                anc_fds = m.fds                 # 屏障：本条控制数据随本次返回
                m.fds = []
                consumed += 1
                break
            if m.data:
                break                           # 缓冲区满 → 下次继续
            self.queue.pop(0)                   # 整条读完
            consumed += 1

        return self._finish(receiver, bytes(got), anc_fds, ctrl_buf_size,
                            flags, consumed, msg_flags)

    def _finish(self, receiver: FdTable, data: bytes, anc_fds: list[OpenFileDescription],
                ctrl_buf_size: int, flags: int, consumed: int,
                msg_flags: int = 0) -> RecvResult:
        installed: list[int] = []
        dropped_trunc = 0
        dropped_rlimit = 0

        if anc_fds:
            fit = fds_fit(ctrl_buf_size, len(anc_fds))
            if fit < len(anc_fds):
                # "the excess file descriptors are automatically closed in the
                #  receiving process" —— man7 unix(7)
                msg_flags |= MSG_CTRUNC
                dropped_trunc = len(anc_fds) - fit
            cloexec = bool(flags & MSG_CMSG_CLOEXEC)
            for o in anc_fds:
                o.refcount -= 1                 # 释放队列持有的那份"在途引用"
            for o in anc_fds[:fit]:
                try:
                    installed.append(receiver.install(o, cloexec=cloexec))
                except ScmError:                # RLIMIT_NOFILE 不足 → 同样自动关闭
                    dropped_rlimit += 1
                    # 注意：man7 unix(7) 只说"自动关闭"，**没有**说会置 MSG_CTRUNC，
                    # 这里也不置 —— 想区分只能靠"实际收到的个数 < 发送的个数"。
        return RecvResult(data, installed, msg_flags, consumed, dropped_rlimit, dropped_trunc)


def pipe_pair() -> tuple[UnixSocket, UnixSocket]:
    a = UnixSocket("stream")
    b = UnixSocket("stream", peer=a)
    return a, b
